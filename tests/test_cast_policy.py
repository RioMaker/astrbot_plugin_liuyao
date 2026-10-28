"""Admission policies remain testable independently of command/tool adapters."""

import asyncio

import pytest
from test_permissions import _Event, _make_enabled_plugin

import cast_policy


async def _policy_cast(plugin, event, *, question):
    cast, error = await plugin._prepare_cast(
        event, None, question=question, intent="general", method="策略测试"
    )
    return error or plugin.readings.render(
        cast, intent="general", question=question, method="策略测试"
    )


def test_rolling_window_shared_across_groups_and_entrypoints(monkeypatch):
    plugin = _make_enabled_plugin()
    plugin._test_store["method_switches"]["10002"] = {"liuyao": True}
    now = [10000.0]
    monkeypatch.setattr(cast_policy.time, "time", lambda: now[0])
    event = _Event("member")
    other_group = _Event("member")
    other_group.get_group_id = lambda: "10002"

    async def check():
        await _policy_cast(plugin, event, question="这个项目能否签约")
        now[0] += 10
        await _policy_cast(plugin, other_group, question="这次考试能否通过")
        now[0] += 10
        await _policy_cast(plugin, event, question="明天杭州是否下雨")
        assert len(plugin._test_store["cast_policy:20002"]["timestamps"]) == 3
        assert "每小时最多" in await _policy_cast(plugin, event, question="这次面试能否录用")
        other_user = _Event("member")
        other_user.get_sender_id = lambda: "different-user"
        assert "本卦" in await _policy_cast(plugin, other_user, question="这次面试能否录用")
        now[0] = 13599
        assert "每小时最多" in await _policy_cast(plugin, event, question="这次面试能否录用")
        now[0] = 13600
        assert "本卦" in await _policy_cast(plugin, event, question="这次面试能否录用")

    asyncio.run(check())


def test_concurrent_requests_cannot_exceed_quota():
    plugin = _make_enabled_plugin()
    original = plugin.get_kv_data

    async def delayed_get(key, default=None):
        await asyncio.sleep(0)
        return await original(key, default)

    plugin.get_kv_data = delayed_get

    async def check():
        replies = await asyncio.gather(
            *[
                _policy_cast(plugin, _Event("member"), question=f"第 {index} 个项目能否签约")
                for index in range(8)
            ]
        )
        assert sum("本卦" in reply for reply in replies) == 3
        assert sum("每小时最多" in reply for reply in replies) == 5

    asyncio.run(check())


def test_lowering_quota_waits_until_enough_records_expire(monkeypatch):
    plugin = _make_enabled_plugin()
    plugin.config = {"ai_daily_limit": 0, "cast_cooldown_seconds": 120, "cast_max_per_window": 3}
    now = [10000.0]
    monkeypatch.setattr(cast_policy.time, "time", lambda: now[0])
    event = _Event("member")

    async def check():
        for question in ["这个项目能否签约", "这次考试能否通过", "明天杭州是否下雨"]:
            assert "本卦" in await _policy_cast(plugin, event, question=question)
            now[0] += 10
        plugin.config["cast_max_per_window"] = 1
        plugin._test_store["cast_policy:20002"]["timestamps"] = [10020, 10000, 10010]
        assert "约 2 分钟后" in await _policy_cast(plugin, event, question="这次面试能否录用")
        now[0] = 10120
        assert "约 20 秒后" in await _policy_cast(plugin, event, question="这次面试能否录用")
        now[0] = 10140
        assert "本卦" in await _policy_cast(plugin, event, question="这次面试能否录用")

    asyncio.run(check())


def test_custom_quota_is_atomic_under_concurrent_requests():
    plugin = _make_enabled_plugin()
    plugin.config = {"ai_daily_limit": 0, "cast_cooldown_seconds": 120, "cast_max_per_window": 2}
    original = plugin.get_kv_data

    async def delayed_get(key, default=None):
        await asyncio.sleep(0)
        return await original(key, default)

    plugin.get_kv_data = delayed_get

    async def check():
        replies = await asyncio.gather(
            *[
                _policy_cast(plugin, _Event("member"), question=f"第 {index} 个项目能否签约")
                for index in range(6)
            ]
        )
        assert sum("本卦" in reply for reply in replies) == 2
        assert sum("滚动 2 分钟内最多起卦 2 次" in reply for reply in replies) == 4
        assert len(plugin._test_store["cast_policy:20002"]["timestamps"]) == 2

    asyncio.run(check())


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("cast_cooldown_seconds", value)
        for value in (0, -1, 86401, True, 120.5, None, "bad", "120.0", [])
    ]
    + [("cast_max_per_window", value) for value in (-1, 101, False, 2.5, None, "bad", "2.0", {})],
)
def test_invalid_cooldown_configuration_falls_back_to_defaults(monkeypatch, key, value):
    plugin = _make_enabled_plugin()
    plugin.config = {"ai_daily_limit": 0, key: value}
    monkeypatch.setattr(cast_policy.time, "time", lambda: 10000)
    timestamps = [9800, 9810, 9820]
    plugin._test_store["cast_policy:20002"] = {"timestamps": timestamps}

    result = asyncio.run(_policy_cast(plugin, _Event("member"), question="这次面试能否录用"))

    assert "每小时最多起卦 3 次" in result
    assert plugin._test_store["cast_policy:20002"]["timestamps"] == timestamps
