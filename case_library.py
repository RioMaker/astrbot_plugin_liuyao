"""Persistent Liuyao case records and deterministic relevance matching."""

from __future__ import annotations

import re
from typing import Any, Iterable


CASE_SCHEMA_VERSION = 1


def normalize_case_store(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        values = payload.get("cases", [])
    elif isinstance(payload, list):
        values = payload
    else:
        values = []
    return [dict(item) for item in values if isinstance(item, dict) and item.get("id")]


def serialize_case_store(cases: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": CASE_SCHEMA_VERSION, "cases": list(cases)}


def trim_cases(cases: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    maximum = max(20, min(int(maximum), 5000))
    return cases[-maximum:]


def search_cases(
    cases: Iterable[dict[str, Any]],
    *,
    group_id: str,
    query: str = "",
    intent: str = "",
    primary_number: int = 0,
    changed_number: int = 0,
    moving_lines: Iterable[int] = (),
    caster_id: str = "",
    limit: int = 3,
    cross_group: bool = False,
) -> list[dict[str, Any]]:
    query_tokens = _tokens(query)
    wanted_moving = {_safe_int(value) for value in moving_lines if _safe_int(value)}
    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for case in cases:
        if not cross_group and str(case.get("group_id") or "") != str(group_id):
            continue
        score = 0.0
        cast = case.get("cast") if isinstance(case.get("cast"), dict) else {}
        if caster_id and str(case.get("caster_id") or "") == str(caster_id):
            score += 1.5
        if intent and str(case.get("intent") or "") == intent:
            score += 5.0
        if primary_number and _safe_int(cast.get("primary_number")) == primary_number:
            score += 9.0
        if changed_number and _safe_int(cast.get("changed_number")) == changed_number:
            score += 3.0
        existing_moving = {
            _safe_int(value)
            for value in cast.get("moving_lines", [])
            if _safe_int(value)
        }
        if wanted_moving and existing_moving:
            score += min(3.0, len(wanted_moving & existing_moving) * 1.5)

        haystack = " ".join(
            [
                str(case.get("question") or ""),
                str(case.get("analysis") or ""),
                str(case.get("verdict") or ""),
                " ".join(
                    str(item.get("text") or "")
                    for item in case.get("feedback", [])
                    if isinstance(item, dict)
                ),
            ]
        )
        if query_tokens:
            overlap = query_tokens & _tokens(haystack)
            score += min(8.0, len(overlap) * 0.8)
        if case.get("analysis"):
            score += 0.5
        if case.get("feedback"):
            score += 2.0
        if not any((query_tokens, intent, primary_number, changed_number, wanted_moving)):
            score += 1.0
        if score <= 0:
            continue
        ranked.append((score, str(case.get("updated_at") or ""), case))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [dict(item[2]) for item in ranked[: max(1, min(int(limit), 10))]]


def extract_verdict(analysis: str) -> str:
    text = str(analysis or "").strip()
    matches = re.findall(r"(?:^|\n)\s*断语\s*[：:]\s*([^\n]+)", text)
    if matches:
        return matches[-1].strip()[:300]
    return ""


def format_case_references(cases: Iterable[dict[str, Any]]) -> str:
    rows: list[str] = []
    for case in cases:
        cast = case.get("cast") if isinstance(case.get("cast"), dict) else {}
        feedback = [
            item
            for item in case.get("feedback", [])
            if isinstance(item, dict) and item.get("text")
        ]
        evidence = "；".join(
            [
                str(cast.get("primary_judgment") or ""),
                *[
                    str(item.get("text") or "")
                    for item in cast.get("moving_texts", [])
                    if isinstance(item, dict)
                ],
                str(cast.get("changed_judgment") or ""),
            ]
        ).strip("；")
        rows.extend(
            [
                f"卦例 {case.get('id', '未知')}｜{case.get('created_at', '')}",
                f"原问：{case.get('question') or '未填写'}",
                f"方向：{case.get('intent_label') or case.get('intent') or '未分类'}",
                (
                    f"卦象：第{cast.get('primary_number', '?')}卦"
                    f" {cast.get('primary_name', '')} → 第{cast.get('changed_number', '?')}卦"
                    f" {cast.get('changed_name', '')}；动爻："
                    f"{cast.get('moving_lines') or '无'}"
                ),
                f"卦爻据：{_short(evidence, 300) if evidence else '未记录'}",
                "原断："
                + _short(
                    str(
                        case.get("verdict")
                        or case.get("analysis")
                        or "尚未记录"
                    ),
                    350,
                ),
                (
                    "反馈："
                    + "；".join(
                        f"{item.get('outcome', '未分类')}—{item.get('text', '')}"
                        for item in feedback[-3:]
                    )
                    if feedback
                    else "反馈：尚无"
                ),
            ]
        )
    return "\n".join(rows)


def _tokens(value: str) -> set[str]:
    text = re.sub(r"\s+", "", str(value or "").lower())
    tokens = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2}", text))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    tokens.update(chinese[index : index + 2] for index in range(len(chinese) - 1))
    return {token for token in tokens if token not in _STOP_TOKENS}


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _short(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit] + "……"


_STOP_TOKENS = {
    "什么",
    "怎么",
    "如何",
    "是否",
    "可以",
    "这个",
    "那个",
    "现在",
    "以后",
    "结果",
    "事情",
}
