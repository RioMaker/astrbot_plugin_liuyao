from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_ai_daily_integration import used_count
from test_command_spacing import command_event, compact
from test_feedback_review import decision
from test_token_usage import response

from case_library import feedback_closed, feedback_eligible
from divination import ManualCastError
from manual_commands import parse_manual_request
from paipan import BEIJING

REVIEW = '{"reject":false,"valid_question":true,"intent":"事业"}'


def records(plugin):
    return plugin._test_store["liuyao_case_library"]["cases"]


@pytest.mark.parametrize(
    "values",
    [
        "789678",
        "7 8 9 6 7 8",
        "7,8,9,6,7,8",
        "7 , 8 , 9 , 6 , 7 , 8",
        "正反反 正正反 正正正 反反反 正反反 正正反",
        "正反反,正正反,正正正,反反反,正反反,正正反",
    ],
)
@pytest.mark.parametrize("time", ["", "时间=2026-11-15T20:30 ", "时间=2026-11-15 20:30 "])
def test_manual_parser_lines_order_time_and_question(values, time):
    now = datetime(2026, 11, 16, 10, 30, tzinfo=BEIJING)
    cast, question, source = parse_manual_request(f"{values} {time}{QUESTION}", now=now)
    assert cast.lines == (7, 8, 9, 6, 7, 8)
    assert question == QUESTION
    assert source == ("specified" if time else "received")
    assert cast.cast_at == (datetime(2026, 11, 15, 20, 30, tzinfo=BEIJING) if time else now)


@pytest.mark.parametrize(
    "text",
    [
        "78967",
        "7896789",
        "789670",
        "789678 时间=2026-02-30T10:00",
        "789678 时间=2026-11-15T25:00",
        "789678 时间=2026-11-15",
        "789678 时间=1899-11-15T10:00",
    ],
)
def test_manual_parser_rejects_malformed_cast_or_date(text):
    with pytest.raises(ManualCastError):
        parse_manual_request(text)


def test_manual_parser_preserves_multiline_question():
    question = "合同什么时候能签？\n补充：甲方已确认  两项条款。"
    _, parsed, _ = parse_manual_request("789678 " + question)
    assert parsed == question


def test_manual_question_starting_with_time_is_not_a_date_option():
    _, question, source = parse_manual_request("789678 时间合适吗，是否可以出发？")
    assert question == "时间合适吗，是否可以出发？" and source == "received"


@pytest.mark.parametrize("space", ["", " ", "　"])
@pytest.mark.parametrize("analyze", [False, True])
def test_spaced_and_compact_manual_commands_never_randomize(monkeypatch, space, analyze):
    def no_random():
        raise AssertionError("manual cast must not use RNG")

    monkeypatch.setattr("cast_policy.cast_instant", no_random)

    async def run():
        plugin, _ = prepare(limit=1)
        action = "解卦" if analyze else "排盘"
        text = f"{action} 789678 时间=2026-11-15T20:30 {QUESTION}"
        event = command_event("/六爻" + space + text)
        event.message_obj.message_id = "manual"
        if space:
            assert [item async for item in plugin.liuyao_command(event, text)] == []
        else:
            assert await compact(plugin, event) == []
            event.stop_event.assert_called_once()
        assert len(records(plugin)) == 1
        case = records(plugin)[0]
        assert case["cast"]["lines"] == [7, 8, 9, 6, 7, 8]
        assert case["created_at"] == "2026-11-15T20:30:00+08:00"
        assert case["recorded_at"] != case["created_at"]
        assert case["time_source"] == "specified"
        assert feedback_eligible(case) is analyze
        assert used_count(plugin) == int(analyze)
        assert plugin.context.llm_generate.await_count == (2 if analyze else 0)
        assert plugin._render_and_send_chart.call_args.args[1].chart == case["cast"]["chart"]

    asyncio.run(run())


