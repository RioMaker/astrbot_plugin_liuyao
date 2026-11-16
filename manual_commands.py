"""Explicit manual casts and numbered rereadings; never fall back to random casts."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime

if __package__:
    from .divination import ManualCastError, parse_manual_cast, split_manual_payload
    from .feedback_card import case_cast
    from .paipan import BEIJING
else:
    from divination import ManualCastError, parse_manual_cast, split_manual_payload
    from feedback_card import case_cast
    from paipan import BEIJING


CASE_NUMBER_RE = re.compile(r"^(?:编号\s*|[#＃])([0-9]{1,12})(?:\s+([\s\S]*))?\Z")
TIME_RE = re.compile(
    r"^时间[=＝]\s*([0-9]{4}-[0-9]{2}-[0-9]{2})[Tt ]"
    r"([0-9]{2}:[0-9]{2}(?::[0-9]{2})?)(?:\s+([\s\S]*))?\Z"
)


def parse_manual_request(payload: str, *, now: datetime | None = None):
    lines, rest = split_manual_payload(payload)
    cast = parse_manual_cast(lines)
    source = "received"
    cast_at = now or datetime.now(BEIJING)
    if rest.startswith(("时间=", "时间＝")):
        match = TIME_RE.fullmatch(rest)
        if not match:
            raise ManualCastError("时间格式：时间=2026-11-15T20:30（北京时间，可省略）")
        try:
            cast_at = datetime.fromisoformat(f"{match[1]}T{match[2]}").replace(tzinfo=BEIJING)
            if not 1900 <= cast_at.year <= 2100:
                raise ValueError
        except ValueError as exc:
            raise ManualCastError(
                "起卦时间无效，请填写 1900–2100 年的真实公历日期和时分。"
            ) from exc
        source, rest = "specified", (match[3] or "").strip()
    return replace(cast, cast_at=cast_at.astimezone(BEIJING)), rest, source


class ManualCommandsMixin:
    async def _manual_command_reply(self, event, payload: str, *, analyze: bool) -> str:
        if error := await self._group_gate(event, "liuyao"):
            return error
        text = payload.strip()
        if analyze and text.startswith(("编号", "#", "＃")):
            match = CASE_NUMBER_RE.fullmatch(text)
            if not match:
                return "用法：/六爻解卦 编号233 [问题]，或 /六爻解卦 #233 [问题]。"
            records = await self._own_feedback_cases(event)
            record = next((row for row in records if row["serial"] == int(match[1])), None)
            if record is None:
                return "未找到你在当前群的该编号卦例，请用 /六爻反馈 查看全部记录。"
            try:
                cast = case_cast(record)
            except (KeyError, TypeError, ValueError):
                return "该卦例的六爻数据不完整，无法解卦。"
            question = (match[2] or "").strip() or str(
                record.get("analysis_question") or record.get("question") or ""
            )
            original = {
                "question": record.get("question", ""),
                "intent": record.get("intent", "general"),
                "method": record.get("method", "历史排盘"),
            }
            return await self._content_reply(
                event, question, original=original, original_cast=cast, case_record=record
            )
        try:
            cast, question, time_source = parse_manual_request(text)
        except ManualCastError as exc:
            action = "解卦" if analyze else "排盘"
            return (
                f"{exc}\n用法：/六爻{action} 789678 [时间=2026-11-15T20:30] [问题]\n"
                "六爻按初爻到上爻填写；6 老阴、7 少阳、8 少阴、9 老阳。"
                "也可填写六组正反结果（正=3，反=2）。"
            )
        return await self._content_reply(
            event, question, supplied_cast=cast, chart_only=not analyze, time_source=time_source
        )
