from __future__ import annotations

import asyncio
import copy
import inspect
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from test_permissions import _Event, _make_enabled_plugin

from ai_daily_quota import BEIJING
from case_library import search_cases
from case_store import LIUYAO_CASES_KEY
from feedback_review import MAX_REVIEW_ROUNDS, parse_review_command
from main import GreedyStr, LiuyaoPlugin


def decision(case_id="case-1", *, valid=True, **overrides):
    return {
        "version": 1,
        "case_id": case_id,
        "action": "accept" if valid else "retry",
        "valid": valid,
        "outcome": "未应验" if valid else "未分类",
        "reason": "说明了项目实际延期" if valid else "只有评价，未说明实际进展",
        "guidance": "" if valid else "请说明原项目实际是否完成、何时完成及与原断的差异",
        **overrides,
    }


class ReviewModel:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def get_current_chat_provider_id(self, **kwargs):
        return "test-provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        await asyncio.sleep(0)
        value = self.results.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(
            completion_text=json.dumps(value, ensure_ascii=False)
            if isinstance(value, dict)
            else value
        )


def case(number=1, **overrides):
    return {
        "id": f"case-{number}",
        "serial": number,
        "group_id": "10001",
        "caster_id": "20002",
        "created_at": datetime.now(BEIJING).isoformat(),
        "question": f"项目{number}能否按期完成",
        "analysis": f"项目{number}预计按时完成。依据：动爻旺。",
        "verdict": "将如期完成",
        "cast": {"primary_name": "乾", "primary_number": 1, "moving_lines": [2]},
        "feedback": [],
        "status": "analyzed",
        "intent": "career",
        **overrides,
    }


def setup(*results, records=None):
    plugin = _make_enabled_plugin()
    plugin.config.update(
        {
            "ai_quota_feedback_rules": [
                {
                    "name": "短期奖励",
                    "window_days": 3,
                    "minimum_feedback_count": 3,
                    "daily_bonus": 1,
                }
            ],
        }
    )
    plugin.context = ReviewModel(*results)
    plugin._test_store[LIUYAO_CASES_KEY] = {"cases": records if records is not None else [case()]}
    return plugin, _Event("member")


def saved(plugin, number=1):
    return next(c for c in plugin._test_store[LIUYAO_CASES_KEY]["cases"] if c["serial"] == number)


def test_bare_command_lists_private_queue_rules_and_does_not_call_ai():
    async def run():
        now = datetime.now(BEIJING)
        plugin, event = setup(
            records=[
                case(1),
                case(7),
                case(8, analysis=""),
                case(9, caster_id="someone-else"),
                case(10, group_id="other-group"),
                case(11, feedback=[{"text": "已完成"}]),
            ]
        )
        payload = plugin._test_store[LIUYAO_CASES_KEY]
        payload["user_scores"] = {"20002": 2}
        payload["valid_feedback_events"] = {
            "20002": [
                (now - timedelta(days=1)).isoformat(),
                (now - timedelta(days=5)).isoformat(),
            ]
        }
        replies = [reply async for reply in plugin.liuyao_feedback_command(event, "")]
        text = replies[0]["text"]
        assert "队列：2 条" in text and "编号 1｜" in text and "编号 7｜" in text
        assert "编号 9｜" not in text and "编号 10｜" not in text
        assert "累计有效反馈：2 次" in text and "3天内反馈：1/3" in text
        assert "通过才记录" in text and "未应验也算有效" in text and "原编号保留" in text
        assert not plugin.context.calls

    asyncio.run(run())


def test_feedback_handler_keeps_greedy_dispatch_contract_without_default():
    parameter = inspect.signature(LiuyaoPlugin.liuyao_feedback_command, eval_str=True).parameters[
        "content"
    ]
    assert parameter.annotation is GreedyStr
    assert parameter.default is inspect.Parameter.empty