def test_historical_first_read_and_reread_append_tokens_and_keep_original(monkeypatch):
    async def run():
        plugin, event = prepare(limit=3)
        await plugin._dispatch_liuyao(event, "排盘 789678 时间=2026-11-15T20:30")
        original = copy.deepcopy(records(plugin)[0])
        serial = original["serial"]
        last = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        for index, selector in enumerate((f"编号{serial}", f"#{serial}"), 1):
            event.message_obj.message_id = f"read-{index}"
            final = response(f"解读{index}。\n断语：第{index}次结论。", 100, 20)
            final.raw_completion = {"model": "actual-model-v1"}
            plugin.context.llm_generate.side_effect = [response(REVIEW, 40, 10), final]
            assert (
                await plugin._dispatch_liuyao(event, f"解卦 {selector} 项目{index}能否完成") == ""
            )
        saved = records(plugin)[0]
        assert len(records(plugin)) == 1 and saved["serial"] == serial
        assert saved["cast"] == original["cast"] and saved["created_at"] == original["created_at"]
        assert plugin._test_store["cast_policy:20002"] == last
        assert saved["verdict"] == "第1次结论。" and saved["analysis_question"] == "项目1能否完成"
        assert saved["question"] == original["question"]
        history = saved["analysis_history"]
        assert len(history) == 2 and all(row["status"] == "success" for row in history)
        assert [row["question"] for row in history] == ["项目1能否完成", "项目2能否完成"]
        for row in history:
            assert row["provider"] == "plugin-model" and row["model"] == "actual-model-v1"
            assert row["usage"]["total_tokens"] == 170 and len(row["calls"]) == 2
            assert datetime.fromisoformat(row["at"]) > datetime.fromisoformat(saved["created_at"])
        assert used_count(plugin) == 2
        assert feedback_eligible(saved)
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text=json.dumps(decision(saved["id"]), ensure_ascii=False))
        ]
        assert "有效反馈 +1" in await plugin._feedback_reply(event, f"{serial} 项目1实际延期两周")
        original_review = json.loads(
            plugin.context.llm_generate.call_args.kwargs["contexts"][0]["content"]
        )["original_case"]
        assert original_review["question"] == "项目1能否完成"
        assert original_review["verdict"] == "第1次结论。"
        event.message_obj.message_id = "read-after-feedback"
        plugin.context.llm_generate.side_effect = [response(REVIEW), response()]
        assert await plugin._dispatch_liuyao(event, f"解卦 #{serial} 下一步怎么做") == ""
        saved = records(plugin)[0]
        assert feedback_closed(saved) and not feedback_eligible(saved)
        assert len(saved["analysis_history"]) == 3
        assert await plugin._feedback_score(event) == 1
        assert "不在" in await plugin._feedback_reply(event, f"{serial} 再次反馈")

    asyncio.run(run())


@pytest.mark.parametrize("scope", ["owner", "group", "missing"])
def test_numbered_reading_respects_scope_and_never_calls_ai(scope):
    async def run():
        plugin, event = prepare()
        await plugin._dispatch_liuyao(event, "排盘 789678")
        saved = records(plugin)[0]
        number = saved["serial"]
        if scope == "owner":
            saved["caster_id"] = "other"
        elif scope == "group":
            saved["group_id"] = "other"
        else:
            number += 100
        assert "未找到" in await plugin._dispatch_liuyao(event, f"解卦 #{number} {QUESTION}")
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


def test_all_records_list_and_gray_cases_cannot_be_submitted():
    async def run():
        plugin, event = prepare()
        await plugin._dispatch_liuyao(event, "排盘 789678")
        event.message_obj.message_id = "ai"
        await plugin._dispatch_liuyao(event, QUESTION)
        event.message_obj.message_id = "closed"
        plugin.context.llm_generate.side_effect = [response(REVIEW), response()]
        await plugin._dispatch_liuyao(event, QUESTION)
        records(plugin)[2]["feedback"] = [{"valid": True, "text": "已完成"}]
        plugin._send_feedback_image = AsyncMock(return_value=False)
        plugin.context.llm_generate.reset_mock()
        result = await plugin._feedback_command_reply(event, "")
        assert "共 3 例" in result and all(f"编号 {n}｜" in result for n in range(1, 4))
        assert "不可反馈" in result and "已反馈" in result and "待反馈" in result
        assert "不能反馈" in await plugin._feedback_command_reply(event, "1 实际已完成")
        assert "尚未完成" in await plugin._append_case_feedback(
            event, case_id="1", feedback="已完成", outcome="应验", reviewed_valid=True
        )
        assert await plugin._feedback_score(event) == 0
        plugin.context.llm_generate.assert_not_awaited()
        assert len((await plugin._page_cases(query={"state": "pending"}))["items"]) == 1
        assert len((await plugin._page_cases(query={"state": "unavailable"}))["items"]) == 1

    asyncio.run(run())


