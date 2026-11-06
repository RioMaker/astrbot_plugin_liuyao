from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from test_agent_analysis import QUESTION, prepare
from test_ai_daily_integration import used_count
from test_feedback_review import case, decision, saved, setup
from test_permissions import _Event, _make_enabled_plugin
from test_user_management import ban

from user_management import BANNED_MESSAGE


def command_event(text, *, awake=True):
    event = _Event("member")
    event.message_str = text
    event.is_at_or_wake_command = awake
    event.stop_event = Mock()
    return event


async def compact(plugin, event):
    return [reply async for reply in plugin.compact_command(event)]


@pytest.mark.parametrize(
    "message,route,content",
    [
        ("/六爻今年能换工作吗", "cast", "今年能换工作吗"),
        ("六爻开", "cast", "开"),
        ("/六爻关", "cast", "关"),
        ("/liunyao今年能换工作吗", "cast", "今年能换工作吗"),
        ("/起卦六爻今年能换工作吗", "cast", "今年能换工作吗"),
        ("起卦六爻 开", "cast", "开"),
        ("/起卦六爻", "cast", ""),
        ("/divinationliunyao今年能换工作吗", "cast", "今年能换工作吗"),
        ("/六爻反馈302", "feedback", "302"),
        ("六爻反馈302 项目已完成", "feedback", "302 项目已完成"),
        ("/六爻反馈列表2", "feedback", "列表2"),
        ("/六爻test项目能否按期完成", "debug", "项目能否按期完成"),
        (" /六爻今年  能换工作吗\n想在年底前换  ", "cast", "今年  能换工作吗\n想在年底前换"),
    ],
)
def test_compact_routes_to_one_existing_handler_and_preserves_content(message, route, content):
    async def run():
        plugin = _make_enabled_plugin()
        handlers = {name: AsyncMock(return_value=name) for name in ("cast", "feedback", "debug")}
        plugin._dispatch_liuyao = handlers["cast"]
        plugin._feedback_command_reply = handlers["feedback"]
        plugin._run_debug = handlers["debug"]
        event = command_event(message)
        assert await compact(plugin, event) == [{"text": route}]
        handlers[route].assert_awaited_once_with(event, content)
        assert sum(handler.await_count for handler in handlers.values()) == 1
        assert event.stop_event.called

    asyncio.run(run())


@pytest.mark.parametrize(
    "message",
    [
        "/六爻",
        "/六爻 问题",
        "六爻\t问题",
        "六爻\u3000问题",
        "六爻\n问题",
        "/六爻反馈",
        "六爻反馈 302 项目已完成",
        "/六爻test",
        "六爻test 问题",
        "/起卦 六爻开",
        "divination liunyao问题",
        "liunyao 问题",
        "请用/六爻帮我看看",
        "聊聊六爻有什么用",
        "/额度查询",
        "/pingpong",
        "/其他指令",
    ],
)
def test_standard_commands_and_unrelated_messages_are_not_handled_twice(message):
    async def run():
        plugin = _make_enabled_plugin()
        plugin._dispatch_liuyao = AsyncMock()
        plugin._feedback_command_reply = AsyncMock()
        plugin._run_debug = AsyncMock()
        event = command_event(message)
        assert await compact(plugin, event) == []
        plugin._dispatch_liuyao.assert_not_awaited()
        plugin._feedback_command_reply.assert_not_awaited()
        plugin._run_debug.assert_not_awaited()
        event.stop_event.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("message", ["六爻今年能换工作吗", "/六爻开", "六爻反馈302"])
def test_regex_does_not_bypass_framework_wake_rules(message):
    async def run():
        plugin = _make_enabled_plugin()
        plugin._dispatch_liuyao = AsyncMock()
        plugin._feedback_command_reply = AsyncMock()
        event = command_event(message, awake=False)
        assert await compact(plugin, event) == []
        plugin._dispatch_liuyao.assert_not_awaited()
        plugin._feedback_command_reply.assert_not_awaited()
        event.stop_event.assert_not_called()

    asyncio.run(run())


@pytest.mark.parametrize("content", ["六爻开", "六爻 开", "LIUNYAO开"])
def test_spaced_outer_command_accepts_compact_method_argument(content):
    async def run():
        plugin = _make_enabled_plugin()
        plugin._dispatch_liuyao = AsyncMock(return_value="已开启")
        event = command_event("起卦 " + content)
        assert [reply async for reply in plugin.divination_command(event, content)] == [
            {"text": "已开启"}
        ]
        plugin._dispatch_liuyao.assert_awaited_once_with(event, "开")

    asyncio.run(run())


@pytest.mark.parametrize("message", ["六爻开", "六爻关", "起卦六爻开", "六爻test测试问题"])
def test_compact_commands_keep_administrator_checks(message):
    async def run():
        plugin, _ = prepare()
        plugin.config["allow_operator_api_lookup"] = False
        event = command_event(message)
        replies = await compact(plugin, event)
        assert "无权限" in replies[0]["text"]
        plugin.context.llm_generate.assert_not_awaited()
        assert plugin._test_store["method_switches"]["10001"]["liuyao"] is True

    asyncio.run(run())