@pytest.mark.parametrize(
    "feedback",
    [
        "实际项目延期两周，和原断不一致",
        "项目实际延期了 两 周，合同还没签",
        "项目延期了\n实际两周后才完成",
    ],
)
def test_numbered_feedback_command_passes_complete_body_to_review(feedback):
    async def run():
        plugin, event = setup(decision("case-905"), records=[case(905)])
        replies = [
            reply async for reply in plugin.liuyao_feedback_command(event, f"905 {feedback}")
        ]
        assert "已闭环" in replies[0]["text"]
        request = json.loads(plugin.context.calls[0]["prompt"])
        assert request["case_id"] == "case-905"
        assert request["feedback"] == feedback
        assert saved(plugin, 905)["feedback"][0]["text"] == feedback

    asyncio.run(run())


def test_retry_then_accept_inherits_prefix_and_only_then_closes_and_rewards():
    async def run():
        plugin, event = setup(decision(valid=False), decision())
        first = await plugin._feedback_reply(event, "1 挺准的")
        assert "未通过" in first and "问题：" in first and "请补充：" in first
        assert "重新提交：/反馈 1" in first
        assert saved(plugin)["feedback"] == [] and saved(plugin)["status"] == "analyzed"
        assert await plugin._feedback_score(event) == 0
        assert await plugin._valid_feedback_counts(event, {3}) == {3: 0}
        second = await plugin._feedback_reply(event, "1 实际项目延期了两周，未按预测完成")
        assert "已闭环" in second and "有效反馈 +1" in second
        calls = plugin.context.calls
        assert calls[0]["system_prompt"] == calls[1]["system_prompt"]
        assert calls[1]["contexts"][:1] == calls[0]["contexts"]
        assert calls[1]["contexts"][1] == {"role": "user", "content": calls[0]["prompt"]}
        assert json.loads(calls[1]["contexts"][2]["content"])["action"] == "retry"
        original = json.loads(calls[0]["contexts"][0]["content"])["original_case"]
        assert original["analysis"] == saved(plugin)["analysis"] and original["cast"][
            "moving_lines"
        ] == [2]
        feedback = saved(plugin)["feedback"][0]
        assert feedback["valid"] is True and feedback["outcome"] == "未应验"
        assert feedback["review_command"] == decision()
        assert await plugin._feedback_score(event) == 1
        assert await plugin._valid_feedback_counts(event, {3}) == {3: 1}
        assert "3天内反馈：1/3" in await plugin._feedback_reply(event, "")
        assert "不在" in await plugin._feedback_reply(event, "1 再次反馈")
        assert len(plugin.context.calls) == 2
        assert not any(k.startswith("ai_daily_quota_v1:") for k in plugin._test_store)

    asyncio.run(run())


def test_stable_numbers_do_not_shift_after_another_case_is_closed():
    async def run():
        plugin, event = setup(decision("case-3"), records=[case(3), case(9)])
        assert "已闭环" in await plugin._feedback_reply(event, "3 实际已经延期")
        queue = await plugin._feedback_reply(event, "")
        assert "编号 9｜" in queue and "编号 3｜" not in queue
        assert "不在" in await plugin._feedback_reply(event, "1 这是队列第一条")
        assert len(plugin.context.calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "overrides",
    [
        {"valid": "true"},
        {"valid": 1},
        {"case_id": "someone-else"},
        {"action": "execute"},
        {"action": "retry"},
        {"version": True},
        {"outcome": "未知结果"},
        {"reason": ""},
        {"reason": {}},
        {"guidance": []},
        {"daily_bonus": 999},
    ],
)
def test_malformed_or_unauthorized_commands_never_close_or_reward(overrides):
    async def run():
        plugin, event = setup(decision(**overrides))
        reply = await plugin._feedback_reply(event, "1 项目延期两周")
        assert "尚未记录" in reply
        assert saved(plugin)["feedback"] == []
        assert "feedback_review" not in saved(plugin)
        assert await plugin._feedback_score(event) == 0

    asyncio.run(run())


@pytest.mark.parametrize("value", ["非 JSON", "```json\n{}\n```", TimeoutError(), "{}"])
def test_model_failure_is_retryable(value):
    async def run():
        plugin, event = setup(value, decision())
        assert "稍后重试" in await plugin._feedback_reply(event, "1 项目延期")
        assert "已闭环" in await plugin._feedback_reply(event, "1 项目延期")
        assert await plugin._feedback_score(event) == 1

    asyncio.run(run())


