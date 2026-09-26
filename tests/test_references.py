from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_permissions import _Event, _make_enabled_plugin

from case_library import search_cases
from divination import CastResult, infer_intent, parse_intent_and_question
from reference_library import ReferenceLibrary

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("明天杭州会下雨吗", "weather"),
        ("今天成都是否转晴", "weather"),
        ("明天晴吗", "weather"),
        ("周末北京会下雪吗", "weather"),
        ("明天出差会遇上雷雨吗", "weather"),
        ("射覆：猜一件挡雨的物品", "shefu"),
        ("这段感情能风雨同舟吗", "relationship"),
        ("事业雪上加霜怎么办", "career"),
        ("风水如何", "general"),
        ("今天运气如何", "general"),
        ("明天时间能安排好吗", "general"),
    ],
)
def test_routing(text, expected):
    assert infer_intent(text) == expected


@pytest.mark.parametrize("alias", ["天气", "天时", "气象", "晴雨", "weather"])
def test_weather_aliases(alias):
    assert parse_intent_and_question(f"{alias} 明日杭州是否有雨") == (
        "weather",
        "明日杭州是否有雨",
    )


def test_all_directions_have_bounded_source_based_documents():
    plugin = _make_enabled_plugin()
    library = plugin.readings.references
    assert library.errors == {}
    assert set(library.documents) == {"common", *plugin.readings.directions}
    for key, profile in plugin.readings.directions.items():
        text = plugin.readings.reference_context(key)
        assert f"# 六爻分类参考：{profile['label']}" in text
        assert "# 六爻分类参考：通则" in text
        assert "https://" in library.documents[key]
        assert "2026-09-24" in text
        assert len(text) < 10_000
        for other, other_profile in plugin.readings.directions.items():
            if other != key:
                assert f"# 六爻分类参考：{other_profile['label']}" not in text


def test_missing_invalid_and_untrusted_paths_fail_soft(tmp_path):
    (tmp_path / "common.md").write_text("通则", encoding="utf-8")
    (tmp_path / "general.md").write_text("综合内容", encoding="utf-8")
    (tmp_path / "weather.md").write_bytes(b"\xff")
    library = ReferenceLibrary(tmp_path, {"general": {}, "weather": {}, "../../secret": {}})
    assert "weather" in library.errors
    assert "../../secret" in library.errors
    assert "weather.md 未加载" in library.render("weather")
    assert "综合内容" in library.render("../../../private")
    assert "../../../private" not in library.render("../../../private")
    (tmp_path / "weather.md").write_text("天" * 15_000, encoding="utf-8")
    assert "weather" in ReferenceLibrary(tmp_path, {"weather": {}}).errors


def test_weather_context_has_no_generic_success_failure_instruction():
    plugin = _make_enabled_plugin()
    text = plugin.readings.render(
        CastResult((7, 8, 9, 6, 7, 8)),
        intent="天时",
        question="明日杭州是否有雨",
        method="测试",
        for_agent=True,
    )
    assert "# 六爻分类参考：天气" in text
    assert "文献分歧" in text
    assert "日月干支" in text and "未计算" in text
    assert "开头先明确回答成或不成" not in text
    assert "动爻：" in text and "本卦六亲" in text
    plain = plugin.readings.render(
        CastResult((7,) * 6),
        intent="weather",
        question="",
        method="测试",
    )
    assert "分类研判参考｜" not in plain


@pytest.mark.parametrize(
    "payload",
    [
        "明日杭州是否下雨",
        "天气 明日杭州是否有雨",
        "即时 天时 明日杭州是否有雨",
        "手动 乾 天气 明日杭州是否有雨",
        "手动 7 8 9 6 7 8 明日杭州是否下雨",
    ],
)
def test_command_weather_fallback(payload):
    plugin = _make_enabled_plugin()
    text = asyncio.run(plugin._dispatch_liuyao(_Event("member"), payload))
    assert "意图：天气" in text
    assert "排盘图：" in text
    assert "# 六爻分类参考" not in text


def test_explicit_concrete_intent_is_preserved():
    assert parse_intent_and_question("出行 明日暴雨会影响出差吗")[0] == "travel"
    plugin = _make_enabled_plugin()
    plugin.config["agent_generate_chart_comment"] = False
    result = asyncio.run(
        plugin.cast_liuyao_tool(
            _Event("member"),
            intent="出行",
            question="明日暴雨会影响出差吗",
        )
    )
    assert "# 六爻分类参考：出行" in result
    assert "# 六爻分类参考：天气" not in result


