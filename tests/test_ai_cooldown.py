from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_feedback_review import decision

from ai_cooldown import CASES_KEY, COOLDOWNS_KEY, PENDING_SECONDS
from ai_daily_quota import BEIJING, STORAGE_ERROR


def setup(**config):
    plugin, event = prepare(limit=0)
    plugin.config.update(ai_cooldown_enabled=True, **config)
    now = [datetime(2026, 11, 16, 12, tzinfo=BEIJING)]
    plugin._ai_daily_now = lambda: now[0]
    return plugin, event, now


def state(plugin):
    return plugin._test_store[CASES_KEY][COOLDOWNS_KEY]["20002"]


async def read(plugin, event, message="first", *, tool=False, replies=None):
    event.message_obj.message_id = message
    plugin.context.llm_generate.side_effect = replies or [
        SimpleNamespace(completion_text='{"reject":false,"valid_question":true}'),
        SimpleNamespace(completion_text=ANALYSIS),
    ]
    before = plugin.context.llm_generate.await_count
    if tool:
        await plugin.cast_liuyao_tool(event, question=QUESTION)
    else:
        await plugin._dispatch_liuyao(event, QUESTION)
    return plugin.context.llm_generate.await_count - before


@pytest.mark.parametrize("tool", [False, True])
def test_first_week_then_normal_day_and_midnight_does_not_reset(tool):
    async def run():
        plugin, event, now = setup()
        start = now[0]
        assert await read(plugin, event, tool=tool) == 2
        assert state(plugin)["first_at"] == start.timestamp()
        assert not state(plugin)["normal"]
        now[0] += timedelta(days=6, hours=23)
        assert await read(plugin, event, "blocked", tool=tool) == 0
        assert "首次冷却中（7 天）" in plugin._send_caster_reply.call_args.args[3]
        assert "2026-11-23 12:00:00" in await plugin._ai_quota_status(event)
        now[0] = start + timedelta(days=7)
        assert await read(plugin, event, "second", tool=tool) == 2
        assert state(plugin)["normal"]
        now[0] += timedelta(hours=23)
        assert await read(plugin, event, "early", tool=tool) == 0
        assert "正常冷却中（24 小时）" in await plugin._ai_quota_status(event)
        now[0] += timedelta(hours=1)
        assert await read(plugin, event, "third", tool=tool) == 2

    asyncio.run(run())


@pytest.mark.parametrize("feedback_hours", [2, 30])
def test_reviewed_first_feedback_switches_immediately_without_restarting_clock(feedback_hours):
    async def run():
        plugin, event, now = setup()
        await read(plugin, event)
        first = plugin._test_store[CASES_KEY]["cases"][0]
        start = state(plugin)["last_at"]
        now[0] += timedelta(hours=feedback_hours)
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text=json.dumps(decision(first["id"])))
        ]
        reply = await plugin._submit_case_feedback(event, first, "项目实际延期，还没有完成")
        assert "反馈通过" in reply
        assert state(plugin)["normal"]
        assert state(plugin)["last_at"] == start
        assert state(plugin)["first_case_id"] == first["id"]
        if feedback_hours < 24:
            assert await read(plugin, event, "early") == 0
            assert "2026-11-17 12:00:00" in await plugin._ai_quota_status(event)
            now[0] += timedelta(hours=24 - feedback_hours)
        assert await read(plugin, event, "next") == 2

    asyncio.run(run())


def test_invalid_unreviewed_other_case_and_failed_feedback_do_not_unlock():
    async def run():
        plugin, event, now = setup()
        await read(plugin, event)
        first = plugin._test_store[CASES_KEY]["cases"][0]
        now[0] += timedelta(days=2)
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text=json.dumps(decision(first["id"], valid=False)))
        ]
        assert "未通过" in await plugin._submit_case_feedback(event, first, "很准")
        assert not state(plugin)["normal"]
        other = copy.deepcopy(first)
        other.update(id="other", serial=2, feedback=[])
        plugin._test_store[CASES_KEY]["cases"].append(other)
        assert "反馈通过" in await plugin._append_case_feedback(
            event, case_id="other", feedback="延期", outcome="未应验", reviewed_valid=True
        )
        assert not state(plugin)["normal"]
        original = plugin.put_kv_data

        async def fail_feedback(key, value):
            if key == CASES_KEY and value.get("user_scores", {}).get("20002") == 2:
                raise OSError("cannot save")
            await original(key, value)

        plugin.put_kv_data = fail_feedback
        assert "保存失败" in await plugin._append_case_feedback(
            event, case_id=first["id"], feedback="延期", outcome="未应验", reviewed_valid=True
        )
        assert not state(plugin)["normal"]
        plugin.put_kv_data = original
        await plugin._append_case_feedback(
            event, case_id=first["id"], feedback="延期", outcome="未应验", reviewed_valid=None
        )
        assert not state(plugin)["normal"]

    asyncio.run(run())