def test_retry_requires_actionable_guidance():
    with pytest.raises(ValueError):
        parse_review_command(json.dumps(decision(valid=False, guidance="")), "case-1")


def test_review_context_survives_reload_and_is_isolated_between_cases():
    async def run():
        plugin, event = setup(decision(valid=False), records=[case(), case(2)])
        await plugin._feedback_reply(event, "1 挺准")
        previous = copy.deepcopy(saved(plugin)["feedback_review"])
        reloaded, _ = setup(decision("case-2", valid=False), decision())
        reloaded._test_store[LIUYAO_CASES_KEY] = copy.deepcopy(plugin._test_store[LIUYAO_CASES_KEY])
        await reloaded._feedback_reply(event, "2 很准")
        await reloaded._feedback_reply(event, "1 实际延期两周")
        assert len(reloaded.context.calls[0]["contexts"]) == 1
        assert reloaded.context.calls[1]["contexts"] == previous["contexts"]
        assert "项目1" not in reloaded.context.calls[0]["contexts"][0]["content"]

    asyncio.run(run())


def test_concurrent_duplicate_submission_only_calls_ai_and_rewards_once():
    async def run():
        plugin, event = setup(decision())
        replies = await asyncio.gather(
            *[plugin._feedback_reply(event, "1 实际延期两周") for _ in range(2)]
        )
        assert sum("已闭环" in text for text in replies) == 1
        assert len(plugin.context.calls) == 1 and len(saved(plugin)["feedback"]) == 1
        assert await plugin._feedback_score(event) == 1

    asyncio.run(run())


def test_agent_tool_uses_same_retry_context_and_ignores_claimed_outcome():
    async def run():
        plugin, event = setup(decision(valid=False), decision())
        assert "未通过" in await plugin.record_liuyao_feedback_tool(
            event,
            "挺准",
            case_id="case-1",
            outcome="应验",
        )
        assert "已闭环" in await plugin._feedback_reply(event, "1 实际延期两周")
        assert len(plugin.context.calls[1]["contexts"]) == 3
        assert saved(plugin)["feedback"][0]["outcome"] == "未应验"

    asyncio.run(run())


def test_no_provider_wrong_owner_disabled_library_and_missing_content_are_free():
    async def run():
        plugin, event = setup()
        assert "请补充" in await plugin._feedback_reply(event, "1")
        assert "不在" in await plugin._feedback_reply(event, "9 无关")
        event.get_sender_id = lambda: "other-user"
        assert "队列：0 条" in await plugin._feedback_reply(event, "")
        assert "未找到" in await plugin.record_liuyao_feedback_tool(event, "项目延期", case_id="1")
        event.get_sender_id = lambda: "20002"

        async def no_provider(**kwargs):
            return ""

        plugin.context.get_current_chat_provider_id = no_provider
        assert "尚未记录" in await plugin._feedback_reply(event, "1 项目延期")
        plugin.config["case_library_enabled"] = False
        assert "关闭" in await plugin._feedback_reply(event, "")
        assert not plugin.context.calls

    asyncio.run(run())


def test_legacy_rejected_feedback_can_retry_and_never_enters_references():
    async def run():
        old = {"valid": False, "text": "哈哈哈", "outcome": "未分类"}
        plugin, event = setup(
            decision(), records=[case(feedback=[old], status="feedback_recorded")]
        )
        assert not search_cases([saved(plugin)], group_id="10001", query="项目", primary_number=1)
        assert "编号 1｜" in await plugin._feedback_reply(event, "")
        assert "已闭环" in await plugin._feedback_reply(event, "1 项目延期两周")
        assert saved(plugin)["legacy_rejected_feedback"] == [old]
        assert len(saved(plugin)["feedback"]) == 1

    asyncio.run(run())


