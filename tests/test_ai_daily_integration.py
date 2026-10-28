from __future__ import annotations

import asyncio
import copy
import time
from types import SimpleNamespace

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_permissions import _Event


def used_count(plugin):
    return sum(
        item["used"]
        for key, record in plugin._test_store.items()
        if key.startswith("ai_daily_quota_v1:")
        for entries in record["days"].values()
        for item in entries.values()
    )


def test_cast_review_and_interpretation_share_one_message_charge():
    async def run():
        plugin, event = prepare(limit=1)
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert used_count(plugin) == 1
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text='{"reject":false,"intent":"事业"}'),
            SimpleNamespace(completion_text=ANALYSIS),
        ]
        await plugin.reuse_liuyao_tool(event)
        assert used_count(plugin) == 1
        event.message_obj.message_id = "new-message"
        before = plugin.context.llm_generate.await_count
        await plugin.reuse_liuyao_tool(event)
        assert used_count(plugin) == 1 and plugin.context.llm_generate.await_count == before
        assert "额度已用完" in plugin._send_caster_reply.call_args.args[3]

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["empty", "exception", "error_role"])
def test_interpretation_failure_is_charged_but_never_saved_as_prediction(failure):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text='{"reject":false,"intent":"事业"}'),
            RuntimeError("model failure")
            if failure == "exception"
            else SimpleNamespace(
                completion_text="" if failure == "empty" else "Output stopped.",
                role="err" if failure == "error_role" else "assistant",
            ),
        ]
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert used_count(plugin) == 1
        assert plugin.context.llm_generate.await_count == 2
        assert "liuyao_case_library" not in plugin._test_store
        assert (
            "失败" in plugin._send_reply_payload.call_args.args[3]
            or "未能返回" in plugin._send_reply_payload.call_args.args[3]
        )

    asyncio.run(run())


def test_empty_provider_sends_chart_without_consuming_or_calling_ai():
    async def run():
        plugin, event = prepare(limit=1)
        plugin.context.get_current_chat_provider_id.return_value = ""
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        plugin.context.llm_generate.assert_not_awaited()
        assert used_count(plugin) == 0
        plugin._render_and_send_chart.assert_awaited_once()

    asyncio.run(run())


def test_quota_storage_failure_does_not_call_a_model():
    async def run():
        plugin, event = prepare(limit=1)
        original = plugin.put_kv_data

        async def unavailable(key, value):
            if key.startswith("ai_daily_quota_v1:"):
                raise OSError("storage unavailable")
            await original(key, value)

        plugin.put_kv_data = unavailable
        result = await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert "本工具仅返回执行状态" in result
        assert "次数记录读取或保存失败" in plugin._send_caster_reply.call_args.args[3]
        plugin.context.llm_generate.assert_not_awaited()
        plugin._render_and_send_chart.assert_awaited_once()

    asyncio.run(run())


def test_concurrent_tools_reserve_only_one_ai_use_but_all_still_send_charts():
    async def run():
        plugin, _ = prepare(limit=1, library=False)
        events = [_Event("member") for _ in range(6)]
        for index, event in enumerate(events):
            event.message_obj.message_id = str(index)
        await asyncio.gather(
            *[
                plugin.cast_liuyao_tool(event, question=f"第{index}个项目能否完成")
                for index, event in enumerate(events)
            ]
        )
        assert used_count(plugin) == 1
        assert plugin.context.llm_generate.await_count == 2
        assert plugin._render_and_send_chart.await_count == 6
        assert plugin._send_reply_payload.await_count == 1
        assert (
            sum("额度已用完" in call.args[3] for call in plugin._send_caster_reply.call_args_list)
            == 5
        )

    asyncio.run(run())


def test_manual_and_tool_casts_ignore_old_rolling_frequency_and_share_daily_quota():
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update(cast_max_per_window=1, cast_cooldown_seconds=3600)
        plugin._test_store["cast_policy:20002"] = {"timestamps": [time.time()]}
        await plugin._dispatch_liuyao(event, QUESTION)
        original = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        event.message_obj.message_id = "second"
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert plugin._render_and_send_chart.await_count == 2
        assert plugin.context.llm_generate.await_count == 2
        assert used_count(plugin) == 1
        assert plugin._test_store["cast_policy:20002"]["timestamps"] == original["timestamps"]

    asyncio.run(run())


def test_history_tool_returns_stored_prediction_without_ai_use_or_self_interpretation():
    async def run():
        plugin, event = prepare(limit=1)
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        cases = plugin._test_store["liuyao_case_library"]["cases"]
        cases[0]["feedback"] = [{"text": "项目已按期完成", "valid": True}]
        event.message_obj.message_id = "history-query"
        before = copy.deepcopy(plugin._test_store)
        calls = plugin.context.llm_generate.await_count
        result = await plugin.search_liuyao_cases_tool(event, query="项目")
        assert "找到以下历史卦例" in result and "主 AI 不自行解卦或改写原断" in result
        assert "条件落实后可按期完成" in result
        assert plugin.context.llm_generate.await_count == calls
        assert plugin._test_store == before

    asyncio.run(run())
