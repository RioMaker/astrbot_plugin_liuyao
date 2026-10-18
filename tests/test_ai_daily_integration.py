from __future__ import annotations

import asyncio
import copy
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from test_permissions import _Event, _make_enabled_plugin

from divination import CastResult


class Model:
    def __init__(self, *, failure=False):
        self.calls = []
        self.failure = failure

    async def get_current_chat_provider_id(self, *, umo):
        return "provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure:
            raise RuntimeError("provider unavailable")
        return SimpleNamespace(completion_text="依据原卦分析。\n断语：先落实条件再推进。")


def make_plugin(**settings):
    plugin = _make_enabled_plugin()
    plugin.config = {
        "ai_daily_limit": 1,
        "cast_max_per_window": 0,
        "agent_send_chart_image": False,
        "command_send_chart_image": False,
        "agent_generate_chart_comment": True,
        "case_library_enabled": False,
        **settings,
    }
    plugin.context = Model()
    plugin._ai_daily_now = lambda: datetime(2026, 10, 18, 12, tzinfo=timezone(timedelta(hours=8)))
    return plugin


def event(message_id, *, group="10001", user="20002"):
    value = _Event("member")
    value.message_obj.message_id = message_id
    value.get_group_id = lambda: group
    value.get_sender_id = lambda: user
    value.unified_msg_origin = f"aiocqhttp:GroupMessage:{group}"
    return value


def used_count(plugin):
    return sum(
        item["used"]
        for key, record in plugin._test_store.items()
        if key.startswith("ai_daily_quota_v1:")
        for entries in record["days"].values()
        for item in entries.values()
    )


def test_cast_comment_reuse_and_recovery_share_one_message_charge():
    async def run():
        plugin = make_plugin()
        original = event("one")
        result = await plugin.cast_liuyao_tool(original, question="这个项目能否按期签约")
        assert "本卦" in result and len(plugin.context.calls) == 1
        assert "复用原卦" in await plugin.reuse_liuyao_tool(event("one"))
        await plugin.capture_liuyao_agent_analysis(original, object(), None)
        assert len(plugin.context.calls) == 2
        assert used_count(plugin) == 1
        assert "额度已用完" in await plugin.reuse_liuyao_tool(event("two"))
        assert "本卦" in await plugin._dispatch_liuyao(event("local"), "解读")
        assert len(plugin.context.calls) == 2 and used_count(plugin) == 1

    asyncio.run(run())


def test_local_commands_never_consume_or_obey_hourly_or_daily_counts():
    async def run():
        plugin = make_plugin(cast_max_per_window=1)
        await plugin.cast_liuyao_tool(event("ai"), question="今年合作事项能否顺利推进")
        for index in range(6):
            result = await plugin._content_reply(
                event(f"local-{index}"), f"第{index}项工程能否按期交付"
            )
            assert "本卦" in result
        assert used_count(plugin) == 1
        assert len(plugin._test_store["cast_policy:20002"]["timestamps"]) == 1
        assert len(plugin.context.calls) == 1
        assert "禁止连续" in await plugin._content_reply(event("same"), "第5项工程能否按期交付")

    asyncio.run(run())


def test_invalid_closed_and_missing_original_requests_do_not_consume():
    async def run():
        plugin = make_plugin()
        assert "无事不卜" in await plugin.cast_liuyao_tool(event("empty"), question="随便看看")
        assert "输入有误" in await plugin.cast_liuyao_tool(
            event("invalid"), mode="manual", manual_lines="bad", question="这个项目如何推进"
        )
        assert "尚未开启" in await plugin.cast_liuyao_tool(
            event("closed", group="closed"), question="这个项目如何推进"
        )
        assert "尚无" in await plugin.reuse_liuyao_tool(event("no-original"))
        assert used_count(plugin) == 0 and not plugin.context.calls
        assert "本卦" in await plugin.cast_liuyao_tool(event("valid"), question="这个项目如何推进")
        assert used_count(plugin) == 1

    asyncio.run(run())


def test_cast_storage_failure_releases_daily_reservation():
    async def run():
        plugin = make_plugin()
        save = plugin.put_kv_data

        async def fail_cast(key, value):
            if key.startswith("cast_policy:"):
                raise OSError("disk failure")
            await save(key, value)

        plugin.put_kv_data = fail_cast
        assert "保存失败" in await plugin.cast_liuyao_tool(
            event("failed"), question="这个项目如何推进"
        )
        assert used_count(plugin) == 0 and not plugin.context.calls
        plugin.put_kv_data = save
        assert "本卦" in await plugin.cast_liuyao_tool(event("retry"), question="这个项目如何推进")
        assert used_count(plugin) == 1

    asyncio.run(run())


