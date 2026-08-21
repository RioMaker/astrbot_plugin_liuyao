from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _identity_decorator(*args, **kwargs):
    del args, kwargs

    def decorate(value):
        return value

    return decorate


def _command_group(*args, **kwargs):
    del args, kwargs

    def decorate(func):
        func.command = _identity_decorator
        return func

    return decorate


class _Star:
    def __init__(self, context):
        self.context = context


class _Filter:
    command_group = staticmethod(_command_group)
    llm_tool = staticmethod(_identity_decorator)


class _Logger:
    info = staticmethod(lambda *args, **kwargs: None)
    warning = staticmethod(lambda *args, **kwargs: None)


astrbot = types.ModuleType("astrbot")
api = types.ModuleType("astrbot.api")
event_api = types.ModuleType("astrbot.api.event")
star_api = types.ModuleType("astrbot.api.star")
api.logger = _Logger()
event_api.AstrMessageEvent = object
event_api.filter = _Filter()
star_api.Context = object
star_api.Star = _Star
star_api.register = _identity_decorator
sys.modules.setdefault("astrbot", astrbot)
sys.modules.setdefault("astrbot.api", api)
sys.modules.setdefault("astrbot.api.event", event_api)
sys.modules.setdefault("astrbot.api.star", star_api)

from main import LiuyaoPlugin  # noqa: E402


class _Message:
    def __init__(self, role):
        self.raw_message = {"sender": {"role": role}}


class _Event:
    def __init__(self, role):
        self.message_obj = _Message(role)

    def get_group_id(self):
        return "10001"

    def get_sender_id(self):
        return "20002"


def test_only_owner_role_is_accepted() -> None:
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {"allow_owner_api_lookup": False}
    assert asyncio.run(plugin._is_group_owner(_Event("owner"))) is True
    assert asyncio.run(plugin._is_group_owner(_Event("admin"))) is False
    assert asyncio.run(plugin._is_group_owner(_Event("member"))) is False


def test_missing_role_can_use_read_only_onebot_lookup() -> None:
    class Bot:
        async def call_action(self, **kwargs):
            assert kwargs["action"] == "get_group_member_info"
            return {"role": "owner"}

    event = _Event(None)
    event.bot = Bot()
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {"allow_owner_api_lookup": True}
    assert asyncio.run(plugin._is_group_owner(event)) is True


def test_source_registers_two_agent_tools_and_owner_switch() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert '@filter.llm_tool(name="cast_liuyao")' in source
    assert '@filter.llm_tool(name="lookup_zhouyi_text")' in source
    assert "只有当前 QQ 群群主" in source
    compile(source, str(ROOT / "main.py"), "exec")
