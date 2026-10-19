"""Per-QQ-user daily AI admission backed by AstrBot KV storage."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from astrbot.api import logger

BEIJING = timezone(timedelta(hours=8))
STORAGE_ERROR = "AI 次数记录读取或保存失败，本次暂不调用 AI，请稍后重试。"


class AIQuotaStorageError(RuntimeError):
    """The persistent quota could not be safely updated."""


async def _wait_persistent_task(task: asyncio.Task) -> tuple[Any, bool]:
    """Finish a KV transaction even when the caller is cancelled repeatedly."""
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task), cancelled
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True


@dataclass
class AIQuotaLease:
    owner: DailyAIQuotaMixin
    key: str = ""
    day: str = ""
    token: str = ""
    hold: str = ""
    error: str = ""
    already_counted: bool = False
    _finished: bool = False

    async def commit(self) -> None:
        if not self.error and not self._finished:
            await self.owner._finish_ai_daily(self, used=True)
            self._finished = True

    async def release(self) -> None:
        if not self.error and not self._finished:
            await self.owner._finish_ai_daily(self, used=False)
            self._finished = True


class DailyAIQuotaMixin:
    AI_DAILY_LABEL = "本插件"

    @staticmethod
    def _ai_daily_now() -> datetime:
        return datetime.now(BEIJING)

    def _daily_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_ai_daily_quota_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._ai_daily_quota_lock = lock
        return lock

    @staticmethod
    def _daily_limit_value(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        try:
            limit = int(value)
        except ValueError:
            return None
        return limit if 0 <= limit <= 10000 else None

    def _effective_ai_daily_limit(self, event: Any) -> int:
        config = getattr(self, "config", None) or {}
        default = self._daily_limit_value(config.get("ai_daily_limit", 1))
        return 1 if default is None else default

    def _is_ai_quota_whitelisted(self, event: Any) -> bool:
        try:
            config = getattr(self, "config", None) or {}
            configured = config.get("ai_quota_whitelist_qq", "")
            if isinstance(configured, str):
                user_ids = re.split(r"[,，;；\s]+", configured)
            elif isinstance(configured, (list, tuple, set)):
                user_ids = configured
            else:
                return False
            sender = str(event.get_sender_id() or "").strip()
        except Exception:
            return False
        return bool(sender) and any(
            str(user_id).strip() == sender
            for user_id in user_ids
            if isinstance(user_id, (str, int)) and not isinstance(user_id, bool)
        )

    @staticmethod
    def _daily_identity(event: Any) -> tuple[str, str]:
        sender = str(event.get_sender_id() or "").strip()
        if not sender:
            raise ValueError("missing sender")
        umo = str(getattr(event, "unified_msg_origin", "") or "")
        get_platform_id = getattr(event, "get_platform_id", None)
        platform = str(get_platform_id() or "") if callable(get_platform_id) else ""
        if not platform:
            platform = umo.rsplit(":", 2)[0] if umo.count(":") >= 2 else ""
        if not platform:
            get_platform = getattr(event, "get_platform_name", None)
            platform = str(get_platform() or "") if callable(get_platform) else ""
        if not platform:
            raise ValueError("missing platform")
        # The user's allowance follows their QQ account across groups and sessions.
        identity = [platform, "user", sender]
        digest = hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()
        message = getattr(event, "message_obj", None)
        message_id = getattr(message, "message_id", None)
        if message_id is None or not str(message_id).strip():
            get_extra = getattr(event, "get_extra", None)
            token = (
                get_extra("ai_daily_quota_message_token", None)
                if callable(get_extra)
                else None
            )
            if not token:
                token = getattr(event, "_ai_daily_quota_message_token", None)
            if not token:
                token = "event:" + uuid.uuid4().hex
                set_extra = getattr(event, "set_extra", None)
                if callable(set_extra):
                    set_extra("ai_daily_quota_message_token", token)
                else:
                    event._ai_daily_quota_message_token = token
        else:
            token = "message:" + str(message_id).strip()
        return "ai_daily_quota_v1:" + digest, str(token)

    async def _load_daily_record(self, key: str) -> dict[str, Any]:
        raw = await self.get_kv_data(key, {})
        if not isinstance(raw, dict) or not isinstance(raw.get("days", {}), dict):
            raise TypeError("invalid quota record")
        # Do not mutate an object returned by an in-memory KV implementation
        # before the corresponding persistent write succeeds.
        record = json.loads(json.dumps(raw))
        days = record.setdefault("days", {})
        for entries in days.values():
            if not isinstance(entries, dict):
                raise TypeError("invalid quota day")
            for entry in entries.values():
                if (
                    not isinstance(entry, dict)
                    or not isinstance(entry.get("used"), bool)
                    or not isinstance(entry.get("holds"), list)
                    or any(not isinstance(hold, str) for hold in entry["holds"])
                ):
                    raise ValueError("invalid quota entry")
        return record

    async def _reserve_ai_daily(self, event: Any) -> AIQuotaLease:
        if self._is_ai_quota_whitelisted(event):
            # A whitelist bypass still performs the model work, but stores no quota entry.
            return AIQuotaLease(self, _finished=True)
        try:
            # Event/session data may be changed by other framework handlers.
            # Freeze the admission identity before the first await.
            key, token = self._daily_identity(event)
            now = self._ai_daily_now().astimezone(BEIJING)
            day = now.date().isoformat()
            yesterday = (now.date() - timedelta(days=1)).isoformat()
            limit = self._effective_ai_daily_limit(event)
        except Exception as exc:  # noqa: BLE001 - malformed identities must fail closed.
            logger.error(f"AI 日限额身份读取失败：{type(exc).__name__}")
            return AIQuotaLease(self, error=STORAGE_ERROR)
        # A cancellation during the persistent write must not leave an unused
        # reservation behind. Wait for its outcome, then release it.
        task = asyncio.create_task(
            self._reserve_ai_daily_uninterrupted(key, token, day, yesterday, limit)
        )
        lease, cancelled = await _wait_persistent_task(task)
        if cancelled:
            await _wait_persistent_task(asyncio.create_task(lease.release()))
            raise asyncio.CancelledError
        return lease

    async def _reserve_ai_daily_uninterrupted(
        self, key: str, token: str, day: str, yesterday: str, limit: int
    ) -> AIQuotaLease:
        try:
            async with self._daily_lock():
                record = await self._load_daily_record(key)
                days = record["days"]
                # Yesterday remains available for a request spanning midnight.
                for old_day in tuple(days):
                    if old_day < yesterday:
                        del days[old_day]
                # Retries and follow-up tools for a message crossing midnight
                # remain attached to the day on which it was first admitted.
                for recorded_day, recorded_entries in days.items():
                    if token in recorded_entries:
                        day = recorded_day
                        break
                entries = days.setdefault(day, {})
                existing = entries.get(token)
                if existing and existing["used"]:
                    return AIQuotaLease(
                        self, key, day, token, already_counted=True, _finished=True
                    )
                if existing is None and limit and len(entries) >= limit:
                    return AIQuotaLease(
                        self,
                        error=(
                            str(
                                (getattr(self, "config", None) or {}).get(
                                    "ai_quota_exhausted_message", ""
                                )
                                or f"六爻：你今天的 AI 解卦额度已用完（每天 {limit} 次），"
                                "北京时间次日 00:00 恢复。本次仍为你起卦，但不会调用 AI 解读。"
                            )
                        ),
                    )
                hold = uuid.uuid4().hex
                entry = entries.setdefault(token, {"used": False, "holds": []})
                entry["holds"].append(hold)
                await self.put_kv_data(key, record)
                return AIQuotaLease(
                    self, key, day, token, hold, already_counted=existing is not None
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed for any storage backend failure.
            logger.error(f"AI 日限额读取或预约失败：{type(exc).__name__}")
            return AIQuotaLease(self, error=STORAGE_ERROR)

    async def _finish_ai_daily(self, lease: AIQuotaLease, *, used: bool) -> None:
        async def finish() -> None:
            try:
                async with self._daily_lock():
                    if lease._finished:
                        return
                    record = await self._load_daily_record(lease.key)
                    entries = record["days"].get(lease.day, {})
                    entry = entries.get(lease.token)
                    if entry is None or lease.hold not in entry["holds"]:
                        raise ValueError("quota reservation disappeared")
                    entry["used"] = entry["used"] or used
                    entry["holds"].remove(lease.hold)
                    if not entry["used"] and not entry["holds"]:
                        del entries[lease.token]
                    await self.put_kv_data(lease.key, record)
                    lease._finished = True
            except Exception as exc:
                logger.error(f"AI 日限额确认或释放失败：{type(exc).__name__}")
                raise AIQuotaStorageError(STORAGE_ERROR) from exc

        # Finish an already-owned reservation even if its caller is cancelled.
        task = asyncio.create_task(finish())
        _, cancelled = await _wait_persistent_task(task)
        if cancelled:
            raise asyncio.CancelledError
