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
GIFT_RESERVATION_SECONDS = 900


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
    quota_unlimited: bool = False
    source: str = "daily"
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

    def _ai_quota_feedback_rules(self) -> list[dict[str, Any]]:
        config = getattr(self, "config", None) or {}
        configured = config.get("ai_quota_feedback_rules", [])
        if not isinstance(configured, list):
            return []

        rules = []
        for index, entry in enumerate(configured, start=1):
            if not isinstance(entry, dict):
                continue
            try:
                window_days = int(entry.get("window_days", 0))
                minimum = int(entry.get("minimum_feedback_count", 0))
                bonus = int(entry.get("daily_bonus", 0))
            except (TypeError, ValueError):
                continue
            if (
                isinstance(entry.get("window_days"), bool)
                or isinstance(entry.get("minimum_feedback_count"), bool)
                or isinstance(entry.get("daily_bonus"), bool)
                or not 1 <= window_days <= 3650
                or not 1 <= minimum <= 10000
                or not 0 <= bonus <= 10000
                or bonus == 0
            ):
                continue
            rule_name = str(entry.get("name") or "").strip() or f"规则{index}"
            rules.append(
                {
                    "name": rule_name,
                    "window_days": window_days,
                    "minimum_feedback_count": minimum,
                    "daily_bonus": bonus,
                }
            )
        return rules

    @staticmethod
    def _feedback_time(value: object) -> datetime | None:
        try:
            observed_at = datetime.fromisoformat(str(value or "").strip())
        except (TypeError, ValueError):
            return None
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=BEIJING)
        return observed_at.astimezone(BEIJING)

    async def _valid_feedback_counts(self, event: Any, window_days: set[int]) -> dict[int, int]:
        if not window_days:
            return {}
        sender = str(event.get_sender_id() or "").strip()
        if not sender:
            return {days: 0 for days in window_days}

        payload = await self.get_kv_data("liuyao_case_library", {})
        if isinstance(payload, dict):
            event_ledger = payload.get("valid_feedback_events", {})
            cases = payload.get("cases", [])
        elif isinstance(payload, list):
            event_ledger = {}
            cases = payload
        else:
            return {days: 0 for days in window_days}

        timestamps: list[datetime] = []
        if isinstance(event_ledger, dict) and sender in event_ledger:
            raw_events = event_ledger.get(sender)
            if isinstance(raw_events, list):
                timestamps.extend(
                    parsed
                    for parsed in (self._feedback_time(value) for value in raw_events)
                    if parsed is not None
                )
        else:
            if isinstance(cases, list):
                for case in cases:
                    if not isinstance(case, dict):
                        continue
                    feedback_rows = case.get("feedback", [])
                    if not isinstance(feedback_rows, list):
                        continue
                    for feedback in feedback_rows:
                        if not isinstance(feedback, dict) or feedback.get("valid") is not True:
                            continue
                        reporter_id = str(
                            feedback.get("reporter_id") or case.get("caster_id") or ""
                        ).strip()
                        if reporter_id != sender:
                            continue
                        parsed = self._feedback_time(feedback.get("observed_at"))
                        if parsed is not None:
                            timestamps.append(parsed)

        now = self._ai_daily_now().astimezone(BEIJING)
        return {
            days: sum(
                1 for observed_at in timestamps if now - timedelta(days=days) <= observed_at <= now
            )
            for days in window_days
        }

    async def _feedback_score(self, event: Any) -> int:
        sender = str(event.get_sender_id() or "").strip()
        if not sender:
            return 0
        payload = await self.get_kv_data("liuyao_case_library", {})
        if not isinstance(payload, dict):
            return 0
        scores = payload.get("user_scores", {})
        if not isinstance(scores, dict):
            return 0
        try:
            return max(0, int(scores.get(sender, 0)))
        except (TypeError, ValueError):
            return 0

    def _base_ai_daily_limit(self) -> int:
        config = getattr(self, "config", None) or {}
        default = self._daily_limit_value(config.get("ai_daily_limit", 1))
        return 1 if default is None else default

    @staticmethod
    def _feedback_rule_bonus(rules: list[dict[str, Any]], counts: dict[int, int]) -> int:
        return sum(
            rule["daily_bonus"]
            for rule in rules
            if counts.get(rule["window_days"], 0) >= rule["minimum_feedback_count"]
        )

    async def _effective_ai_daily_limit(self, event: Any) -> int:
        base_limit = self._base_ai_daily_limit()
        if base_limit == 0:
            return 0
        rules = self._ai_quota_feedback_rules()
        if not rules:
            return base_limit
        counts = await self._valid_feedback_counts(event, {rule["window_days"] for rule in rules})
        return min(10000, base_limit + self._feedback_rule_bonus(rules, counts))

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
            token = get_extra("ai_daily_quota_message_token", None) if callable(get_extra) else None
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

    @staticmethod
    def _gift_key(sender: str) -> str:
        return "liuyao_gift_quota_v1:" + hashlib.sha256(sender.encode()).hexdigest()

    async def _load_gift_record(self, key: str) -> dict[str, Any]:
        record = await self._load_daily_record(key)
        balance = record.setdefault("balance", 0)
        if type(balance) is not int or balance < 0:
            raise ValueError("invalid gift balance")
        # A process crash before commit must not lock a gifted credit forever.
        # Expired leases cannot commit: _finish_ai_daily reloads through this path.
        now = self._ai_daily_now().timestamp()
        for entries in record["days"].values():
            for token, entry in list(entries.items()):
                expires = entry.get("expires_at")
                if not entry["used"] and isinstance(expires, (int, float)) and expires <= now:
                    del entries[token]
        return record

    @staticmethod
    def _gift_available(record: dict[str, Any]) -> int:
        pending = sum(
            1
            for entries in record["days"].values()
            for entry in entries.values()
            if not entry["used"] and entry["holds"]
        )
        return max(0, record["balance"] - pending)

    async def _gift_remaining(self, event: Any) -> int:
        async with self._daily_lock():
            record = await self._load_gift_record(
                self._gift_key(str(event.get_sender_id() or "").strip())
            )
            return self._gift_available(record)

    async def _ai_quota_brief(self, event: Any) -> str:
        try:
            limit = await self._effective_ai_daily_limit(event)
            key, _ = self._daily_identity(event)
            async with self._daily_lock():
                record = await self._load_daily_record(key)
                entries = record["days"].get(self._ai_daily_now().date().isoformat(), {})
                used = sum(bool(e["used"] or e["holds"]) for e in entries.values())
                gift = await self._load_gift_record(
                    self._gift_key(str(event.get_sender_id() or "").strip())
                )
                extra = self._gift_available(gift)
            remaining = (
                "不限"
                if not limit or self._is_ai_quota_whitelisted(event)
                else str(max(0, limit - used))
            )
            return f"额度余量：{remaining}{f' +{extra}' if extra else ''} 次（已扣除本次预留）。"
        except Exception:
            return "额度余量暂时无法读取，可稍后发送 /额度 查询。"

    async def _reserve_ai_daily(self, event: Any) -> AIQuotaLease:
        gate = getattr(self, "_user_access_error", None)
        if callable(gate) and (error := await gate(event)):
            return AIQuotaLease(self, error=error)
        if self._is_ai_quota_whitelisted(event):
            # A whitelist bypass still performs the model work, but stores no quota entry.
            return AIQuotaLease(self, quota_unlimited=True, _finished=True)
        try:
            # Event/session data may be changed by other framework handlers.
            # Freeze the admission identity before the first await.
            key, token = self._daily_identity(event)
            gift_key = self._gift_key(str(event.get_sender_id() or "").strip())
            now = self._ai_daily_now().astimezone(BEIJING)
            day = now.date().isoformat()
            yesterday = (now.date() - timedelta(days=1)).isoformat()
            limit = await self._effective_ai_daily_limit(event)
        except Exception as exc:  # noqa: BLE001 - malformed identities must fail closed.
            logger.error(f"AI 日限额身份读取失败：{type(exc).__name__}")
            return AIQuotaLease(self, error=STORAGE_ERROR)
        # A cancellation during the persistent write must not leave an unused
        # reservation behind. Wait for its outcome, then release it.
        task = asyncio.create_task(
            self._reserve_ai_daily_uninterrupted(key, token, day, yesterday, limit, gift_key)
        )
        lease, cancelled = await _wait_persistent_task(task)
        if cancelled:
            await _wait_persistent_task(asyncio.create_task(lease.release()))
            raise asyncio.CancelledError
        return lease

    async def _ai_quota_status(self, event: Any) -> str:
        gate = getattr(self, "_user_access_error", None)
        if callable(gate) and (error := await gate(event)):
            return error
        now = self._ai_daily_now().astimezone(BEIJING)
        reset_at = datetime.combine(
            now.date() + timedelta(days=1), datetime.min.time(), tzinfo=BEIJING
        )
        reset_text = reset_at.strftime("%Y-%m-%d %H:%M:%S")
        try:
            gift_remaining = await self._gift_remaining(event)
            gift_suffix = f" +{gift_remaining}" if gift_remaining else ""
            score = await self._feedback_score(event)
            rules = self._ai_quota_feedback_rules()
            counts = await self._valid_feedback_counts(
                event, {rule["window_days"] for rule in rules}
            )
            bonus = self._feedback_rule_bonus(rules, counts)
            base_limit = self._base_ai_daily_limit()
            limit = 0 if base_limit == 0 else min(10000, base_limit + bonus)
            applied_bonus = max(0, limit - base_limit) if base_limit > 0 else 0
            rule_progress = (
                "\n反馈奖励规则："
                + "\n".join(
                    f"{index}）{rule['name']}：最近{rule['window_days']}天有效反馈 "
                    f"{counts.get(rule['window_days'], 0)}/{rule['minimum_feedback_count']} 次，"
                    f"达标每日 +{rule['daily_bonus']} 次"
                    for index, rule in enumerate(rules, start=1)
                )
                if rules
                else "\n反馈奖励规则：未配置，当前无额外额度"
            )
            quota_detail = (
                f"基础 {base_limit} 次 + 反馈奖励 {applied_bonus} 次" if base_limit > 0 else "不限"
            )
            if self._is_ai_quota_whitelisted(event):
                return (
                    f"六爻 AI 每日额度：白名单，不限{gift_suffix}\n"
                    f"反馈积分：{score}\n"
                    f"额度恢复时间：北京时间 {reset_text}{rule_progress}"
                )

            key, _ = self._daily_identity(event)
            record = await self._load_daily_record(key)
            entries = record["days"].get(now.date().isoformat(), {})
            used = sum(1 for entry in entries.values() if entry.get("used") or entry.get("holds"))
            if limit == 0:
                return (
                    f"六爻 AI 每日额度：不限{gift_suffix}\n"
                    f"反馈积分：{score}\n"
                    f"额度恢复时间：北京时间 {reset_text}{rule_progress}"
                )
            remaining = max(0, limit - used)
            return (
                f"六爻 AI 每日额度：{limit} 次（{quota_detail}）\n"
                f"今日已用：{used} 次，剩余：{remaining}{gift_suffix} 次\n"
                f"反馈积分：{score}\n"
                f"额度恢复时间：北京时间 {reset_text}{rule_progress}"
            )
        except Exception as exc:
            logger.warning("liuyao：读取 AI 额度状态失败：%r", exc)
            return "暂时无法读取六爻 AI 额度信息，请稍后重试。"

    async def _reserve_ai_daily_uninterrupted(
        self,
        key: str,
        token: str,
        day: str,
        yesterday: str,
        limit: int,
        gift_key: str = "",
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
                # Gift reservations live in one QQ-account record across platforms.
                # Check them before admitting a retry into a newly reset daily quota.
                gift_record = await self._load_gift_record(gift_key) if gift_key else None
                gift_token = key + ":" + token
                gift_day = day
                gift_existing = None
                if gift_record is not None:
                    for recorded_day, recorded_entries in gift_record["days"].items():
                        if gift_token in recorded_entries:
                            gift_day, gift_existing = recorded_day, recorded_entries[gift_token]
                            break
                if gift_existing is not None:
                    return await self._reserve_gift_locked(
                        gift_key, gift_token, gift_day, yesterday, gift_record
                    )
                if existing and existing["used"]:
                    return AIQuotaLease(
                        self,
                        key,
                        day,
                        token,
                        already_counted=True,
                        quota_unlimited=not bool(limit),
                        _finished=True,
                    )
                if existing is None and limit and len(entries) >= limit:
                    if gift_record is not None and self._gift_available(gift_record) > 0:
                        return await self._reserve_gift_locked(
                            gift_key, gift_token, day, yesterday, gift_record
                        )
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
                    self,
                    key,
                    day,
                    token,
                    hold,
                    already_counted=existing is not None,
                    quota_unlimited=not bool(limit),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - fail closed for any storage backend failure.
            logger.error(f"AI 日限额读取或预约失败：{type(exc).__name__}")
            return AIQuotaLease(self, error=STORAGE_ERROR)

    async def _reserve_gift_locked(
        self, key: str, token: str, day: str, yesterday: str, record: dict[str, Any]
    ) -> AIQuotaLease:
        # Keep unexpired reservations across midnight. Balance is
        # decremented only on first commit, atomically with the reservation entry.
        for old_day, entries in list(record["days"].items()):
            if old_day < yesterday:
                record["days"][old_day] = {
                    t: e for t, e in entries.items() if e["holds"] or t == token
                }
                if not record["days"][old_day]:
                    del record["days"][old_day]
        entries = record["days"].setdefault(day, {})
        existing = entries.get(token)
        if existing and existing["used"]:
            return AIQuotaLease(
                self, key, day, token, already_counted=True, source="gift", _finished=True
            )
        hold = uuid.uuid4().hex
        entry = entries.setdefault(
            token,
            {
                "used": False,
                "holds": [],
                "expires_at": self._ai_daily_now().timestamp() + GIFT_RESERVATION_SECONDS,
            },
        )
        entry["holds"].append(hold)
        await self.put_kv_data(key, record)
        return AIQuotaLease(
            self, key, day, token, hold, already_counted=existing is not None, source="gift"
        )

    async def _finish_ai_daily(self, lease: AIQuotaLease, *, used: bool) -> None:
        async def finish() -> None:
            try:
                async with self._daily_lock():
                    if lease._finished:
                        return
                    record = (
                        await self._load_gift_record(lease.key)
                        if lease.source == "gift"
                        else await self._load_daily_record(lease.key)
                    )
                    entries = record["days"].get(lease.day, {})
                    entry = entries.get(lease.token)
                    if entry is None or lease.hold not in entry["holds"]:
                        raise ValueError("quota reservation disappeared")
                    if lease.source == "gift" and used and not entry["used"]:
                        if record["balance"] <= 0:
                            raise ValueError("gift balance exhausted")
                        record["balance"] -= 1
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
