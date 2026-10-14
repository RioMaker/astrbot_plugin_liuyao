from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from test_permissions import _Event, _make_enabled_plugin

from case_library import normalize_case_store, search_cases, serialize_case_store
from case_store import LIUYAO_CASES_KEY
from divination import CastResult
from shefu import parse_shefu_answer


def run(awaitable):
    return asyncio.run(awaitable)


def create_case(plugin, event=None, intent="shefu"):
    return run(
        plugin._open_agent_case(
            event or _Event("member"),
            CastResult((9, 7, 7, 7, 7, 7)),
            intent=intent,
            question="盒子里是什么",
            method="手动铜币",
            cast_at=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc),
        )
    )[0]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("答案是：苹果", ("", "苹果")),
        ("射覆答案 001 红色橡皮球", ("001", "红色橡皮球")),
        ("揭晓：钥匙", ("", "钥匙")),
        ("谜底是红色的塑料杯", ("", "红色的塑料杯")),
        ("答案是什么？", None),
        ("我猜是苹果", None),
        ("答案是可能是钥匙", None),
        ("/六爻 卦例 001", None),
    ],
)
def test_explicit_answer_parser(text, expected):
    assert parse_shefu_answer(text) == expected


def test_listener_records_only_caster_and_group_and_survives_restart():
    plugin = _make_enabled_plugin()
    create_case(plugin)
    wrong_user = _Event("member")
    wrong_user.get_sender_id = lambda: "another-user"
    wrong_user.message_str = "答案是苹果"
    assert run(plugin._collect_shefu_answer(wrong_user)) == ""
    wrong_group = _Event("member")
    wrong_group.get_group_id = lambda: "another-group"
    wrong_group.message_str = "答案是苹果"
    assert run(plugin._collect_shefu_answer(wrong_group)) == ""
    # Mimic an independent event after plugin restart; no pending memory required.
    plugin._pending_agent_cases = {}
    answer = _Event("member")
    answer.message_str = "答案是：苹果"

    async def listen():
        return [result async for result in plugin.collect_shefu_answer(answer)]

    messages = run(listen())
    assert "001" in messages[0]["text"]
    saved = plugin._test_store[LIUYAO_CASES_KEY]["cases"][0]
    assert saved["answer"] == "苹果"
    assert saved["feedback"][0]["text"] == answer.message_str
    assert saved["feedback"][0]["outcome"] == "未分类"
    assert run(plugin._collect_shefu_answer(answer)) == ""
    assert len(saved["feedback"]) == 1


def test_multiple_unrevealed_cases_require_number_and_preserve_predictions():
    plugin = _make_enabled_plugin()
    event = _Event("member")
    first = create_case(plugin, event)
    create_case(plugin, event)
    run(plugin._save_case_analysis(event, first, "颜色：白色\n断语：首选橡皮。"))
    event.message_str = "答案是苹果"
    assert "多条待揭晓" in run(plugin._collect_shefu_answer(event))
    assert all(not c["answer"] for c in plugin._test_store[LIUYAO_CASES_KEY]["cases"])
    event.message_str = "射覆答案 001 苹果"
    assert "001" in run(plugin._collect_shefu_answer(event))
    saved = plugin._test_store[LIUYAO_CASES_KEY]["cases"]
    assert saved[0]["answer"] == "苹果" and saved[1]["answer"] == ""
    assert saved[0]["verdict"] == "首选橡皮。"
    # Duplicated group delivery cannot duplicate feedback.
    run(plugin._collect_shefu_answer(event))
    assert len(plugin._test_store[LIUYAO_CASES_KEY]["cases"][0]["feedback"]) == 1


@pytest.mark.parametrize("role", ["owner", "admin", "member"])
def test_browser_requires_astrbot_admin_even_for_group_operators(role):
    plugin = _make_enabled_plugin()
    create_case(plugin)
    event = _Event(role)
    assert "仅 AstrBot 管理员" in run(plugin._dispatch_liuyao(event, "卦例"))
    assert "仅 AstrBot 管理员" in run(plugin._dispatch_liuyao(event, "卦例 001"))
    assert event.sent == []


def test_browser_lists_and_reconstructs_original_chart_even_when_group_disabled(tmp_path):
    plugin = _make_enabled_plugin()
    case_id = create_case(plugin)
    caster = _Event("member")
    run(
        plugin._append_case_feedback(
            caster,
            case_id=case_id,
            feedback="答案是杯子",
            outcome="未分类",
            answer="杯子",
            shefu_only=True,
        )
    )
    plugin._test_store["method_switches"]["10001"]["liuyao"] = False
    kwargs_seen = {}

    class Renderer:
        def render(self, cast, **kwargs):
            assert cast.lines == (9, 7, 7, 7, 7, 7)
            kwargs_seen.update(kwargs)
            path = tmp_path / "archive.png"
            path.write_bytes(b"test-image")
            return path

    plugin.renderer = Renderer()
    admin = _Event("member", astrbot_admin=True)
    admin.get_sender_id = lambda: "administrator"
    assert "001" in run(plugin._dispatch_liuyao(admin, "卦例"))
    detail = run(plugin._dispatch_liuyao(admin, "卦例 001"))
    assert "用户揭晓答案：杯子" in detail and "用户反馈" in detail
    assert len(admin.sent) == 1
    assert kwargs_seen["caster_id"] == "20002"
    assert kwargs_seen["cast_at"].isoformat() == "2026-09-22T12:00:00+00:00"
    assert not (tmp_path / "archive.png").exists()
    plugin.renderer = None
    assert "原始排盘" in run(plugin._dispatch_liuyao(admin, "卦例 001"))