def test_failover_and_cancellation_stay_in_same_case_with_unknown_tokens():
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config["command_interpretation_backup_provider_id"] = "backup"
        plugin.context.llm_generate.side_effect = [
            response(REVIEW),
            RuntimeError("failed"),
            response(),
        ]
        await plugin._dispatch_liuyao(event, f"解卦 789678 {QUESTION}")
        saved = records(plugin)[0]
        history = saved["analysis_history"][0]
        assert history["provider"] == "backup" and history["usage"]["unknown_calls"] == 1
        assert len(history["calls"]) == 3 and used_count(plugin) == 1
        before = copy.deepcopy(saved)
        plugin.config["ai_daily_limit"] = 2
        entered = asyncio.Event()

        async def block(**kwargs):
            entered.set()
            await asyncio.Future()

        plugin.context.llm_generate.side_effect = block
        event.message_obj.message_id = "cancel"
        task = asyncio.create_task(plugin._dispatch_liuyao(event, "解卦 #1 后续进展如何"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        saved = records(plugin)[0]
        assert saved["analysis"] == before["analysis"]
        assert saved["analysis_history"][-1]["status"] == "interrupted"
        assert saved["analysis_history"][-1]["calls"][0]["status"] == "cancelled"
        assert used_count(plugin) == 1

    asyncio.run(run())


def test_storage_failure_prevents_unrecorded_model_calls():
    async def run():
        plugin, event = prepare()
        original_put = plugin.put_kv_data

        async def broken(key, value):
            if key == "liuyao_case_library":
                raise OSError("unavailable")
            await original_put(key, value)

        plugin.put_kv_data = broken
        assert "保存失败" in await plugin._dispatch_liuyao(event, f"解卦 789678 {QUESTION}")
        plugin.context.llm_generate.assert_not_awaited()
        assert used_count(plugin) == 0

    asyncio.run(run())


def test_identical_manual_charts_have_independent_case_token_totals():
    async def run():
        plugin, event = prepare()
        for index in range(2):
            event.message_obj.message_id = f"import-{index}"
            plugin.context.llm_generate.side_effect = [response(REVIEW, 40, 10), response()]
            await plugin._dispatch_liuyao(
                event, f"解卦 789678 时间=2026-11-15T20:30 项目{index}能否完成"
            )
        assert len(records(plugin)) == 2
        page = await plugin._page_cases(query={})
        assert [row["token_usage"]["total_tokens"] for row in page["items"]] == [170, 170]
        assert len({row["token_key"] for row in records(plugin)}) == 2
        plugin.context.llm_generate.side_effect = [response(REVIEW, 40, 10), response()]
        event.message_obj.message_id = "reuse-latest"
        await plugin.reuse_liuyao_tool(event, question="后续进展如何")
        assert [len(row["analysis_history"]) for row in records(plugin)] == [1, 2]

    asyncio.run(run())


def test_chart_retention_cannot_evict_ai_case_and_old_chart_can_be_promoted():
    async def run():
        plugin, event = prepare()
        plugin.config.update(case_library_max_records=20, case_library_max_chart_records=20)
        await plugin._dispatch_liuyao(event, "排盘 789678")
        original = copy.deepcopy(records(plugin)[0])
        for index in range(21):
            event.message_obj.message_id = f"ai-{index}"
            plugin.context.llm_generate.side_effect = [response(REVIEW), response()]
            await plugin._dispatch_liuyao(event, QUESTION)
        assert len(records(plugin)) == 21
        assert records(plugin)[0]["id"] == original["id"]
        event.message_obj.message_id = "promote"
        plugin.context.llm_generate.side_effect = [response(REVIEW), response()]
        assert await plugin._dispatch_liuyao(event, f"解卦 #{original['serial']} {QUESTION}") == ""
        promoted = next(row for row in records(plugin) if row["id"] == original["id"])
        assert promoted["analysis"] == ANALYSIS and len(records(plugin)) == 20
        for index in range(22):
            event.message_obj.message_id = f"chart-{index}"
            await plugin._dispatch_liuyao(event, "排盘 789678")
        assert len(records(plugin)) == 40
        assert any(row["id"] == original["id"] for row in records(plugin))

    asyncio.run(run())


def test_invalid_manual_payload_never_falls_back_to_random_or_model(monkeypatch):
    def forbidden():
        raise AssertionError("invalid manual command cannot cast randomly")

    monkeypatch.setattr("cast_policy.cast_instant", forbidden)

    async def run():
        plugin, event = prepare()
        for text in (
            "排盘 78967",
            "解卦",
            "解卦 #abc",
            "解卦789678",
            "解卦编号233",
            "排盘正反反",
            "排盘 789678 时间=2026-02-30T10:00",
        ):
            assert await plugin._dispatch_liuyao(event, text)
        plugin.context.llm_generate.assert_not_awaited()
        assert "cast_policy:20002" not in plugin._test_store

    asyncio.run(run())