def test_hours_days_configuration_changes_and_switches():
    async def run():
        plugin, event, now = setup(
            ai_first_cooldown_duration=3,
            ai_first_cooldown_unit="小时",
            ai_normal_cooldown_duration=2,
            ai_normal_cooldown_unit="天",
        )
        await read(plugin, event)
        now[0] += timedelta(hours=2)
        assert await read(plugin, event, "early") == 0
        plugin.config["ai_first_cooldown_duration"] = 2
        assert await read(plugin, event, "second") == 2
        now[0] += timedelta(days=1)
        assert await read(plugin, event, "early-normal") == 0
        plugin.config["ai_normal_cooldown_duration"] = 1
        assert await read(plugin, event, "third") == 2
        plugin.config["ai_cooldown_enabled"] = False
        assert await read(plugin, event, "disabled") == 2
        plugin.config["ai_cooldown_enabled"] = True
        assert await read(plugin, event, "enabled-again") == 0

        plugin, event, now = setup(ai_first_cooldown_enabled=False)
        await read(plugin, event)
        assert "正常冷却中" in await plugin._ai_quota_status(event)
        now[0] += timedelta(days=1)
        assert await read(plugin, event, "second") == 2

    asyncio.run(run())


def test_reload_cross_group_cross_platform_and_case_deletion_preserve_cooldown():
    async def run():
        plugin, event, now = setup()
        await read(plugin, event)
        snapshot = copy.deepcopy(plugin._test_store)
        reloaded, new_event, new_now = setup()
        reloaded._test_store.update(snapshot)
        new_now[0] = now[0] + timedelta(days=2)
        new_event.get_group_id = lambda: "10002"
        new_event.unified_msg_origin = "qq-2:GroupMessage:10002"
        new_event.message_obj.message_id = "cross-platform"
        assert "首次冷却" in (await reloaded._reserve_ai_daily(new_event)).error
        # Pruning/deletion uses serialize_case_store and retains this ledger.
        reloaded._test_store[CASES_KEY]["cases"] = []
        assert "首次冷却" in (await reloaded._reserve_ai_daily(new_event)).error
        assert state(reloaded) == snapshot[CASES_KEY][COOLDOWNS_KEY]["20002"]

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["model", "review", "question"])
def test_failure_and_chart_only_do_not_start_cooldown(failure):
    async def run():
        plugin, event, _ = setup()
        if failure == "question":
            await plugin._dispatch_liuyao(event, "起卦")
            assert COOLDOWNS_KEY not in plugin._test_store[CASES_KEY]
        else:
            replies = (
                [SimpleNamespace(completion_text='{"reject":true,"valid_question":true}')]
                if failure == "review"
                else [
                    SimpleNamespace(completion_text='{"reject":false,"valid_question":true}'),
                    RuntimeError("model failed"),
                ]
            )
            await read(plugin, event, replies=replies)
            assert state(plugin)["last_at"] == 0
            assert not state(plugin)["pending"]
        assert await read(plugin, event, "retry") == 2
        assert not state(plugin)["normal"]

    asyncio.run(run())


def test_concurrent_admission_dedup_and_release_are_shared_across_platforms():
    async def run():
        plugin, _, _ = setup()
        events = [prepare()[1] for _ in range(8)]
        for i, event in enumerate(events):
            event.message_obj.message_id = str(i)
            event.unified_msg_origin = f"qq-{i}:GroupMessage:10001"
        leases = await asyncio.gather(*(plugin._reserve_ai_daily(event) for event in events))
        accepted = [lease for lease in leases if not lease.error]
        assert len(accepted) == 1
        repeat = await plugin._reserve_ai_daily(events[0])
        assert not repeat.error
        await accepted[0].release()
        assert (await plugin._reserve_ai_daily(events[1])).error
        await repeat.release()
        assert not state(plugin)["pending"]
        retry = await plugin._reserve_ai_daily(events[1])
        assert not retry.error
        await retry.release()

    asyncio.run(run())


def test_duplicate_success_does_not_extend_cooldown_or_skip_first_period():
    async def run():
        plugin, event, now = setup()
        await read(plugin, event)
        original = copy.deepcopy(state(plugin))
        now[0] += timedelta(hours=1)
        assert await read(plugin, event) == 2
        assert state(plugin) == original

    asyncio.run(run())


def test_existing_successes_use_normal_cooldown_and_manual_chart_time_is_ignored():
    async def run():
        plugin, event, now = setup()
        # Simulate an upgrade with an old successful reading and no cooldown ledger.
        plugin._test_store[CASES_KEY] = {
            "cases": [
                {
                    "id": "legacy",
                    "caster_id": "20002",
                    "analysis": ANALYSIS,
                    "created_at": "1900-01-01T00:00:00+08:00",
                    "analysis_at": (now[0] - timedelta(hours=2)).isoformat(),
                }
            ]
        }
        assert "正常冷却中" in await plugin._ai_quota_status(event)
        now[0] += timedelta(hours=22)
        assert await read(plugin, event) == 2
        assert state(plugin)["normal"]
        assert state(plugin)["last_at"] == now[0].timestamp()

    asyncio.run(run())