def test_number_migration_retention_and_parallel_creation():
    plugin = _make_enabled_plugin()
    create_case(plugin)
    legacy = copy.deepcopy(plugin._test_store[LIUYAO_CASES_KEY]["cases"][0])
    legacy.pop("serial")
    payload = {"cases": [legacy]}
    migrated = normalize_case_store(payload)
    assert migrated[0]["serial"] == 1
    assert normalize_case_store(serialize_case_store(migrated)) == migrated
    plugin._test_store[LIUYAO_CASES_KEY] = serialize_case_store(migrated)
    plugin.config["case_library_max_records"] = 20

    async def create_many():
        await asyncio.gather(
            *[
                plugin._open_agent_case(
                    _Event("member"),
                    CastResult((7,) * 6),
                    intent="shefu",
                    question="袋中何物",
                    method="测试",
                    cast_at=datetime.now(timezone.utc),
                )
                for _ in range(25)
            ]
        )

    run(create_many())
    payload = plugin._test_store[LIUYAO_CASES_KEY]
    assert [c["serial"] for c in payload["cases"]] == list(range(7, 27))
    assert payload["next_serial"] == 27


def test_shefu_agent_and_command_request_attributes_candidates_and_store_result(tmp_path):
    plugin = _make_enabled_plugin()
    prompts = []
    analysis = (
        "颜色：红色；材质：橡胶；软硬：软；形状：圆形；大小：手掌大；"
        "用途：玩具；关联信息：运动。\n候选物品：1.橡皮球；2.气球。\n断语：首选橡皮球。"
    )

    class Context:
        async def get_current_chat_provider_id(self, **kwargs):
            return "model"

        async def llm_generate(self, **kwargs):
            prompts.append(kwargs["prompt"])
            return SimpleNamespace(completion_text=analysis)

    captured = {}

    class Renderer:
        def render(self, cast, **kwargs):
            captured.update(kwargs)
            path = tmp_path / "new.png"
            path.write_bytes(b"image")
            return path

    plugin.context = Context()
    plugin.renderer = Renderer()
    event = _Event("member")
    assert run(plugin._dispatch_liuyao(event, "射覆 盒中何物")) == ""
    assert "候选物品" in prompts[0] and "软硬" in prompts[0]
    assert captured["ai_comment"] == analysis
    record = plugin._test_store[LIUYAO_CASES_KEY]["cases"][0]
    assert record["intent"] == "shefu" and record["analysis"] == analysis
    assert not plugin._pending_agent_cases
    context = run(plugin.cast_liuyao_tool(event, intent="射覆", question="袋中何物"))
    assert "2–4 个具体候选物品" in context and "颜色" in context
    assert "真实答案" in context  # Historical shefu cases provided.


def test_shefu_reference_library_excludes_other_directions():
    plugin = _make_enabled_plugin()
    create_case(plugin, intent="career")
    create_case(plugin, intent="shefu")
    cases = plugin._test_store[LIUYAO_CASES_KEY]["cases"]
    assert [c["serial"] for c in search_cases(cases, group_id="10001", intent="shefu")] == [2]


def test_disabled_collection_and_unauthorized_feedback_do_not_write():
    plugin = _make_enabled_plugin()
    case_id = create_case(plugin)
    other = _Event("admin", astrbot_admin=True)
    other.get_sender_id = lambda: "not-the-caster"
    result = run(
        plugin.record_liuyao_feedback_tool(
            other,
            feedback="答案是苹果",
            case_id=case_id,
            answer="苹果",
        )
    )
    assert "未找到" in result
    own = _Event("member")
    own.message_str = "答案是苹果"
    plugin.config["case_library_enabled"] = False
    assert run(plugin._collect_shefu_answer(own)) == ""
    assert plugin._test_store[LIUYAO_CASES_KEY]["cases"][0]["answer"] == ""


def test_admin_identity_errors_deny_access_without_reading_archive():
    plugin = _make_enabled_plugin()
    event = _Event("owner")

    def broken():
        raise RuntimeError("identity check failed")

    event.is_admin = broken

    async def forbidden(*args):
        raise AssertionError("archive must not be accessed")

    plugin.get_kv_data = forbidden
    assert "仅 AstrBot 管理员" in run(plugin._case_browser(event, "001"))


def test_browser_never_exposes_other_group_even_with_cross_group_references():
    plugin = _make_enabled_plugin()
    create_case(plugin)
    plugin.config["case_library_cross_group"] = True
    admin = _Event("member", astrbot_admin=True)
    admin.get_group_id = lambda: "other-group"
    assert "0 条" in run(plugin._case_browser(admin, ""))
    assert "未找到" in run(plugin._case_browser(admin, "001"))


def test_answer_before_analysis_keeps_feedback_status():
    plugin = _make_enabled_plugin()
    event = _Event("member")
    case_id = create_case(plugin, event)
    event.message_str = "答案是苹果"
    run(plugin._collect_shefu_answer(event))
    run(plugin._save_case_analysis(event, case_id, "断语：首选苹果。"))
    case = plugin._test_store[LIUYAO_CASES_KEY]["cases"][0]
    assert case["status"] == "feedback_recorded" and case["answer"] == "苹果"


def test_shefu_model_failure_still_keeps_original_cast():
    plugin = _make_enabled_plugin()
    result = run(plugin._dispatch_liuyao(_Event("member"), "手动 乾 射覆 盒中何物"))
    assert "分析未生成" in result
    case = plugin._test_store[LIUYAO_CASES_KEY]["cases"][0]
    assert case["cast"]["lines"] == [7] * 6
    assert case["intent"] == "shefu" and case["answer"] == ""
