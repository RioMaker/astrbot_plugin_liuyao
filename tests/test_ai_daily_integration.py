from __future__ import annotations

import asyncio
import copy
import json
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
            SimpleNamespace(
                completion_text='{"reject":false,"valid_question":true,"intent":"事业"}'
            ),
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


@pytest.mark.parametrize("via_tool", [False, True])
@pytest.mark.parametrize("failure", ["empty", "exception", "timeout", "error_role", "stopped"])
def test_interpretation_failure_releases_quota_and_never_saves_prediction(failure, via_tool):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(
                completion_text='{"reject":false,"valid_question":true,"intent":"事业"}'
            ),
            (
                TimeoutError("model timeout")
                if failure == "timeout"
                else RuntimeError("model failure")
            )
            if failure in {"exception", "timeout"}
            else SimpleNamespace(
                completion_text="" if failure == "empty" else "Output stopped.",
                role="err" if failure == "error_role" else "assistant",
            ),
        ]
        if via_tool:
            await plugin.cast_liuyao_tool(event, question=QUESTION)
        else:
            await plugin._dispatch_liuyao(event, QUESTION)
        assert used_count(plugin) == 0
        assert "今日已用：0 次，剩余：1 次" in await plugin._ai_quota_status(event)
        assert plugin.context.llm_generate.await_count == 2
        assert "liuyao_case_library" not in plugin._test_store
        plugin._render_and_send_chart.assert_awaited_once()
        assert (
            "失败" in plugin._send_reply_payload.call_args.args[3]
            or "未能返回" in plugin._send_reply_payload.call_args.args[3]
        )
        assert "不消耗 AI 解卦次数" in plugin._send_reply_payload.call_args.args[3]

        # A retry on the preserved chart can use the released daily credit.
        original = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        event.message_obj.message_id = "retry"
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(
                completion_text='{"reject":false,"valid_question":true,"intent":"事业"}'
            ),
            SimpleNamespace(completion_text=ANALYSIS),
        ]
        await plugin.reuse_liuyao_tool(event)
        assert used_count(plugin) == 1
        assert plugin._test_store["cast_policy:20002"] == original
        assert plugin.context.llm_generate.await_count == 4

    asyncio.run(run())


@pytest.mark.parametrize("via_tool", [False, True])
@pytest.mark.parametrize(
    "question",
    ["起卦", "算一卦", "请帮我起一卦吧！", "事业 起卦", "随便看看", "？？？", "测试一下"],
)
def test_obvious_non_question_only_casts_without_models_or_quota(question, via_tool):
    async def run():
        plugin, event = prepare(limit=1)
        if via_tool:
            await plugin.cast_liuyao_tool(event, question=question)
        else:
            await plugin._dispatch_liuyao(event, question)
        plugin.context.llm_generate.assert_not_awaited()
        plugin._render_and_send_chart.assert_awaited_once()
        assert plugin._render_and_send_chart.call_args.kwargs["method"] == "普通排盘"
        assert "cast_policy:20002" in plugin._test_store
        assert "liuyao_case_library" not in plugin._test_store
        assert not any(key.startswith("ai_daily_quota_v1:") for key in plugin._test_store)
        assert "未识别到有效问题" in plugin._send_reply_payload.call_args.args[3]
        assert "今日已用：0 次，剩余：1 次" in await plugin._ai_quota_status(event)

    asyncio.run(run())


@pytest.mark.parametrize(
    "review,notice",
    [
        ({"reject": False, "valid_question": False}, "未识别到有效问题"),
        ({"reject": "false", "valid_question": "false"}, "未识别到有效问题"),
        ({"reject": True, "valid_question": True}, "不能提供"),
        ({"reject": False}, "预审暂不可用"),
        ({"reject": False, "valid_question": "unknown"}, "预审暂不可用"),
        ({"reject": False, "valid_question": 1}, "预审暂不可用"),
        ({"reject": False, "valid_question": None}, "预审暂不可用"),
    ],
)
def test_review_requires_an_explicit_valid_question_and_releases_quota(review, notice):
    async def run():
        plugin, event = prepare(limit=1, replies=[json.dumps(review)])
        await plugin.cast_liuyao_tool(event, question="我也不知道要问什么，你来决定吧")
        plugin.context.llm_generate.assert_awaited_once()
        plugin._render_and_send_chart.assert_awaited_once()
        assert notice in plugin._send_reply_payload.call_args.args[3]
        assert "不消耗 AI 解卦次数" in plugin._send_reply_payload.call_args.args[3]
        assert "liuyao_case_library" not in plugin._test_store
        assert "今日已用：0 次，剩余：1 次" in await plugin._ai_quota_status(event)

    asyncio.run(run())


