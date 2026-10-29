"""Persistent QQ user registry and administrator actions (no model calls)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from types import SimpleNamespace
from typing import Any

from astrbot.api import logger

if __package__:
    from .case_library import normalize_case_store, serialize_case_store
else:
    from case_library import normalize_case_store, serialize_case_store

USERS_KEY = "liuyao_users_v1"
CASES_KEY = "liuyao_case_library"
BANNED_MESSAGE = "你已被禁止使用六爻插件，请联系管理员。"


class ManagementError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def require_text(value: Any, name: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ManagementError(f"{name}格式无效或过长")
    value = value.strip()
    if not value and not empty:
        raise ManagementError(f"{name}不能为空")
    return value


def case_revision(case: dict) -> str:
    return hashlib.sha256(json.dumps(case, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class UserManagementMixin:
    async def _load_user_registry(self) -> dict:
        raw = await self.get_kv_data(USERS_KEY, {})
        if not isinstance(raw, dict) or not isinstance(raw.get("users", {}), dict):
            raise ValueError("invalid user registry")
        result = copy.deepcopy(raw)
        result.setdefault("users", {})
        for user in result["users"].values():
            if not isinstance(user, dict) or type(user.get("banned", False)) is not bool:
                raise ValueError("invalid user record")
        return result

    async def _user_access_error(self, event) -> str:
        try:
            sender = str(event.get_sender_id() or "").strip()
            if not sender:
                return "无法识别用户，暂不能使用六爻插件。"
            registry = await self._load_user_registry()
            return BANNED_MESSAGE if registry["users"].get(sender, {}).get("banned") else ""
        except Exception as exc:
            logger.warning("liuyao：用户权限读取失败：%s", type(exc).__name__)
            return "六爻用户权限暂不可用，请稍后重试。"

    @staticmethod
    def _new_user(sender: str) -> dict:
        return {
            "id": sender,
            "name": "",
            "first_cast_at": "",
            "last_cast_at": "",
            "cast_count": 0,
            "groups": [],
            "quota_keys": [],
            "banned": False,
            "ban_reason": "",
            "note": "",
            "audit": [],
        }

    def _merge_user_cast(self, user: dict, *, name: str, timestamp: str, group: str) -> None:
        user["cast_count"] += 1
        if not user["first_cast_at"] or timestamp < user["first_cast_at"]:
            user["first_cast_at"] = timestamp
        if timestamp >= user["last_cast_at"]:
            user["last_cast_at"] = timestamp
            user["name"] = name[:100]
        if group and group not in user["groups"]:
            user["groups"].append(group)

    async def _ensure_user_registry(self) -> None:
        # Once-only backfill, even when saving new cases has been disabled.
        # All writers share the quota lock, and case -> quota is the lock order.
        async with self._case_lock_instance(), self._daily_lock():
            registry = await self._load_user_registry()
            if registry.get("legacy_imported"):
                return
            cases = normalize_case_store(await self.get_kv_data(CASES_KEY, {}))
            for case in cases:
                sender = str(case.get("caster_id") or "").strip()
                if not sender:
                    continue
                user = registry["users"].setdefault(sender, self._new_user(sender))
                self._merge_user_cast(
                    user,
                    name=str(case.get("caster_name") or ""),
                    timestamp=str(case.get("created_at") or ""),
                    group=str(case.get("group_id") or ""),
                )
                origin = str(case.get("origin") or "")
                if origin.count(":") >= 2:
                    event = SimpleNamespace(
                        get_sender_id=lambda s=sender: s, unified_msg_origin=origin
                    )
                    key, _ = self._daily_identity(event)
                    if key not in user["quota_keys"]:
                        user["quota_keys"].append(key)
            registry["legacy_imported"] = True
            await self.put_kv_data(USERS_KEY, registry)

    async def _record_cast_user(self, event, cast_at) -> None:
        await self._ensure_user_registry()
        sender = str(event.get_sender_id() or "").strip()
        name, _ = self._caster_identity(event)
        key, _ = self._daily_identity(event)
        async with self._daily_lock():
            registry = await self._load_user_registry()
            user = registry["users"].setdefault(sender, self._new_user(sender))
            self._merge_user_cast(
                user,
                name=name,
                timestamp=cast_at.isoformat(),
                group=str(event.get_group_id() or ""),
            )
            if key not in user["quota_keys"]:
                user["quota_keys"].append(key)
            await self.put_kv_data(USERS_KEY, registry)

    async def _managed_user(self, sender: str) -> dict:
        registry = await self._load_user_registry()
        if sender not in registry["users"]:
            raise ManagementError("用户不存在，请先刷新用户列表", 404)
        return registry["users"][sender]

    async def _admin_user_detail(self, sender: str) -> dict:
        async with self._daily_lock():
            user = await self._managed_user(sender)
            revision = case_revision(user)
            gift = await self._load_gift_record(self._gift_key(sender))
            user["gift_remaining"] = self._gift_available(gift)
            user["gift_reserved"] = gift["balance"] - user["gift_remaining"]
            user["grants"] = list(reversed(gift.get("grants", [])))
            day = self._ai_daily_now().date().isoformat()
            daily_used = []
            for key in user["quota_keys"]:
                daily = await self._load_daily_record(key)
                daily_used.append(
                    sum(bool(e["used"] or e["holds"]) for e in daily["days"].get(day, {}).values())
                )
        event = SimpleNamespace(get_sender_id=lambda: sender)
        limit = await self._effective_ai_daily_limit(event)
        user["daily_unlimited"] = limit == 0 or self._is_ai_quota_whitelisted(event)
        user["daily_limit"] = limit
        user["daily_remaining"] = [max(0, limit - used) for used in daily_used]
        user.pop("quota_keys", None)
        user["revision"] = revision
        return user

    async def _admin_grant(self, sender: str, payload: dict, actor: str) -> dict:
        amount = payload.get("amount")
        if type(amount) is not int or not 1 <= amount <= 10000:
            raise ManagementError("赠送次数须为 1–10000 的整数")
        request_id = require_text(payload.get("request_id"), "操作编号", 64)
        if not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", request_id):
            raise ManagementError("操作编号格式无效")
        note = require_text(payload.get("note", ""), "赠送说明", 300, empty=True)
        async with self._daily_lock():
            await self._managed_user(sender)
            key = self._gift_key(sender)
            gift = await self._load_gift_record(key)
            grants = gift.setdefault("grants", [])
            for grant in grants:
                if grant["request_id"] == request_id:
                    if grant["amount"] != amount or grant["note"] != note:
                        raise ManagementError("操作编号已用于另一笔赠送", 409)
                    return {"saved": True, "duplicate": True}
            if gift["balance"] + amount > 1000000:
                raise ManagementError("赠送余额不能超过 1000000 次")
            gift["balance"] += amount
            grants.append(
                {
                    "request_id": request_id,
                    "amount": amount,
                    "note": note,
                    "actor": actor,
                    "at": self._ai_daily_now().isoformat(),
                }
            )
            await self.put_kv_data(key, gift)
        return {"saved": True}

    async def _admin_update_user(self, sender: str, payload: dict, actor: str) -> dict:
        if type(payload.get("banned")) is not bool:
            raise ManagementError("封禁状态须为布尔值")
        note = require_text(payload.get("note", ""), "用户备注", 1000, empty=True)
        reason = require_text(payload.get("ban_reason", ""), "封禁原因", 300, empty=True)
        async with self._daily_lock():
            registry = await self._load_user_registry()
            if sender not in registry["users"]:
                raise ManagementError("用户不存在", 404)
            user = registry["users"][sender]
            if payload.get("revision") != case_revision(user):
                raise ManagementError("用户信息已变化，请刷新后重试", 409)
            user.update(banned=payload["banned"], ban_reason=reason, note=note)
            user["audit"] = (
                user.get("audit", [])
                + [
                    {
                        "actor": actor,
                        "at": self._ai_daily_now().isoformat(),
                        "banned": user["banned"],
                        "reason": reason,
                    }
                ]
            )[-100:]
            await self.put_kv_data(USERS_KEY, registry)
        return {"saved": True}

    async def _admin_change_case(self, case_id: str, payload: dict, actor: str) -> dict:
        await self._ensure_user_registry()
        action = payload.get("action")
        if action not in {"note", "delete"}:
            raise ManagementError("未知卦例操作")
        note = require_text(payload.get("note", ""), "卦例备注", 2000, empty=True)
        async with self._feedback_lock(case_id), self._case_lock_instance():
            raw = copy.deepcopy(await self.get_kv_data(CASES_KEY, {}))
            cases = normalize_case_store(raw)
            case = next((c for c in cases if c["id"] == case_id), None)
            if case is None:
                raise ManagementError("卦例不存在或已删除", 404)
            if payload.get("revision") != case_revision(case):
                raise ManagementError("卦例已有新反馈或更新，请刷新后重试", 409)
            if action == "delete":
                # Materialize legacy feedback counters before removing the source.
                saved = serialize_case_store(cases, raw)
                ledger = saved.setdefault("valid_feedback_events", {})
                for row in cases:
                    for feedback in row.get("feedback", []):
                        if feedback.get("valid") is True:
                            sender = str(feedback.get("reporter_id") or row.get("caster_id") or "")
                            if sender not in ledger:
                                ledger[sender] = [
                                    str(f.get("observed_at") or "")
                                    for c in cases
                                    for f in c.get("feedback", [])
                                    if f.get("valid") is True
                                    and str(f.get("reporter_id") or c.get("caster_id") or "")
                                    == sender
                                ]
                cases.remove(case)
                updated = serialize_case_store(cases, saved)
            else:
                case.update(
                    admin_note=note,
                    admin_updated_by=actor,
                    updated_at=self._ai_daily_now().isoformat(),
                )
                updated = serialize_case_store(cases, raw)
            await self.put_kv_data(CASES_KEY, updated)
        return {"saved": True}
