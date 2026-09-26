import asyncio

from test_permissions import _Event, _make_enabled_plugin

import cast_policy


def test_empty_casual_and_direction_only_requests_never_generate(monkeypatch):
    plugin = _make_enabled_plugin()

    def forbidden():
        raise AssertionError("rejected requests must not generate a cast")

    monkeypatch.setattr(cast_policy, "cast_instant", forbidden)

    async def check():
        for question in ["", "随便看看", "没什么事，随便起一卦", "事业", "试试"]:
            assert "无事不卜" in await plugin._instant_reply(_Event("member"), question)
            assert "无事不卜" in await plugin.cast_liuyao_tool(_Event("member"), question=question)
        assert "无事不卜" in await plugin._manual_reply(_Event("member"), "乾 随便看看")

    asyncio.run(check())
    assert not any(key.startswith("cast_policy:") for key in plugin._test_store)


def test_rolling_window_shared_across_groups_and_entrypoints(monkeypatch):
    plugin = _make_enabled_plugin()
    plugin._test_store["method_switches"]["10002"] = {"liuyao": True}
    now = [10000.0]
    monkeypatch.setattr(cast_policy.time, "time", lambda: now[0])
    event = _Event("member")
    other_group = _Event("member")
    other_group.get_group_id = lambda: "10002"

    async def check():
        await plugin._content_reply(event, "这个项目能否签约")
        now[0] += 10
        await plugin._manual_reply(other_group, "乾 这次考试能否通过")
        now[0] += 10
        await plugin.cast_liuyao_tool(event, question="明天杭州是否下雨")
        assert len(plugin._test_store["cast_policy:20002"]["timestamps"]) == 3
        assert "每小时最多" in await plugin._content_reply(event, "这次面试能否录用")
        other_user = _Event("member")
        other_user.get_sender_id = lambda: "different-user"
        assert "本卦" in await plugin._content_reply(other_user, "这次面试能否录用")
        now[0] = 13599
        assert "每小时最多" in await plugin._content_reply(event, "这次面试能否录用")
        now[0] = 13600
        assert "本卦" in await plugin._content_reply(event, "这次面试能否录用")

    asyncio.run(check())


def test_duplicate_manual_and_instant_reuse_survives_reload(monkeypatch):
    plugin = _make_enabled_plugin()
    event = _Event("member")

    async def check():
        await plugin._manual_reply(event, "7 8 9 6 7 8 事业 这个项目能否签约？")
        # A fresh instance with the same persistent store must keep the restriction.
        restored = _make_enabled_plugin()
        restored.get_kv_data = plugin.get_kv_data
        restored.put_kv_data = plugin.put_kv_data
        assert "禁止连续" in await restored.cast_liuyao_tool(event, question="这个项目能否签约")
        assert "禁止连续" in await restored._manual_reply(event, "乾 这个项目能否签约")
        record = plugin._test_store["cast_policy:20002"]
        for _ in range(5):
            text = await restored.reuse_liuyao_tool(event)
            assert "7 8 9 6 7 8" in text
            assert "复用原卦" in text
        assert plugin._test_store["cast_policy:20002"] == record
        monkeypatch.setattr(cast_policy.time, "time", lambda: record["last"]["timestamp"] + 3601)
        assert "禁止连续" in await restored._content_reply(event, "这个项目能否签约！")
        assert "复用原卦" in await restored._dispatch_liuyao(event, "解读")

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
                plugin._content_reply(_Event("member"), f"第 {index} 个项目能否签约")
                for index in range(8)
            ]
        )
        assert sum("本卦" in reply for reply in replies) == 3
        assert sum("每小时最多" in reply for reply in replies) == 5

    asyncio.run(check())


def test_invalid_manual_input_and_storage_failure_do_not_publish_cast():
    plugin = _make_enabled_plugin()
    event = _Event("member")
    assert "输入有误" in asyncio.run(plugin._manual_reply(event, "1 2 3 这个项目能否签约"))
    assert "cast_policy:20002" not in plugin._test_store

    async def fail(*args):
        raise OSError("storage unavailable")

    plugin.put_kv_data = fail
    reply = asyncio.run(plugin._content_reply(event, "这个项目能否签约"))
    assert "保存失败" in reply
    assert "本卦" not in reply
    assert not event.sent


def test_reuse_at_quota_and_group_isolation():
    plugin = _make_enabled_plugin()
    event = _Event("member")
    plugin._test_store["method_switches"]["10002"] = {"liuyao": True}

    async def check():
        for question in ["这个项目能否签约", "这次考试能否通过", "明天杭州是否下雨"]:
            await plugin._content_reply(event, question)
        before = list(plugin._test_store["cast_policy:20002"]["timestamps"])
        assert "复用原卦" in await plugin.reuse_liuyao_tool(event)
        assert plugin._test_store["cast_policy:20002"]["timestamps"] == before
        elsewhere = _Event("member")
        elsewhere.get_group_id = lambda: "10002"
        assert "回原群" in await plugin.reuse_liuyao_tool(elsewhere)
        outsider = _Event("member")
        outsider.get_sender_id = lambda: "other-user"
        assert "尚无" in await plugin.reuse_liuyao_tool(outsider)

    asyncio.run(check())


def test_concurrent_same_matter_is_cast_only_once():
    plugin = _make_enabled_plugin()

    async def check():
        replies = await asyncio.gather(
            *[plugin._content_reply(_Event("member"), "这个项目能否签约") for _ in range(4)]
        )
        assert sum("本卦" in reply for reply in replies) == 1
        assert sum("禁止连续" in reply for reply in replies) == 3

    asyncio.run(check())
