from __future__ import annotations

from case_library import (
    extract_verdict,
    format_case_references,
    normalize_case_store,
    search_cases,
    serialize_case_store,
    trim_cases,
)


def _case(
    case_id: str,
    *,
    group_id: str = "10001",
    question: str,
    intent: str,
    primary: int,
    changed: int,
    feedback: bool = False,
) -> dict:
    return {
        "id": case_id,
        "group_id": group_id,
        "caster_id": "20002",
        "created_at": f"2026-08-0{case_id[-1]}T10:00:00+08:00",
        "updated_at": f"2026-08-0{case_id[-1]}T10:00:00+08:00",
        "question": question,
        "intent": intent,
        "cast": {
            "primary_number": primary,
            "primary_name": "测试本卦",
            "changed_number": changed,
            "changed_name": "测试之卦",
            "moving_lines": [2],
        },
        "analysis": "依据卦爻辞形成的分析",
        "verdict": "此事可成",
        "feedback": (
            [{"outcome": "应验", "text": "最终如期完成"}] if feedback else []
        ),
    }


def test_case_search_prefers_same_hexagram_intent_and_feedback() -> None:
    cases = [
        _case(
            "LY-1",
            question="换工作能否成功",
            intent="career",
            primary=1,
            changed=44,
            feedback=True,
        ),
        _case(
            "LY-2",
            question="感情能否复合",
            intent="relationship",
            primary=2,
            changed=23,
        ),
        _case(
            "LY-3",
            group_id="other-group",
            question="换工作能否成功",
            intent="career",
            primary=1,
            changed=44,
            feedback=True,
        ),
    ]
    matches = search_cases(
        cases,
        group_id="10001",
        query="最近换工作会成功吗",
        intent="career",
        primary_number=1,
        changed_number=44,
        moving_lines=[2],
        caster_id="20002",
        limit=3,
    )
    assert [item["id"] for item in matches] == ["LY-1", "LY-2"]


def test_case_store_round_trip_trim_and_verdict_extraction() -> None:
    cases = [
        _case(
            f"LY-{index}",
            question="测试问题",
            intent="general",
            primary=1,
            changed=1,
        )
        for index in range(1, 26)
    ]
    payload = serialize_case_store(cases)
    assert normalize_case_store(payload) == cases
    assert len(trim_cases(cases, 20)) == 20
    assert extract_verdict("分析正文\n断语：此事不成，宜止。") == "此事不成，宜止。"


def test_formatted_references_omit_identity_and_limit_analysis() -> None:
    case = _case(
        "LY-1",
        question="项目能否落地",
        intent="career",
        primary=1,
        changed=44,
        feedback=True,
    )
    case["caster_name"] = "不应暴露的昵称"
    case["analysis"] = "很长的分析" * 200
    case["verdict"] = ""
    text = format_case_references([case])
    assert "卦例 LY-1" in text
    assert "最终如期完成" in text
    assert "不应暴露的昵称" not in text
    assert len(text) < 1000