@pytest.mark.parametrize("message", ["六爻问题", "起卦六爻问题", "六爻反馈302", "六爻test问题"])
def test_compact_commands_keep_user_ban_checks(message):
    async def run():
        plugin, _ = prepare()
        event = command_event(message)
        plugin._test_store["liuyao_case_library"] = {"cases": [case()]}
        await plugin._ensure_user_registry()
        await ban(plugin)
        plugin._send_feedback_image = AsyncMock(return_value=False)
        event._astrbot_admin = True
        replies = await compact(plugin, event)
        assert BANNED_MESSAGE in replies[0]["text"]
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("prefix", ["六爻", "起卦六爻"])
def test_compact_casts_once_charges_once_and_obeys_group_switch(prefix):
    async def run():
        plugin, event = prepare(limit=1)
        event.message_str = prefix + QUESTION
        event.is_at_or_wake_command = True
        event.stop_event = Mock()
        assert await compact(plugin, event) == []
        assert plugin.context.llm_generate.await_count == 2
        plugin._render_and_send_chart.assert_awaited_once()
        assert used_count(plugin) == 1
        event.stop_event.assert_called_once()

        event.message_obj.message_id = "second"
        assert await compact(plugin, event) == []
        assert plugin.context.llm_generate.await_count == 2
        assert used_count(plugin) == 1
        assert "额度已用完" in plugin._send_caster_reply.call_args.args[3]

        plugin._test_store["method_switches"]["10001"]["liuyao"] = False
        replies = await compact(plugin, event)
        assert "未开启" in replies[0]["text"]
        assert plugin.context.llm_generate.await_count == 2

    asyncio.run(run())


@pytest.mark.parametrize("tail,page", [("列表2", 2), ("列表 2", 2), ("列表\u30002", 2)])
def test_compact_feedback_pagination(tail, page):
    async def run():
        plugin, _ = setup(records=[case(n) for n in range(1, 11)])
        plugin._send_feedback_image = AsyncMock(return_value=True)
        assert await compact(plugin, command_event("六爻反馈" + tail)) == []
        args = plugin._send_feedback_image.call_args
        assert args.args[1] == "render_queue"
        assert args.kwargs["page"] == page
        assert [record["serial"] for record in args.kwargs["cases"]] == [9, 10]
        assert not plugin.context.calls

    asyncio.run(run())


def test_compact_feedback_preserves_number_body_and_single_review():
    async def run():
        body = "项目  已延期\n实际两周后才完成"
        plugin, _ = setup(decision("case-302"), records=[case(302), case(905)])
        plugin._send_feedback_image = AsyncMock(return_value=False)
        replies = await compact(plugin, command_event("六爻反馈302 " + body))
        assert "已闭环" in replies[0]["text"]
        assert len(plugin.context.calls) == 1
        assert json.loads(plugin.context.calls[0]["prompt"])["feedback"] == body
        assert saved(plugin, 302)["feedback"][0]["text"] == body
        assert saved(plugin, 905)["feedback"] == []

    asyncio.run(run())


def test_unnumbered_numeric_feedback_is_not_reinterpreted_as_case_number():
    async def run():
        plugin, _ = setup(decision("case-302"), records=[case(302)])
        plugin._send_feedback_image = AsyncMock(return_value=False)
        replies = await compact(plugin, command_event("六爻反馈3天后收到通知"))
        assert "已闭环" in replies[0]["text"]
        assert saved(plugin, 302)["feedback"][0]["text"] == "3天后收到通知"

    asyncio.run(run())


@pytest.mark.parametrize(
    "message,help_expected",
    [
        ("/六爻今年能换工作吗", False),
        ("六爻开", False),
        ("/起卦六爻今年能换工作吗", False),
        ("/六爻反馈302", False),
        ("/六爻test测试问题", False),
        ("/六爻反馈 列表 2", False),
        ("/额度", False),
        ("/ping", False),
        ("如何使用六爻", True),
        ("请看/六爻这个指令", True),
        ("/pingpong", True),
    ],
)
def test_bot_mention_does_not_add_help_to_compact_commands(message, help_expected):
    plugin = _make_enabled_plugin()
    plugin.config["mention_help_group_ids"] = "10001"
    plugin._event_mentions_bot = Mock(return_value=True)
    assert plugin._should_reply_mention_help(command_event(message)) is help_expected


def test_compact_reply_stops_propagation_after_reply_result_is_created():
    async def run():
        plugin = _make_enabled_plugin()
        plugin._dispatch_liuyao = AsyncMock(return_value="已处理")
        event = command_event("六爻开")

        def reply_result(text):
            event.stop_event.reset_mock()
            return {"text": text}

        event.plain_result = reply_result
        assert await compact(plugin, event) == [{"text": "已处理"}]
        event.stop_event.assert_called_once()

    asyncio.run(run())
