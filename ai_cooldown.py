"""Account-wide AI cooldowns, persisted with successful readings and feedback."""

from __future__ import annotations

import asyncio
import copy
import math
import uuid
from datetime import datetime, timedelta, timezone

if __package__:
    from .case_library import analysis_history, normalize_case_store, serialize_case_store
else:
    from case_library import analysis_history, normalize_case_store, serialize_case_store

CASES_KEY = "liuyao_case_library"
COOLDOWNS_KEY = "ai_cooldowns_v1"
PENDING_SECONDS = 900
BEIJING = timezone(timedelta(hours=8))


class AICooldownMixin:
    def _ai_cooldown_enabled(self):
        return (getattr(self, "config", None) or {}).get("ai_cooldown_enabled", False) is True

    def _ai_cooldown_duration(self, *, first=False):
        config = getattr(self, "config", None) or {}
        prefix = "ai_first_cooldown" if first else "ai_normal_cooldown"
        default, default_unit = (7, "天") if first else (24, "小时")
        value = config.get(prefix + "_duration", default)
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            value = default
        try:
            value = int(value)
        except ValueError:
            value = default
        if not 1 <= value <= 87600:
            value = default
        unit = config.get(prefix + "_unit", default_unit)
        if unit not in ("小时", "天"):
            unit = default_unit
        return value * (86400 if unit == "天" else 3600), f"{value} {unit}"

    def _ai_cooldown_lock(self):
        # Share the case lock: success/feedback and admission use the same ledger.
        lock = getattr(self, "_case_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._case_lock = lock
        return lock

    async def _ai_cooldown_payload(self):
        payload = copy.deepcopy(await self.get_kv_data(CASES_KEY, {}))
        if isinstance(payload, list):
            return serialize_case_store(normalize_case_store(payload), payload)
        if not isinstance(payload, dict):
            raise ValueError("invalid cooldown store")
        return payload

    def _ai_cooldown_record(self, payload, sender):
        ledger = payload.setdefault(COOLDOWNS_KEY, {})
        if not isinstance(ledger, dict):
            raise ValueError("invalid cooldown ledger")
        if sender not in ledger:
            # Existing successful users are established users, not new trial users.
            legacy = [
                case
                for case in normalize_case_store(payload)
                if str(case.get("caster_id", "")) == sender and case.get("analysis")
            ]
            timestamps = [
                parsed.timestamp()
                for case in legacy
                for row in analysis_history(case)
                if row.get("status") == "success"
                and (parsed := self._feedback_time(row.get("updated_at") or row.get("at")))
            ]
            ledger[sender] = {
                "first_at": 0,
                "first_case_id": "",
                "last_at": max(timestamps, default=0),
                "last_token": "",
                "normal": bool(legacy),
                "pending": {},
            }
        record = ledger[sender]
        if not isinstance(record, dict) or type(record.get("normal")) is not bool:
            raise ValueError("invalid cooldown record")
        for key in ("first_at", "last_at"):
            value = record.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError("invalid cooldown timestamp")
        if not isinstance(record.get("pending"), dict):
            raise ValueError("invalid cooldown reservations")
        now = self._ai_daily_now().timestamp()
        for token, pending in list(record["pending"].items()):
            if (
                not isinstance(pending, dict)
                or not isinstance(pending.get("holds"), list)
                or any(not isinstance(hold, str) for hold in pending["holds"])
                or type(pending.get("expires_at")) not in (int, float)
                or not math.isfinite(pending["expires_at"])
            ):
                raise ValueError("invalid cooldown reservation")
            if pending["expires_at"] <= now:
                del record["pending"][token]
        return record

    def _ai_cooldown_phase(self, record):
        config = getattr(self, "config", None) or {}
        first = not record["normal"] and config.get("ai_first_cooldown_enabled", True) is True
        seconds, label = self._ai_cooldown_duration(first=first)
        return first, seconds, label

    def _ai_cooldown_notice(self, record):
        first, seconds, label = self._ai_cooldown_phase(record)
        if not record["last_at"]:
            return ""
        ready_at = record["last_at"] + seconds
        if self._ai_daily_now().timestamp() >= ready_at:
            return ""
        ready = datetime.fromtimestamp(ready_at, BEIJING).strftime("%Y-%m-%d %H:%M:%S")
        feedback = "首次卦例反馈审核通过后，立即改按正常冷却时长计算。" if first else ""
        return (
            f"AI 解卦{'首次' if first else '正常'}冷却中（{label}），"
            f"北京时间 {ready} 恢复。{feedback}"
        )

    async def _reserve_ai_with_cooldown(self, sender, key, token, day, yesterday, limit, gift_key):
        if not self._ai_cooldown_enabled():
            return await self._reserve_ai_daily_uninterrupted(
                key, token, day, yesterday, limit, gift_key
            )
        # Keep lock order case -> quota, as used by user management.
        async with self._ai_cooldown_lock():
            payload = await self._ai_cooldown_payload()
            record = self._ai_cooldown_record(payload, sender)
            identity = key + ":" + token
            if identity != record["last_token"]:
                notice = self._ai_cooldown_notice(record)
                if notice:
                    return self._cooldown_denied(notice)
            if any(other != identity for other in record["pending"]):
                return self._cooldown_denied("已有一条 AI 解卦正在处理，请等待完成后再试。")
            lease = await self._reserve_ai_daily_uninterrupted(
                key, token, day, yesterday, limit, gift_key
            )
            if lease.error or lease._finished:
                return lease
            hold = uuid.uuid4().hex
            pending = record["pending"].setdefault(identity, {"holds": []})
            pending["holds"].append(hold)
            pending["expires_at"] = self._ai_daily_now().timestamp() + PENDING_SECONDS
            try:
                await self.put_kv_data(CASES_KEY, payload)
            except BaseException:
                await lease.release()
                raise
            lease.cooldown_sender = sender
            lease.cooldown_token = identity
            lease.cooldown_hold = hold
            return lease

    async def _release_ai_cooldown(self, lease):
        if not lease.cooldown_hold:
            return
        async with self._ai_cooldown_lock():
            payload = await self._ai_cooldown_payload()
            record = self._ai_cooldown_record(payload, lease.cooldown_sender)
            pending = record["pending"].get(lease.cooldown_token)
            if pending and lease.cooldown_hold in pending["holds"]:
                pending["holds"].remove(lease.cooldown_hold)
                if not pending["holds"]:
                    del record["pending"][lease.cooldown_token]
                await self.put_kv_data(CASES_KEY, payload)
            lease.cooldown_hold = ""

    def _record_ai_cooldown_success(self, payload, event, case_id):
        """Called before changing the original/history, inside its persistence transaction."""
        sender = str(event.get_sender_id() or "").strip()
        record = self._ai_cooldown_record(payload, sender)
        key, token = self._daily_identity(event)
        identity = key + ":" + token
        if record["last_token"] == identity:
            return
        now = self._ai_daily_now().timestamp()
        if record["last_at"] or record["normal"]:
            record["normal"] = True
        else:
            record["first_at"] = now
            record["first_case_id"] = case_id
        record["last_at"] = now
        record["last_token"] = identity

    def _record_ai_cooldown_feedback(self, payload, sender, case_id):
        record = self._ai_cooldown_record(payload, sender)
        if record["first_case_id"] == case_id:
            # Preserve the successful reading time: feedback never restarts the clock.
            record["normal"] = True

    async def _ai_cooldown_status(self, event):
        if not self._ai_cooldown_enabled():
            return ""
        if self._is_ai_quota_whitelisted(event):
            return "AI 解卦冷却：白名单豁免。"
        async with self._ai_cooldown_lock():
            payload = await self._ai_cooldown_payload()
            record = self._ai_cooldown_record(payload, str(event.get_sender_id() or "").strip())
            notice = self._ai_cooldown_notice(record)
            if notice:
                return notice
            if record["pending"]:
                return "AI 解卦冷却：当前请求处理中。"
            first = (
                not record["last_at"]
                and not record["normal"]
                and (getattr(self, "config", None) or {}).get("ai_first_cooldown_enabled", True)
                is True
            )
            _, label = self._ai_cooldown_duration(first=first)
            return f"AI 解卦冷却：当前可用（下次成功解卦后冷却 {label}）。"