def test_agent_weather_image_reference_and_case_roundtrip(tmp_path):
    plugin = _make_enabled_plugin()
    event = _Event("member")
    prompts = []
    rendered = []

    class Context:
        async def get_current_chat_provider_id(self, **kwargs):
            return "mock"

        async def llm_generate(self, **kwargs):
            prompts.append(kwargs["prompt"])
            return SimpleNamespace(
                completion_text=json.dumps(
                    {
                        "intent": "事业",
                        "comment": "测试短评：先核实天气变化。",
                    },
                    ensure_ascii=False,
                )
            )

    class Renderer:
        def render(self, cast, **kwargs):
            rendered.append(kwargs)
            path = tmp_path / "weather.png"
            path.write_bytes(b"mock-image")
            return path

    plugin.context = Context()
    plugin.renderer = Renderer()
    text = asyncio.run(
        plugin.cast_liuyao_tool(
            event,
            mode="manual",
            manual_lines="乾",
            question="明天杭州会下雨吗",
        )
    )
    assert len(event.sent) == 1
    assert rendered[0]["intent_label"].startswith("天气")
    assert "# 六爻分类参考：天气" in prompts[0]
    assert "本卦六亲" in prompts[0]
    assert "# 六爻分类参考：天气" in text
    assert "按已返回的天气参考研判" in text
    assert "先明确断成败" not in text
    assert not (tmp_path / "weather.png").exists()
    record = plugin._test_store["liuyao_case_library"]["cases"][0]
    assert record["intent"] == "weather"
    original = "本地测试分析。\n断语：明天杭州以阴天为主。"
    asyncio.run(plugin._save_case_analysis(event, record["id"], original))
    reply = asyncio.run(
        plugin.record_liuyao_feedback_tool(
            event,
            case_id=record["id"],
            feedback="次日杭州上午实测阴天无雨。",
            outcome="应验",
        )
    )
    assert "已将反馈写入" in reply
    saved = plugin._test_store["liuyao_case_library"]["cases"][0]
    assert saved["analysis"] == original
    assert "实测" in saved["feedback"][0]["text"]
    assert "仅 AstrBot 管理员" in asyncio.run(plugin._case_browser(event, "天气"))
    admin = _Event("member", astrbot_admin=True)
    assert "本群天气卦例清单｜1 条" in asyncio.run(plugin._case_browser(admin, "天气"))
    assert "0 条" in asyncio.run(plugin._case_browser(admin, "射覆"))
    other = dict(saved, id="career-example", intent="career")
    assert [
        r["id"]
        for r in search_cases(
            [saved, other],
            group_id="10001",
            intent="weather",
            primary_number=1,
        )
    ] == [saved["id"]]


def test_weather_missing_scope_and_reference_still_returns_cast(tmp_path):
    plugin = _make_enabled_plugin()
    plugin.config["agent_generate_chart_comment"] = False
    plugin.readings.references = ReferenceLibrary(tmp_path, plugin.readings.directions)
    text = asyncio.run(
        plugin.cast_liuyao_tool(_Event("member"), intent="天气", question="明天是否下雨")
    )
    assert "本卦：" in text
    assert "weather.md 未加载" in text
    assert "缺失先询问" in text


def test_weather_command_success_is_image_only(tmp_path):
    plugin = _make_enabled_plugin()
    captured = []

    class Renderer:
        def render(self, cast, **kwargs):
            captured.append(kwargs)
            path = tmp_path / "command.png"
            path.write_bytes(b"mock-image")
            return path

    plugin.renderer = Renderer()
    event = _Event("member")
    assert asyncio.run(plugin._dispatch_liuyao(event, "明天杭州会下雨吗")) == ""
    assert len(event.sent) == 1 and captured[0]["intent_label"] == "天气"
    assert not plugin._pending_agent_cases


@pytest.mark.parametrize(
    "intent",
    [
        "general",
        "career",
        "relationship",
        "wealth",
        "study",
        "health",
        "family",
        "travel",
        "shefu",
        "weather",
    ],
)
def test_each_agent_direction_returns_its_document(intent):
    plugin = _make_enabled_plugin()
    plugin.config["agent_generate_chart_comment"] = False
    text = asyncio.run(
        plugin.cast_liuyao_tool(_Event("member"), intent=intent, question="这件安排能否按期完成")
    )
    label = plugin.readings.directions[intent]["label"]
    assert f"# 六爻分类参考：{label}" in text
    assert "# 六爻分类参考：通则" in text
