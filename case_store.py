"""AstrBot KV-backed persistence mixin for Liuyao case records."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from astrbot.api import logger

try:
    from .case_library import (
        extract_verdict,
        format_case_references,
        normalize_case_store,
        search_cases,
        serialize_case_store,
        trim_cases,
    )
    from .najia import relatives_for_bits
    from .paipan import chart_for
except ImportError:  # pragma: no cover - direct local execution
    from case_library import (
        extract_verdict,
        format_case_references,
        normalize_case_store,
        search_cases,
        serialize_case_store,
        trim_cases,
    )
    from najia import relatives_for_bits
    from paipan import chart_for


LIUYAO_CASES_KEY = "liuyao_case_library"


class LiuyaoCaseStoreMixin:
    """Methods expected to be mixed into the AstrBot plugin class."""

    async def _open_agent_case(
        self,
        event: Any,
        cast: Any,
        *,
        intent: str,
        question: str,
        method: str,
        cast_at: datetime,
        track_agent: bool = True,
    ) -> tuple[str, str, str]:
        if not bool(self._config_get("case_library_enabled", True)):
            return "", "", "卦例库已由配置关闭。"
        get_data = getattr(self, "get_kv_data", None)
        put_data = getattr(self, "put_kv_data", None)
        if not callable(get_data) or not callable(put_data):
            return "", "", "AstrBot 持久化接口不可用，本次未存档。"

        group_id = str(event.get_group_id() or "").strip()
        caster_name, caster_id = self._caster_identity(event)
        try:
            async with self._case_lock_instance():
                payload = await get_data(LIUYAO_CASES_KEY, {})
                cases = normalize_case_store(payload)
                references = search_cases(
                    cases,
                    group_id=group_id,
                    query=question,
                    intent=self.readings.normalize_intent(intent),
                    primary_number=cast.primary_number,
                    changed_number=cast.changed_number,
                    moving_lines=cast.moving_lines,
                    caster_id=caster_id,
                    limit=self._bounded_config_int("case_reference_limit", 3, 1, 5),
                    cross_group=bool(self._config_get("case_library_cross_group", False)),
                )
                case = self._build_case_record(
                    event,
                    cast,
                    intent=intent,
                    question=question,
                    method=method,
                    cast_at=cast_at,
                    caster_name=caster_name,
                    caster_id=caster_id,
                )
                case["serial"] = serialize_case_store(cases, payload)["next_serial"]
                cases.append(case)
                maximum = self._bounded_config_int("case_library_max_records", 500, 20, 5000)
                await put_data(
                    LIUYAO_CASES_KEY,
                    serialize_case_store(trim_cases(cases, maximum), payload),
                )
            if not track_agent:
                return str(case["id"]), format_case_references(references), "排盘已保存。"
            pending = getattr(self, "_pending_agent_cases", None)
            if not isinstance(pending, dict):
                pending = {}
                self._pending_agent_cases = pending
            run_key = self._agent_run_key(event)
            pending_cases = pending.get(run_key, [])
            if isinstance(pending_cases, str):
                pending_cases = [pending_cases]
            pending_cases.append(str(case["id"]))
            pending[run_key] = pending_cases
            return (
                str(case["id"]),
                format_case_references(references),
                "排盘已保存；Agent 最终回复完成后将自动写入分析与断语。",
            )
        except Exception as exc:
            logger.exception("liuyao：创建卦例失败（%s）：%r", type(exc).__name__, exc)
            return "", "", f"卦例保存失败（{type(exc).__name__}）。"

    def _build_case_record(
        self,
        event: Any,
        cast: Any,
        *,
        intent: str,
        question: str,
        method: str,
        cast_at: datetime,
        caster_name: str,
        caster_id: str,
    ) -> dict[str, Any]:
        primary = self.corpus.get(cast.primary_number)
        changed = self.corpus.get(cast.changed_number)
        primary_relatives = relatives_for_bits(cast.primary_bits)
        changed_relatives = relatives_for_bits(
            cast.changed_bits,
            reference_element=primary_relatives.palace_element,
        )
        intent_key = self.readings.normalize_intent(intent)
        timestamp = cast_at.isoformat()
        return {
            "schema_version": 3,
            "id": f"LY-{cast_at.strftime('%Y%m%d')}-{uuid4().hex[:8]}",
            "created_at": timestamp,
            "updated_at": timestamp,
            "group_id": str(event.get_group_id() or "").strip(),
            "origin": str(getattr(event, "unified_msg_origin", "") or ""),
            "caster_id": caster_id,
            "caster_name": caster_name,
            "intent": intent_key,
            "intent_label": str(self.readings.directions[intent_key]["label"]),
            "question": question,
            "method": method,
            "cast": {
                "chart": chart_for(cast, cast_at),
                "lines": list(cast.lines),
                "primary_number": int(primary["number"]),
                "primary_name": str(primary["name"]),
                "primary_symbol": str(primary["symbol"]),
                "primary_judgment": str(primary["judgment"]),
                "changed_number": int(changed["number"]),
                "changed_name": str(changed["name"]),
                "changed_symbol": str(changed["symbol"]),
                "changed_judgment": str(changed["judgment"]),
                "moving_lines": list(cast.moving_lines),
                "moving_texts": [
                    {
                        "position": position,
                        "text": str(primary["lines"][position - 1]),
                    }
                    for position in cast.moving_lines
                ],
                "palace": primary_relatives.palace,
                "palace_element": primary_relatives.palace_element,
                "primary_relatives": [line.label for line in primary_relatives.lines],
                "changed_relatives": [line.label for line in changed_relatives.lines],
            },
            "analysis": "",
            "answer": "",
            "verdict": "",
            "feedback": [],
            "status": "awaiting_analysis",
        }

    async def _save_case_analysis(
        self,
        event: Any,
        case_id: str,
        analysis: str,
    ) -> None:
        get_data = getattr(self, "get_kv_data", None)
        put_data = getattr(self, "put_kv_data", None)
        if not callable(get_data) or not callable(put_data):
            return
        group_id = str(event.get_group_id() or "").strip()
        try:
            async with self._case_lock_instance():
                payload = await get_data(LIUYAO_CASES_KEY, {})
                cases = normalize_case_store(payload)
                matched = False
                for case in cases:
                    if (
                        str(case.get("id") or "") == case_id
                        and str(case.get("group_id") or "") == group_id
                    ):
                        case["analysis"] = analysis
                        case["verdict"] = extract_verdict(analysis)
                        case["updated_at"] = datetime.now().astimezone().isoformat()
                        case["status"] = "feedback_recorded" if case.get("feedback") else "analyzed"
                        matched = True
                        break
                if not matched:
                    logger.warning("liuyao：待写入分析的卦例不存在：%s", case_id)
                    return
                await put_data(LIUYAO_CASES_KEY, serialize_case_store(cases, payload))
            logger.info("liuyao：已自动保存卦例分析 %s", case_id)
        except Exception as exc:
            logger.exception("liuyao：保存卦例分析失败（%s）：%r", type(exc).__name__, exc)

    async def _append_case_feedback(
        self,
        event: Any,
        *,
        case_id: str,
        feedback: str,
        outcome: str,
        answer: str = "",
        shefu_only: bool = False,
        reviewed_valid: bool | None = None,
        review_reason: str = "",
        review_command: dict[str, Any] | None = None,
    ) -> str:
        if not bool(self._config_get("case_library_enabled", True)):
            return "卦例库已由配置关闭。"
        get_data = getattr(self, "get_kv_data", None)
        put_data = getattr(self, "put_kv_data", None)
        if not callable(get_data) or not callable(put_data):
            return "AstrBot 持久化接口不可用，反馈未保存。"
        if reviewed_valid is False:
            return (
                f"反馈未通过：{review_reason or '请补充与原问题相关的真实进展或结果'}。"
                "尚未记录反馈，请用 /反馈 查看原编号并补充重试。"
            )
        group_id = str(event.get_group_id() or "").strip()
        caster_id = str(event.get_sender_id() or "").strip()
        try:
            async with self._case_lock_instance():
                payload = await get_data(LIUYAO_CASES_KEY, {})
                cases = normalize_case_store(payload)
                eligible = [
                    case
                    for case in cases
                    if str(case.get("group_id") or "") == group_id
                    and str(case.get("caster_id") or "") == caster_id
                    and (not shefu_only or case.get("intent") == "shefu")
                ]
                if case_id:
                    eligible = [
                        case
                        for case in eligible
                        if str(case.get("id") or "").lower() == case_id.lower()
                        or (case_id.isdigit() and case.get("serial") == int(case_id))
                    ]
                elif shefu_only:
                    eligible = [
                        case
                        for case in eligible
                        if not case.get("answer") and not case.get("feedback")
                    ]
                    if len(eligible) > 1:
                        numbers = "、".join(f"{case['serial']:03d}" for case in eligible)
                        return f"存在多条待揭晓射覆卦例（{numbers}），请发送：射覆答案 编号 物品。"
                if not eligible:
                    return "未找到当前用户在本群可更新的对应卦例。"
                case = max(
                    eligible,
                    key=lambda item: str(item.get("created_at") or ""),
                )
                rejected_rows = [
                    dict(item)
                    for item in case.get("feedback", [])
                    if isinstance(item, dict) and item.get("valid") is False
                ]
                feedback_rows = [
                    dict(item)
                    for item in case.get("feedback", [])
                    if isinstance(item, dict) and item.get("valid") is not False
                ]
                if feedback_rows:
                    return f"卦例 {case['serial']:03d} 已反馈过，每个卦例只能反馈一次。"
                observed_at = datetime.now().astimezone().isoformat()
                feedback_item = {
                    "observed_at": observed_at,
                    "outcome": outcome,
                    "text": feedback,
                    "answer": answer,
                    "reporter_id": caster_id,
                }
                if reviewed_valid is not None:
                    feedback_item["valid"] = reviewed_valid
                    feedback_item["review_reason"] = review_reason[:300]
                if review_command is not None:
                    feedback_item["review_command"] = review_command
                if rejected_rows:
                    case["legacy_rejected_feedback"] = rejected_rows
                feedback_rows.append(feedback_item)
                case["feedback"] = feedback_rows[-20:]
                if answer and case.get("intent") == "shefu":
                    case["answer"] = answer
                case["updated_at"] = observed_at
                case["status"] = "feedback_recorded"
                updated_payload = serialize_case_store(cases, payload)
                if reviewed_valid is True:
                    self._record_valid_feedback_event(
                        updated_payload, cases, caster_id, observed_at
                    )
                    scores = updated_payload.get("user_scores", {})
                    if not isinstance(scores, dict):
                        scores = {}
                    scores = dict(scores)
                    try:
                        current_score = max(0, int(scores.get(caster_id, 0)))
                    except (TypeError, ValueError):
                        current_score = 0
                    scores[caster_id] = current_score + 1
                    updated_payload["user_scores"] = scores
                await put_data(LIUYAO_CASES_KEY, updated_payload)
            if answer and case.get("intent") == "shefu":
                score_note = "AI 审核有效，已记入积分。" if reviewed_valid else ""
                return f"已记录射覆卦例 {case['serial']:03d}，用户揭晓答案：{answer}。{score_note}"
            if reviewed_valid is True:
                score_note = (
                    f"AI 审核通过，卦例 {case['serial']} 已闭环，累计有效反馈 +1。"
                    "可发送 /反馈 查看剩余队列及奖励进度。"
                )
            elif reviewed_valid is False:
                score_note = f"AI 审核未判为有效：{review_reason or '未提供可核验的实际进展'}。"
            else:
                score_note = ""
            return f"已将反馈写入卦例 {case['id']}（{outcome}）。{score_note}"
        except Exception as exc:
            logger.exception("liuyao：保存卦例反馈失败（%s）：%r", type(exc).__name__, exc)
            return f"卦例反馈保存失败（{type(exc).__name__}）。"

    async def _load_case_records(self) -> list[dict[str, Any]]:
        if not bool(self._config_get("case_library_enabled", True)):
            return []
        get_data = getattr(self, "get_kv_data", None)
        if not callable(get_data):
            return []
        try:
            return normalize_case_store(await get_data(LIUYAO_CASES_KEY, {}))
        except Exception as exc:
            logger.warning("liuyao：读取卦例库失败（%s）：%r", type(exc).__name__, exc)
            return []

    @staticmethod
    def _record_valid_feedback_event(
        payload: dict[str, Any],
        cases: list[dict[str, Any]],
        caster_id: str,
        observed_at: str,
    ) -> None:
        ledger = payload.get("valid_feedback_events", {})
        ledger = dict(ledger) if isinstance(ledger, dict) else {}
        existing = ledger.get(caster_id)
        if isinstance(existing, list):
            values = [str(value) for value in existing]
            # One locked successful case closure is one event. Distinct cases
            # can have the same clock timestamp, especially on Windows.
            values.append(observed_at)
        else:
            values = []
            for case in cases:
                feedback_rows = case.get("feedback", [])
                if not isinstance(feedback_rows, list):
                    continue
                for feedback in feedback_rows:
                    if not isinstance(feedback, dict) or feedback.get("valid") is not True:
                        continue
                    reporter_id = str(
                        feedback.get("reporter_id") or case.get("caster_id") or ""
                    ).strip()
                    timestamp = str(feedback.get("observed_at") or "").strip()
                    if reporter_id == caster_id and timestamp:
                        values.append(timestamp)
            if observed_at not in values:
                values.append(observed_at)

        now = datetime.now().astimezone()
        cutoff = now - timedelta(days=3650)
        recent_values = []
        for value in values:
            try:
                timestamp = datetime.fromisoformat(value)
            except ValueError:
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=now.tzinfo)
            if cutoff <= timestamp.astimezone(now.tzinfo) <= now:
                recent_values.append(value)
        ledger[caster_id] = recent_values
        payload["valid_feedback_events"] = ledger

    def _case_lock_instance(self) -> asyncio.Lock:
        lock = getattr(self, "_case_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._case_lock = lock
        return lock

    def _agent_run_key(self, event: Any) -> str:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        message_id = str(self._raw_get(raw, "message_id") or "")
        return "|".join(
            (
                str(getattr(event, "unified_msg_origin", "") or ""),
                str(event.get_sender_id() or ""),
                message_id,
            )
        )

    def _bounded_config_int(
        self,
        key: str,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        try:
            value = int(self._config_get(key, default) or default)
        except (TypeError, ValueError):
            value = default
        return min(max(value, minimum), maximum)

    @staticmethod
    def _clean_case_text(value: str, limit: int) -> str:
        text = str(value or "").replace("\x00", "").replace("\r\n", "\n")
        text = "\n".join(line.rstrip() for line in text.split("\n")).strip()
        return text[:limit]

    @staticmethod
    def _normalize_case_outcome(value: str) -> str:
        text = str(value or "").strip().lower()
        if any(token in text for token in ("部分", "一半", "part")):
            return "部分应验"
        if any(token in text for token in ("未应验", "没应验", "相反", "不准", "fail")):
            return "未应验"
        if any(token in text for token in ("应验", "符合", "实现", "成了", "fulfilled")):
            return "应验"
        if any(token in text for token in ("进行", "尚未", "等待", "pending")):
            return "进行中"
        return "未分类"
