from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_permissions import _Event, _make_enabled_plugin

from case_library import normalize_case_store
from divination import CastResult
from main import LiuyaoPlugin

QUESTION = "这个项目能否按期完成"
ANALYSIS = "先落实合同条款。\n断语：条件落实后可按期完成。"


def prepare(*, replies=None, limit=0, library=True):
    plugin = _make_enabled_plugin()
    plugin.config.update(ai_daily_limit=limit, case_library_enabled=library)
    plugin.context = SimpleNamespace(
        get_current_chat_provider_id=AsyncMock(return_value="plugin-model"),
        llm_generate=AsyncMock(
            side_effect=[
                SimpleNamespace(completion_text=value, role="assistant")
                for value in (
                    replies
                    if replies is not None
                    else ['{"reject":false,"intent":"事业"}', ANALYSIS]
                )
            ]
        ),
    )
    plugin._render_and_send_chart = AsyncMock(return_value="已发送到当前会话")
    plugin._send_reply_payload = AsyncMock()
    plugin._send_caster_reply = AsyncMock()
    event = _Event("member")
    event.message_obj.message_id = "first"
    return plugin, event


def test_tool_and_command_use_identical_models_prompts_delivery_and_archive(monkeypatch):
    monkeypatch.setattr("cast_policy.cast_instant", lambda: CastResult((7, 8, 9, 6, 7, 8)))
    monkeypatch.setattr("cast_policy.time.time", lambda: 1793152800.0)

    async def run():
        command, direct = prepare()
        tool, natural = prepare()
        assert await command._dispatch_liuyao(direct, QUESTION) == ""
        result = await tool.cast_liuyao_tool(
            natural, question=QUESTION, intent="射覆", agent_name="主 AI"
        )
        assert "本工具仅返回执行状态" in result
        assert "主 AI 不要自行解卦" in result
        assert ANALYSIS not in result and "本卦" not in result
        assert (
            tool.context.llm_generate.call_args_list == command.context.llm_generate.call_args_list
        )
        assert tool.context.llm_generate.await_count == 2
        for call in tool.context.llm_generate.call_args_list:
            assert set(call.kwargs) == {"chat_provider_id", "prompt"}
        assert tool._send_reply_payload.call_args.args[3] == ANALYSIS
        assert (
            tool._send_reply_payload.call_args.kwargs
            == command._send_reply_payload.call_args.kwargs
        )
        assert (
            tool._render_and_send_chart.call_args.kwargs
            == command._render_and_send_chart.call_args.kwargs
        )
        cases = normalize_case_store(tool._test_store["liuyao_case_library"])
        assert len(cases) == 1 and cases[0]["analysis"] == ANALYSIS
        assert cases[0]["verdict"] == "条件落实后可按期完成。"
        assert not tool._pending_agent_cases
        assert not hasattr(LiuyaoPlugin, "capture_liuyao_agent_analysis")

    asyncio.run(run())


@pytest.mark.parametrize(
    "review",
    [
        '{"reject":true,"reason":"伤害他人","intent":"综合"}',
        "invalid review",
    ],
)
def test_tool_review_rejection_or_failure_sends_chart_without_interpretation(review):
    async def run():
        plugin, event = prepare(replies=[review])
        result = await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert "自行解卦" in result
        assert plugin.context.llm_generate.await_count == 1
        plugin._render_and_send_chart.assert_awaited_once()
        assert (
            "不能提供" in plugin._send_reply_payload.call_args.args[3]
            or "预审暂不可用" in plugin._send_reply_payload.call_args.args[3]
        )
        assert "liuyao_case_library" not in plugin._test_store

    asyncio.run(run())