def test_quota_commit_failure_restores_original_and_hourly_history_before_retry():
    async def run():
        plugin = make_plugin()
        await plugin._content_reply(event("original"), "原先的合作能否落实")
        plugin._test_store["cast_policy:20002"]["timestamps"] = [time.time() - 10]
        previous = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        save = plugin.put_kv_data

        async def fail_commit(key, value):
            if key.startswith("ai_daily_quota_v1:") and any(
                item["used"] for entries in value["days"].values() for item in entries.values()
            ):
                raise OSError("quota disk failure")
            await save(key, value)

        plugin.put_kv_data = fail_commit
        response = await plugin.cast_liuyao_tool(event("failure"), question="这个项目如何推进")
        assert "保存失败" in response and not plugin.context.calls
        assert used_count(plugin) == 0
        assert plugin._test_store["cast_policy:20002"] == previous
        assert "原先的合作能否落实" in await plugin._dispatch_liuyao(event("local"), "解读")
        plugin.put_kv_data = save
        assert "本卦" in await plugin.cast_liuyao_tool(event("retry"), question="这个项目如何推进")
        assert used_count(plugin) == 1

    asyncio.run(run())


def test_failed_confirmation_rollback_cannot_overwrite_a_concurrent_success():
    async def run():
        plugin = make_plugin(ai_daily_limit=2)
        await plugin._content_reply(event("original"), "原先的合作能否落实")
        previous = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        save = plugin.put_kv_data
        failed = False

        async def fail_first_confirmation(key, value):
            nonlocal failed
            if (
                not failed
                and key.startswith("ai_daily_quota_v1:")
                and any(
                    item["used"] for entries in value["days"].values() for item in entries.values()
                )
            ):
                failed = True
                raise OSError("first confirmation failed")
            if key == "cast_policy:20002" and value == previous:
                await asyncio.sleep(0)
            await save(key, value)

        plugin.put_kv_data = fail_first_confirmation
        results = await asyncio.gather(
            plugin.cast_liuyao_tool(event("first"), question="第一个项目能否签约"),
            plugin.cast_liuyao_tool(event("second"), question="第二个项目能否签约"),
        )
        assert "保存失败" in results[0] and "本卦" in results[1]
        assert plugin._test_store["cast_policy:20002"]["last"]["question"] == "第二个项目能否签约"
        assert len(plugin._test_store["cast_policy:20002"]["timestamps"]) == 1
        assert used_count(plugin) == 1 and len(plugin.context.calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["comment", "shefu", "recovery"])
def test_empty_provider_never_consumes_or_calls_ai(phase):
    async def run():
        plugin = make_plugin()

        async def empty_provider(**kwargs):
            return ""

        plugin.context.get_current_chat_provider_id = empty_provider
        current = event("no-provider")
        if phase == "comment":
            await plugin._enrich_agent_chart(
                current,
                CastResult((7,) * 6),
                intent="career",
                question="项目如何推进",
                agent_name="",
            )
        elif phase == "shefu":
            assert "本卦" in await plugin._dispatch_liuyao(current, "手动 乾 射覆 盒中何物")
        else:
            await plugin._recover_agent_analysis(current, {"readings": ["保存的原卦材料"]})
        assert used_count(plugin) == 0 and not plugin.context.calls

    asyncio.run(run())


def test_ai_history_search_counts_but_local_history_and_empty_search_are_free():
    async def run():
        plugin = make_plugin(case_library_enabled=True)
        assert "没有找到" in await plugin.search_liuyao_cases_tool(event("empty"))
        assert "必须是数字" in await plugin.search_liuyao_cases_tool(event("bad"), hexagram="bad")
        assert used_count(plugin) == 0
        assert "本卦" in await plugin.cast_liuyao_tool(event("cast"), question="这个项目如何推进")
        assert "找到以下历史" in await plugin.search_liuyao_cases_tool(event("cast"))
        assert used_count(plugin) == 1
        assert "额度已用完" in await plugin.search_liuyao_cases_tool(event("new-reading"))
        admin = event("local-history")
        admin.is_admin = lambda: True
        assert "卦例清单" in await plugin._dispatch_liuyao(admin, "卦例")
        assert used_count(plugin) == 1 and len(plugin.context.calls) == 1

    asyncio.run(run())