def test_save_failure_keeps_case_open_and_unrewarded():
    async def run():
        plugin, event = setup(decision())

        async def fail_save(*args):
            raise OSError("disk failure")

        plugin.put_kv_data = fail_save
        # Numbering is already migrated, so failure happens while saving the review.
        plugin._test_store[LIUYAO_CASES_KEY].update(schema_version=2, next_serial=2)
        assert "尚未记录" in await plugin._feedback_reply(event, "1 项目延期")
        assert not saved(plugin)["feedback"] and await plugin._feedback_score(event) == 0

    asyncio.run(run())


def test_context_is_bounded_and_keeps_original_case():
    async def run():
        plugin, event = setup(*[decision(valid=False) for _ in range(MAX_REVIEW_ROUNDS + 3)])
        for i in range(MAX_REVIEW_ROUNDS + 3):
            assert "未通过" in await plugin._feedback_reply(event, f"1 好{i}")
        contexts = saved(plugin)["feedback_review"]["contexts"]
        assert len(contexts) == 1 + 2 * MAX_REVIEW_ROUNDS
        assert json.loads(contexts[0]["content"])["original_case"]["id"] == "case-1"
        assert json.loads(contexts[1]["content"])["feedback"] == "好3"

    asyncio.run(run())


def test_reward_threshold_counts_only_accepted_distinct_cases():
    async def run():
        plugin, event = setup(
            *[decision(f"case-{n}") for n in range(1, 4)],
            records=[
                case(1),
                case(2),
                case(3),
            ],
        )
        plugin.config["ai_daily_limit"] = 1
        for n in range(1, 4):
            assert "已闭环" in await plugin._feedback_reply(event, f"{n} 实际项目延期两周")
        assert await plugin._effective_ai_daily_limit(event) == 2
        queue = await plugin._feedback_reply(event, "")
        assert "3天内反馈：3/3，已达标" in queue and "累计有效反馈：3 次" in queue

    asyncio.run(run())


def test_distinct_closures_at_identical_timestamps_are_counted_separately():
    plugin, _ = setup()
    timestamp = datetime.now(BEIJING).isoformat()
    payload = {"valid_feedback_events": {"20002": [timestamp]}}
    plugin._record_valid_feedback_event(payload, [], "20002", timestamp)
    assert payload["valid_feedback_events"]["20002"] == [timestamp, timestamp]


def test_formal_feedback_save_failure_can_retry_without_early_reward():
    async def run():
        plugin, event = setup(decision(), decision())
        save = plugin.put_kv_data

        async def fail_closure(key, payload):
            if key == LIUYAO_CASES_KEY and payload["cases"][0].get("feedback"):
                raise OSError("closure save failed")
            await save(key, payload)

        plugin.put_kv_data = fail_closure
        reply = await plugin._feedback_reply(event, "1 实际延期两周")
        assert "保存失败" in reply and "已闭环" not in reply
        assert not saved(plugin)["feedback"] and saved(plugin)["status"] == "analyzed"
        assert await plugin._feedback_score(event) == 0
        plugin.put_kv_data = save
        assert "已闭环" in await plugin._feedback_reply(event, "1 实际延期两周")
        assert await plugin._feedback_score(event) == 1

    asyncio.run(run())


def test_store_rejects_invalid_feedback_even_if_caller_attempts_to_save_it():
    async def run():
        plugin, event = setup()
        reply = await plugin._append_case_feedback(
            event,
            case_id="case-1",
            feedback="哈哈哈",
            outcome="未分类",
            reviewed_valid=False,
            review_reason="无关内容",
        )
        assert "未通过" in reply and not saved(plugin)["feedback"]
        assert saved(plugin)["status"] == "analyzed"

    asyncio.run(run())


def test_legacy_numbering_is_saved_before_queue_display():
    async def run():
        old = case()
        del old["serial"]
        plugin, event = setup(records=[old])
        assert "编号 1｜" in await plugin._feedback_reply(event, "")
        assert saved(plugin)["serial"] == 1
        payload = plugin._test_store[LIUYAO_CASES_KEY]
        assert payload["next_serial"] == 2

    asyncio.run(run())