def test_tool_quota_exhaustion_still_sends_chart_without_model_or_reinterpretation():
    async def run():
        plugin, event = prepare(limit=1)
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        plugin.context.llm_generate.reset_mock()
        plugin._render_and_send_chart.reset_mock()
        plugin._send_reply_payload.reset_mock()
        event.message_obj.message_id = "second"
        result = await plugin.cast_liuyao_tool(event, question="明天能签约吗")
        assert "不要因未取得正文而再次调用起卦" in result
        plugin.context.llm_generate.assert_not_awaited()
        plugin._render_and_send_chart.assert_awaited_once()
        assert "额度已用完" in plugin._send_caster_reply.call_args.args[3]
        plugin._send_reply_payload.assert_not_awaited()
        assert plugin._test_store["cast_policy:20002"]["last"]["question"] == "明天能签约吗"

    asyncio.run(run())


def test_reuse_interprets_inside_plugin_keeps_snapshot_and_original_prediction():
    async def run():
        plugin, event = prepare()
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        original = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        old_cases = copy.deepcopy(plugin._test_store["liuyao_case_library"])
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text='{"reject":false,"intent":"事业"}'),
            SimpleNamespace(completion_text="等待合同。\n断语：先核对条款。"),
        ]
        event.message_obj.message_id = "follow-up"
        result = await plugin.reuse_liuyao_tool(event, question="客户已口头同意，下一步呢")
        assert "本工具仅返回执行状态" in result
        assert "本卦" not in result
        assert plugin.context.llm_generate.await_count == 4
        prompt = plugin.context.llm_generate.call_args.kwargs["prompt"]
        assert QUESTION in prompt and "客户已口头同意，下一步呢" in prompt
        assert plugin._test_store["cast_policy:20002"] == original
        assert plugin._test_store["liuyao_case_library"] == old_cases
        rendered_cast = plugin._render_and_send_chart.call_args.args[1]
        assert list(rendered_cast.lines) == original["last"]["lines"]
        assert rendered_cast.chart == original["last"]["chart"]
        assert rendered_cast.cast_at.timestamp() == pytest.approx(
            original["last"]["timestamp"], abs=1e-6
        )

    asyncio.run(run())


@pytest.mark.parametrize("branch", ["missing", "other_group", "closed"])
def test_reuse_denied_before_any_model_call(branch):
    async def run():
        plugin, event = prepare()
        if branch == "other_group":
            await plugin._prepare_cast(
                event, None, question=QUESTION, intent="career", method="测试"
            )
            event.get_group_id = lambda: "other"
            plugin._test_store["method_switches"]["other"] = {"liuyao": True}
        elif branch == "closed":
            event.get_group_id = lambda: "closed"
        result = await plugin.reuse_liuyao_tool(event)
        assert any(word in result for word in ("尚无", "回原群", "尚未开启"))
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


def test_cancellation_before_model_releases_shared_quota_reservation():
    async def run():
        plugin, event = prepare(limit=1)
        entered = asyncio.Event()

        async def block(*args):
            entered.set()
            await asyncio.Future()

        plugin._send_caster_reply.side_effect = block
        task = asyncio.create_task(plugin.cast_liuyao_tool(event, question=QUESTION))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        plugin._send_caster_reply.side_effect = None
        event.message_obj.message_id = "retry"
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert plugin.context.llm_generate.await_count == 2
        assert plugin._send_reply_payload.call_args.args[3] == ANALYSIS

    asyncio.run(run())


def test_reuse_can_archive_first_successful_interpretation_after_review_failure():
    async def run():
        plugin, event = prepare(replies=["invalid review"])
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        original = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        assert "liuyao_case_library" not in plugin._test_store
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text='{"reject":false,"intent":"事业"}'),
            SimpleNamespace(completion_text=ANALYSIS),
        ]
        event.message_obj.message_id = "retry"
        await plugin.reuse_liuyao_tool(event)
        cases = normalize_case_store(plugin._test_store["liuyao_case_library"])
        assert len(cases) == 1 and cases[0]["analysis"] == ANALYSIS
        assert plugin._test_store["cast_policy:20002"] == original

    asyncio.run(run())