def test_reuse_requires_original_or_supplement_to_contain_a_question():
    async def run():
        plugin, event = prepare(limit=1)
        await plugin.cast_liuyao_tool(event, question="起卦")
        original = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        event.message_obj.message_id = "empty-reuse"
        await plugin.reuse_liuyao_tool(event, question="请解卦")
        plugin.context.llm_generate.assert_not_awaited()
        assert used_count(plugin) == 0
        event.message_obj.message_id = "valid-supplement"
        await plugin.reuse_liuyao_tool(event, question=QUESTION)
        assert plugin.context.llm_generate.await_count == 2
        assert used_count(plugin) == 1
        assert plugin._test_store["cast_policy:20002"] == original

    asyncio.run(run())


def test_failed_reuse_of_same_message_does_not_refund_previous_success():
    async def run():
        plugin, event = prepare(limit=1)
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        cases = copy.deepcopy(plugin._test_store["liuyao_case_library"])
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text='{"reject":false,"valid_question":true}'),
            RuntimeError("interpretation failed"),
        ]
        await plugin.reuse_liuyao_tool(event)
        assert used_count(plugin) == 1
        assert plugin._test_store["liuyao_case_library"] == cases
        assert "今日已用：1 次，剩余：0 次" in await plugin._ai_quota_status(event)

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["pre_review", "interpretation"])
def test_cancellation_during_models_releases_quota(stage):
    async def run():
        plugin, event = prepare(limit=1)
        entered = asyncio.Event()

        async def blocked(**kwargs):
            if stage == "interpretation" and plugin.context.llm_generate.await_count == 1:
                return SimpleNamespace(completion_text='{"reject":false,"valid_question":true}')
            entered.set()
            await asyncio.Future()

        plugin.context.llm_generate.side_effect = blocked
        task = asyncio.create_task(plugin.cast_liuyao_tool(event, question=QUESTION))
        await entered.wait()
        assert "今日已用：1 次，剩余：0 次" in await plugin._ai_quota_status(event)
        assert used_count(plugin) == 0  # Pending, not consumed.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "今日已用：0 次，剩余：1 次" in await plugin._ai_quota_status(event)
        assert "liuyao_case_library" not in plugin._test_store

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["review", "invalid_question", "interpretation"])
def test_failed_request_releases_gift_without_refunding_previous_success(failure):
    async def run():
        plugin, event = prepare(limit=1)
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        await plugin._admin_grant(
            "20002", {"amount": 1, "request_id": "failure-gift-request"}, "admin"
        )
        cases = copy.deepcopy(plugin._test_store["liuyao_case_library"])
        replies = []
        if failure == "interpretation":
            replies.append(
                SimpleNamespace(completion_text='{"reject":false,"valid_question":true}')
            )
        replies.append(
            SimpleNamespace(completion_text='{"reject":false,"valid_question":false}')
            if failure == "invalid_question"
            else RuntimeError("model failed")
        )
        plugin.context.llm_generate.side_effect = replies
        event.message_obj.message_id = "failed-gift-request"
        await plugin.reuse_liuyao_tool(event)
        assert used_count(plugin) == 1
        assert await plugin._gift_remaining(event) == 1
        assert plugin._test_store["liuyao_case_library"] == cases
        assert "剩余：0 +1 次" in await plugin._ai_quota_status(event)

        event.message_obj.message_id = "successful-gift-retry"
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text='{"reject":false,"valid_question":true}'),
            SimpleNamespace(completion_text=ANALYSIS),
        ]
        await plugin.reuse_liuyao_tool(event)
        assert used_count(plugin) == 1
        assert await plugin._gift_remaining(event) == 0

    asyncio.run(run())


@pytest.mark.parametrize(
    "question", ["下雨吗", "请帮我起卦，明天面试能通过吗", "我有点无聊，明天能签约吗"]
)
def test_short_or_boilerplate_prefixed_real_question_can_still_be_interpreted(question):
    async def run():
        plugin, event = prepare(limit=1)
        await plugin._dispatch_liuyao(event, question)
        assert plugin.context.llm_generate.await_count == 2
        assert plugin._send_reply_payload.call_args.args[3] == ANALYSIS
        assert used_count(plugin) == 1

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
