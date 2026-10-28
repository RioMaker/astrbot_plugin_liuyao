from __future__ import annotations

import ast
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_permissions import _Event, _make_enabled_plugin

from main import AGENT_ACTION_TOOL_NAMES, HELP_TEXT

ROOT = Path(__file__).resolve().parents[1]
ACTION_CALLS = [
    ("cast_liuyao_tool", {"question": "这个项目能否按期完成"}),
    ("reuse_liuyao_tool", {}),
    ("lookup_liuyao_reference_tool", {"topic": "事业"}),
    ("lookup_zhouyi_text_tool", {"hexagram": 1}),
    ("search_liuyao_cases_tool", {"query": "项目"}),
    ("record_liuyao_feedback_tool", {"feedback": "项目已按期完成"}),
]


@pytest.mark.parametrize(("method", "kwargs"), ACTION_CALLS)
def test_disabled_tools_return_help_without_reading_or_mutating_data(method, kwargs):
    async def run():
        plugin = _make_enabled_plugin()
        plugin.config["agent_tools_enabled"] = False
        before = copy.deepcopy(plugin._test_store)
        plugin.get_kv_data = AsyncMock(side_effect=AssertionError("must not read data"))
        plugin.put_kv_data = AsyncMock(side_effect=AssertionError("must not write data"))
        plugin.context = SimpleNamespace(
            llm_generate=AsyncMock(side_effect=AssertionError("must not call a model"))
        )
        # An opaque event also proves the gate runs before platform/group access.
        result = await getattr(plugin, method)(object(), **kwargs)
        assert "已关闭 AI 直接使用" in result
        assert "请指导用户亲自发送" in result
        assert HELP_TEXT in result
        assert plugin._test_store == before
        plugin.get_kv_data.assert_not_awaited()
        plugin.put_kv_data.assert_not_awaited()
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("enabled", [False, True])
def test_help_is_available_without_a_group_or_quota(enabled):
    plugin = _make_enabled_plugin()
    plugin.config.update(agent_tools_enabled=enabled, ai_daily_limit=1)
    before = copy.deepcopy(plugin._test_store)
    result = asyncio.run(plugin.get_liuyao_help_tool(object()))
    assert HELP_TEXT in result
    assert ("当前允许" if enabled else "当前已关闭") in result
    assert plugin._test_store == before


def test_disabled_request_hides_actions_and_preserves_help_and_other_plugins():
    async def run():
        plugin = _make_enabled_plugin()
        plugin.config["agent_tools_enabled"] = False
        names = [*sorted(AGENT_ACTION_TOOL_NAMES), "get_liuyao_help", "other_plugin_tool"]
        shared = SimpleNamespace(tools=[SimpleNamespace(name=name) for name in names])
        request = SimpleNamespace(func_tool=shared)
        await plugin.configure_liuyao_agent_tools(object(), request)
        assert [tool.name for tool in request.func_tool.tools] == [
            "get_liuyao_help",
            "other_plugin_tool",
        ]
        assert [tool.name for tool in shared.tools] == names
        assert request.func_tool is not shared
        assert request.func_tool.tools[-1] is shared.tools[-1]
        await plugin.configure_liuyao_agent_tools(object(), request)
        assert [tool.name for tool in request.func_tool.tools] == [
            "get_liuyao_help",
            "other_plugin_tool",
        ]

    asyncio.run(run())


@pytest.mark.parametrize("toolset", [None, SimpleNamespace(tools=[])])
def test_disabled_request_does_not_enable_tools_excluded_by_persona(toolset):
    plugin = _make_enabled_plugin()
    plugin.config["agent_tools_enabled"] = False
    request = SimpleNamespace(func_tool=toolset)
    asyncio.run(plugin.configure_liuyao_agent_tools(object(), request))
    assert request.func_tool is None or request.func_tool.tools == []


@pytest.mark.parametrize("setting", [None, True])
def test_enabled_or_legacy_config_keeps_tools_and_existing_group_rules(setting):
    async def run():
        plugin = _make_enabled_plugin()
        if setting is not None:
            plugin.config["agent_tools_enabled"] = setting
        toolset = SimpleNamespace(tools=[SimpleNamespace(name="cast_liuyao")])
        request = SimpleNamespace(func_tool=toolset)
        await plugin.configure_liuyao_agent_tools(object(), request)
        assert request.func_tool is toolset
        assert "乾" in await plugin.lookup_zhouyi_text_tool(_Event("member"), 1)
        closed = _Event("member")
        closed.get_group_id = lambda: "closed"
        assert "尚未开启" in await plugin.lookup_zhouyi_text_tool(closed, 1)

    asyncio.run(run())


def test_manual_command_still_runs_ai_review_and_interpretation_when_tools_disabled():
    async def run():
        plugin = _make_enabled_plugin()
        plugin.config.update(
            agent_tools_enabled=False,
            ai_daily_limit=1,
            case_library_enabled=False,
        )
        plugin.context = SimpleNamespace(
            get_current_chat_provider_id=AsyncMock(return_value="local-test-model"),
            llm_generate=AsyncMock(
                side_effect=[
                    SimpleNamespace(completion_text='{"reject":false,"intent":"事业"}'),
                    SimpleNamespace(completion_text="先落实合同条件。\n断语：条件落实后再推进。"),
                ]
            ),
        )
        plugin._render_and_send_chart = AsyncMock(return_value="已发送")
        plugin._send_reply_payload = AsyncMock()
        event = _Event("member")
        event.message_obj.message_id = "manual-command"
        assert await plugin._dispatch_liuyao(event, "这个项目能否按期完成") == ""
        assert plugin.context.llm_generate.await_count == 2
        plugin._render_and_send_chart.assert_awaited_once()
        plugin._send_reply_payload.assert_awaited_once()
        assert "断语" in plugin._send_reply_payload.call_args.args[3]
        assert await plugin._dispatch_liuyao(event, "") == HELP_TEXT
        assert "卦例库已由配置关闭" in await plugin._feedback_reply(event, "")
        closed = _Event("member")
        closed.get_group_id = lambda: "closed"
        assert "尚未开启" in await plugin._dispatch_liuyao(closed, "这个项目能否按期完成")
        assert plugin.context.llm_generate.await_count == 2

    asyncio.run(run())


def test_switch_schema_and_action_registry_cover_all_functional_tools():
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema["agent_tools_enabled"]["type"] == "bool"
    assert schema["agent_tools_enabled"]["default"] is True
    tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
    registered = {
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "llm_tool"
        for keyword in decorator.keywords
        if keyword.arg == "name"
    }
    assert registered == AGENT_ACTION_TOOL_NAMES | {"get_liuyao_help"}