def test_shefu_provider_lookup_failure_before_ai_does_not_consume():
    async def run():
        plugin = make_plugin()

        async def unavailable(**kwargs):
            raise RuntimeError("provider missing")

        plugin.context.get_current_chat_provider_id = unavailable
        result = await plugin._dispatch_liuyao(event("shefu"), "手动 乾 射覆 盒中何物")
        assert "本卦" in result and "模型调用失败" in result
        assert not plugin.context.calls and used_count(plugin) == 0

    asyncio.run(run())


def test_group_override_and_group_and_user_isolation_survive_reload():
    async def run():
        plugin = make_plugin(ai_daily_group_limits=[{"group_id": "10001", "limit": 2}])
        plugin._test_store["method_switches"]["10002"] = {"liuyao": True}
        await plugin._content_reply(event("local"), "这个项目如何推进")
        assert "复用原卦" in await plugin.reuse_liuyao_tool(event("a"))
        assert "复用原卦" in await plugin.reuse_liuyao_tool(event("b"))
        restored = make_plugin(**plugin.config)
        restored.get_kv_data = plugin.get_kv_data
        restored.put_kv_data = plugin.put_kv_data
        assert "额度已用完" in await restored.reuse_liuyao_tool(event("c"))
        assert "复用原卦" in await restored.reuse_liuyao_tool(event("a"))
        assert "本卦" in await plugin.cast_liuyao_tool(
            event("other-group", group="10002"), question="这次考试能否通过"
        )
        assert "本卦" in await plugin.cast_liuyao_tool(
            event("other-user", user="other-user"), question="这次考试能否通过"
        )
        assert used_count(plugin) == 4

    asyncio.run(run())


def test_concurrent_tool_requests_cannot_exceed_daily_limit():
    async def run():
        plugin = make_plugin()
        results = await asyncio.gather(
            *[
                plugin.cast_liuyao_tool(
                    event(f"message-{index}"), question=f"第{index}项工程能否交付"
                )
                for index in range(6)
            ]
        )
        assert sum("本卦" in result for result in results) == 1
        assert sum("额度已用完" in result for result in results) == 5
        assert used_count(plugin) == 1 and len(plugin.context.calls) == 1

    asyncio.run(run())


@pytest.mark.parametrize("disabled", [False, True])
def test_shefu_command_keeps_local_cast_when_daily_exhausted_or_ai_disabled(disabled):
    async def run():
        plugin = make_plugin(command_ai_analysis_enabled=not disabled)
        if not disabled:
            await plugin.cast_liuyao_tool(event("ai"), question="这个项目如何推进")
        before = len(plugin.context.calls)
        result = await plugin._dispatch_liuyao(event("shefu"), "手动 乾 射覆 盒中何物")
        assert "本卦" in result
        assert ("AI 分析已关闭" if disabled else "额度已用完") in result
        assert len(plugin.context.calls) == before
        assert plugin._test_store["cast_policy:20002"]["last"]["lines"] == [7] * 6
        assert used_count(plugin) == (0 if disabled else 1)

    asyncio.run(run())


def test_shefu_success_and_model_failure_both_consume_after_request():
    async def run(failure):
        plugin = make_plugin()
        plugin.context = Model(failure=failure)
        result = await plugin._dispatch_liuyao(event("shefu"), "手动 乾 射覆 盒中何物")
        assert "本卦" in result and len(plugin.context.calls) == 1
        assert used_count(plugin) == 1
        assert "额度已用完" in await plugin.reuse_liuyao_tool(event("new-reading"))

    for failure in (False, True):
        asyncio.run(run(failure))


def test_cancellation_before_cast_save_releases_reservation():
    async def run():
        plugin = make_plugin()
        entered = asyncio.Event()
        original = plugin._prepare_cast

        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        plugin._prepare_cast = blocked
        task = asyncio.create_task(
            plugin.cast_liuyao_tool(event("cancel"), question="这个项目如何推进")
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        plugin._prepare_cast = original
        assert used_count(plugin) == 0 and not plugin.context.calls
        assert "本卦" in await plugin.cast_liuyao_tool(event("retry"), question="这个项目如何推进")

    asyncio.run(run())