def test_gifts_do_not_bypass_cooldown_but_whitelist_does():
    async def run():
        plugin, event, _ = setup()
        plugin.config["ai_daily_limit"] = 1
        plugin._test_store[plugin._gift_key("20002")] = {"balance": 3, "days": {}}
        await read(plugin, event)
        assert await read(plugin, event, "gift-blocked") == 0
        assert await plugin._gift_remaining(event) == 3
        plugin.config["ai_quota_whitelist_qq"] = "20002"
        assert await read(plugin, event, "whitelist") == 2
        assert "白名单豁免" in await plugin._ai_quota_status(event)
        assert await plugin._gift_remaining(event) == 3

    asyncio.run(run())


def test_successful_gift_reading_starts_normal_cooldown():
    async def run():
        plugin, event, now = setup(
            ai_first_cooldown_duration=1,
            ai_first_cooldown_unit="小时",
            ai_normal_cooldown_duration=1,
        )
        plugin.config["ai_daily_limit"] = 1
        plugin._test_store[plugin._gift_key("20002")] = {"balance": 3, "days": {}}
        assert await read(plugin, event) == 2
        now[0] += timedelta(hours=1)
        assert await read(plugin, event, "gift") == 2
        assert await plugin._gift_remaining(event) == 2
        assert state(plugin)["normal"]
        assert not state(plugin)["pending"]
        assert await read(plugin, event, "gift-too-soon") == 0
        assert await plugin._gift_remaining(event) == 2

    asyncio.run(run())


def test_historical_manual_reading_and_numbered_reread_share_cooldown():
    async def run():
        plugin, event, now = setup()
        await plugin._dispatch_liuyao(event, "解卦 789678 时间=202610220830 " + QUESTION)
        assert plugin.context.llm_generate.await_count == 2
        case = plugin._test_store[CASES_KEY]["cases"][0]
        assert case["created_at"].startswith("2026-10-22")
        assert state(plugin)["last_at"] == now[0].timestamp()
        event.message_obj.message_id = "numbered-reread"
        await plugin._dispatch_liuyao(event, f"解卦 #{case['serial']} {QUESTION}")
        assert plugin.context.llm_generate.await_count == 2
        assert "首次冷却" in plugin._send_caster_reply.call_args.args[3]
        assert len(plugin._test_store[CASES_KEY]["cases"]) == 1

    asyncio.run(run())


@pytest.mark.parametrize("value", [True, None, -1, 0, 1.5, "bad", "2.5", 87601])
def test_bad_duration_values_fall_back_to_defaults(value):
    plugin, _, _ = setup(ai_first_cooldown_duration=value, ai_normal_cooldown_duration=value)
    assert plugin._ai_cooldown_duration(first=True) == (7 * 86400, "7 天")
    assert plugin._ai_cooldown_duration() == (86400, "24 小时")


def test_storage_errors_fail_closed_without_spending_daily_credit():
    async def run():
        plugin, event, _ = setup()
        original = plugin.put_kv_data

        async def fail_cooldown(key, value):
            if key == CASES_KEY:
                raise OSError("cannot reserve")
            await original(key, value)

        plugin.put_kv_data = fail_cooldown
        assert (await plugin._reserve_ai_daily(event)).error == STORAGE_ERROR
        plugin.put_kv_data = original
        key, _ = plugin._daily_identity(event)
        assert all(not entries for entries in plugin._test_store[key]["days"].values())
        plugin._test_store[CASES_KEY] = {COOLDOWNS_KEY: {"20002": {"normal": "bad"}}}
        assert (await plugin._reserve_ai_daily(event)).error == STORAGE_ERROR

    asyncio.run(run())


def test_cancelled_reservation_releases_both_holds_and_expired_pending_recovers():
    async def run():
        plugin, event, now = setup()
        original = plugin.put_kv_data
        started, resume = asyncio.Event(), asyncio.Event()

        async def slow_write(key, value):
            await original(key, value)
            if key == CASES_KEY:
                started.set()
                await resume.wait()

        plugin.put_kv_data = slow_write
        task = asyncio.create_task(plugin._reserve_ai_daily(event))
        await started.wait()
        task.cancel()
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        plugin.put_kv_data = original
        assert not state(plugin)["pending"]
        pending = await plugin._reserve_ai_daily(event)
        now[0] += timedelta(seconds=PENDING_SECONDS + 1)
        event.message_obj.message_id = "after-crash"
        retry = await plugin._reserve_ai_daily(event)
        assert not retry.error
        await retry.release()
        await pending.release()

    asyncio.run(run())


def test_configuration_page_exposes_switches_and_both_units():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text("utf-8")
    )
    assert schema["ai_cooldown_enabled"]["type"] == "bool"
    assert schema["ai_cooldown_enabled"]["default"] is False
    assert schema["ai_first_cooldown_enabled"]["default"] is True
    assert schema["ai_first_cooldown_duration"]["default"] == 7
    assert schema["ai_normal_cooldown_duration"]["default"] == 24
    for prefix in ("ai_first_cooldown", "ai_normal_cooldown"):
        assert schema[prefix + "_unit"]["options"] == ["小时", "天"]
