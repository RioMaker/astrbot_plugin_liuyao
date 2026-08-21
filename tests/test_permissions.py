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


class _Star:
    def __init__(self, context):
        self.context = context


class _Filter:
    command = staticmethod(_identity_decorator)
    llm_tool = staticmethod(_identity_decorator)


class _Logger:
    info = staticmethod(lambda *args, **kwargs: None)
    warning = staticmethod(lambda *args, **kwargs: None)


astrbot = types.ModuleType("astrbot")
api = types.ModuleType("astrbot.api")
event_api = types.ModuleType("astrbot.api.event")
star_api = types.ModuleType("astrbot.api.star")
core = types.ModuleType("astrbot.core")
core_star = types.ModuleType("astrbot.core.star")
core_filter = types.ModuleType("astrbot.core.star.filter")
command_api = types.ModuleType("astrbot.core.star.filter.command")
api.logger = _Logger()
event_api.AstrMessageEvent = object
event_api.filter = _Filter()
star_api.Context = object
star_api.Star = _Star
star_api.register = _identity_decorator
command_api.GreedyStr = str
sys.modules.setdefault("astrbot", astrbot)
sys.modules.setdefault("astrbot.api", api)
sys.modules.setdefault("astrbot.api.event", event_api)
sys.modules.setdefault("astrbot.api.star", star_api)
sys.modules.setdefault("astrbot.core", core)
sys.modules.setdefault("astrbot.core.star", core_star)
sys.modules.setdefault("astrbot.core.star.filter", core_filter)
sys.modules.setdefault("astrbot.core.star.filter.command", command_api)

from corpus import ZhouyiCorpus  # noqa: E402
from main import LiuyaoPlugin, METHOD_LIUYAO  # noqa: E402
from reading import ReadingService  # noqa: E402


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


def _make_enabled_plugin() -> LiuyaoPlugin:
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {"show_disclaimer": False}
    plugin.corpus = ZhouyiCorpus(ROOT / "data" / "zhouyi.json")
    plugin.readings = ReadingService(
        plugin.corpus,
        ROOT / "data" / "intents.json",
    )

    async def get_data(key, default=None):
        if key == "method_switches":
            return {"10001": {"liuyao": True}}
        return default

    plugin.get_kv_data = get_data
    return plugin


def test_owner_and_group_admin_are_accepted() -> None:
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {"allow_operator_api_lookup": False}
    assert asyncio.run(plugin._is_group_operator(_Event("owner"))) is True
    assert asyncio.run(plugin._is_group_operator(_Event("admin"))) is True
    assert asyncio.run(plugin._is_group_operator(_Event("member"))) is False


def test_missing_role_can_use_read_only_onebot_lookup() -> None:
    class Bot:
        async def call_action(self, **kwargs):
            assert kwargs["action"] == "get_group_member_info"
            return {"role": "admin"}

    event = _Event(None)
    event.bot = Bot()
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {"allow_operator_api_lookup": True}
    assert asyncio.run(plugin._is_group_operator(event)) is True


def test_switch_state_is_saved_per_group_and_method() -> None:
    store = {}
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {}
    plugin._switch_lock = asyncio.Lock()

    async def get_data(key, default=None):
        return store.get(key, default)

    async def put_data(key, value):
        store[key] = value

    plugin.get_kv_data = get_data
    plugin.put_kv_data = put_data

    asyncio.run(plugin._set_method_enabled("10001", METHOD_LIUYAO, True))
    assert store["method_switches"] == {"10001": {"liuyao": True}}
    assert asyncio.run(plugin._is_method_enabled("10001", METHOD_LIUYAO)) is True
    assert asyncio.run(plugin._is_method_enabled("10002", METHOD_LIUYAO)) is False


def test_legacy_group_switch_is_read_for_liuyao() -> None:
    store = {"group_switches": {"10001": True}}
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {}

    async def get_data(key, default=None):
        return store.get(key, default)

    plugin.get_kv_data = get_data
    assert asyncio.run(plugin._is_method_enabled("10001", METHOD_LIUYAO)) is True


def test_source_registers_flat_commands_and_agent_tools() -> None:
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert '@filter.command("六爻"' in source
    assert '@filter.command("起卦"' in source
    assert "content: GreedyStr" in source
    assert "command_group" not in source
    assert '@filter.llm_tool(name="cast_liuyao")' in source
    assert '@filter.llm_tool(name="lookup_zhouyi_text")' in source
    assert "群主或 QQ 群管理员" in source
    compile(source, str(ROOT / "main.py"), "exec")


def test_manual_command_accepts_direct_hexagram_name() -> None:
    plugin = _make_enabled_plugin()
    result = asyncio.run(
        plugin._manual_reply(
            _Event("member"),
            "乾为天 事业 这个项目如何推进",
        )
    )
    assert "手动指定卦名（乾静卦）" in result
    assert "本卦：䷀ 第1卦 乾" in result
    assert "之卦：无动爻" in result
    assert "所问：这个项目如何推进" in result


def test_bare_liuyao_content_casts_instantly() -> None:
    plugin = _make_enabled_plugin()
    result = asyncio.run(
        plugin._dispatch_liuyao(
            _Event("member"),
            "今年适合换工作吗",
        )
    )
    assert "六爻问卦｜传统文化参考" in result
    assert "方式：即时天机（简捷问卦）" in result
    assert "意图：综合" in result
    assert "所问：今年适合换工作吗" in result


def test_bare_liuyao_content_recognizes_intent_prefix() -> None:
    plugin = _make_enabled_plugin()
    result = asyncio.run(
        plugin._dispatch_liuyao(
            _Event("member"),
            "事业 今年适合换工作吗",
        )
    )
    assert "意图：事业" in result
    assert "所问：今年适合换工作吗" in result


def test_reserved_subcommands_remain_available() -> None:
    plugin = _make_enabled_plugin()
    help_result = asyncio.run(
        plugin._dispatch_liuyao(_Event("member"), "help")
    )
    instant_result = asyncio.run(
        plugin._dispatch_liuyao(
            _Event("member"),
            "即时 感情 这段关系如何发展",
        )
    )
    assert "/六爻 <问卦内容>" in help_result
    assert "方式：即时天机（三枚铜币等概率模拟）" in instant_result
    assert "意图：感情" in instant_result
    assert "所问：这段关系如何发展" in instant_result
