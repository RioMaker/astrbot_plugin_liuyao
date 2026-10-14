from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

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
    EventMessageType = types.SimpleNamespace(GROUP_MESSAGE="group")
    event_message_type = staticmethod(_identity_decorator)
    command = staticmethod(_identity_decorator)
    llm_tool = staticmethod(_identity_decorator)
    on_agent_done = staticmethod(_identity_decorator)


class _Logger:
    info = staticmethod(lambda *args, **kwargs: None)
    warning = staticmethod(lambda *args, **kwargs: None)
    exception = staticmethod(lambda *args, **kwargs: None)


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
from divination import CastResult  # noqa: E402
from main import METHOD_LIUYAO, LiuyaoPlugin  # noqa: E402
from reading import ReadingService  # noqa: E402


class _Message:
    def __init__(self, role):
        self.raw_message = {"sender": {"role": role}}


class _Event:
    def __init__(self, role, *, astrbot_admin=False):
        self.message_obj = _Message(role)
        self.sent = []
        self.unified_msg_origin = "aiocqhttp:GroupMessage:10001"
        self._astrbot_admin = astrbot_admin

    def get_group_id(self):
        return "10001"

    def get_sender_id(self):
        return "20002"

    def get_sender_name(self):
        return "测试群友"

    def is_admin(self):
        return self._astrbot_admin

    def image_result(self, path):
        return {"image": path}

    def plain_result(self, text):
        return {"text": text}

    async def send(self, message):
        self.sent.append(message)


class _ChildContext:
    def __init__(self):
        self.calls = []

    async def get_current_chat_provider_id(self, **kwargs):
        return "test-provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        return types.SimpleNamespace(
            completion_text=(
                '{"analysis":"依据原卦判断。\\n断语：先落实条件再推进。",'
                '"summary":"此事宜先落实条件再推进。"}'
            )
        )


def _make_enabled_plugin() -> LiuyaoPlugin:
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {}
    plugin.context = _ChildContext()
    plugin.corpus = ZhouyiCorpus(ROOT / "data" / "zhouyi.json")
    plugin.readings = ReadingService(
        plugin.corpus,
        ROOT / "data" / "intents.json",
    )

    store = {"method_switches": {"10001": {"liuyao": True}}}

    async def get_data(key, default=None):
        return store.get(key, default)

    async def put_data(key, value):
        store[key] = value

    plugin.get_kv_data = get_data
    plugin.put_kv_data = put_data
    plugin._case_lock = asyncio.Lock()
    plugin._cast_lock = asyncio.Lock()
    plugin._test_store = store
    return plugin


def test_owner_group_admin_and_astrbot_admin_are_accepted() -> None:
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {"allow_operator_api_lookup": False}
    assert asyncio.run(plugin._can_manage_switch(_Event("owner"))) is True
    assert asyncio.run(plugin._can_manage_switch(_Event("admin"))) is True
    assert asyncio.run(plugin._can_manage_switch(_Event("member", astrbot_admin=True))) is True
    assert asyncio.run(plugin._can_manage_switch(_Event("member"))) is False


def test_astrbot_admin_can_enable_group_switch() -> None:
    store = {}
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = {"allow_operator_api_lookup": False}
    plugin._switch_lock = asyncio.Lock()

    async def get_data(key, default=None):
        return store.get(key, default)

    async def put_data(key, value):
        store[key] = value

    plugin.get_kv_data = get_data
    plugin.put_kv_data = put_data
    result = asyncio.run(
        plugin._switch_reply(
            _Event("member", astrbot_admin=True),
            METHOD_LIUYAO,
            True,
        )
    )
    assert result == "本群“六爻”功能已开启。"
    assert store["method_switches"] == {"10001": {"liuyao": True}}


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
    assert '@filter.llm_tool(name="search_liuyao_cases")' in source
    assert '@filter.llm_tool(name="record_liuyao_feedback")' in source
    assert "@filter.on_agent_done()" not in source
    assert "QQ 群主、QQ 群管理员或 AstrBot 管理员" in source
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
    assert "六爻问卦｜纳甲排盘" in result
    assert "方式：即时天机（简捷问卦）" in result
    assert "意图：事业" in result
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
    help_result = asyncio.run(plugin._dispatch_liuyao(_Event("member"), "help"))
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


def test_agent_cast_sends_ai_enriched_chart_before_context(tmp_path) -> None:
    class FakeRenderer:
        def __init__(self):
            self.kwargs = None

        def render(self, cast, **kwargs):
            assert cast.primary_number in range(1, 65)
            self.kwargs = kwargs
            output = tmp_path / "agent-chart.png"
            output.write_bytes(b"fake-png")
            return output

    class FakeContext:
        def __init__(self):
            self.prompt = ""
            self.child_prompt = ""

        async def get_current_chat_provider_id(self, *, umo):
            assert umo == "aiocqhttp:GroupMessage:10001"
            return "provider-1"

        async def llm_generate(self, *, chat_provider_id, prompt):
            assert chat_provider_id == "provider-1"
            if "临时六爻解读助手" in prompt:
                self.child_prompt = prompt
                return types.SimpleNamespace(
                    completion_text=(
                        '{"analysis":"结论：此事能成。\\n断语：三个月内先难后成。",'
                        '"summary":"三个月内先难后成。"}'
                    )
                )
            self.prompt = prompt
            return types.SimpleNamespace(
                completion_text=(
                    '{"intent":"事业","comment":"先核实机会与成本，再择稳妥时点推进。"}'
                )
            )

    plugin = _make_enabled_plugin()
    renderer = FakeRenderer()
    context = FakeContext()
    plugin.renderer = renderer
    plugin.context = context
    event = _Event("member")

    result = asyncio.run(
        plugin.cast_liuyao_tool(
            event,
            mode="instant",
            intent="",
            question="今年适合换工作吗",
            agent_name="可可子",
        )
    )

    assert len(event.sent) == 2
    assert "断语：三个月内先难后成。" in event.sent[1]["text"]
    assert event.sent[0]["image"].endswith("agent-chart.png")
    assert not (tmp_path / "agent-chart.png").exists()
    assert renderer.kwargs["caster_name"] == "测试群友"
    assert renderer.kwargs["caster_id"] == "20002"
    assert renderer.kwargs["group_id"] == "10001"
    assert renderer.kwargs["intent_label"] == "事业（AI补全）"
    assert renderer.kwargs["question"] == "今年适合换工作吗"
    assert renderer.kwargs["agent_name"] == "可可子"
    assert renderer.kwargs["ai_comment"] == "先核实机会与成本，再择稳妥时点推进。"
    assert "当前Agent自称：可可子" in context.prompt
    assert "完整正文已直接发送" in result
    assert "卦例：LY-" in result
    assert "本卦六亲（初爻→上爻）" in context.child_prompt
    assert "不得用“既可能" in context.child_prompt
    assert "断语：" not in result
    assert "分类研判参考" not in result
    saved_case = plugin._test_store["liuyao_case_library"]["cases"][0]
    case_id = saved_case["id"]
    assert saved_case["analysis"] == event.sent[1]["text"]
    assert saved_case["verdict"] == "三个月内先难后成。"
    assert saved_case["status"] == "analyzed"

    feedback_result = asyncio.run(
        plugin.record_liuyao_feedback_tool(
            event,
            feedback="两个月后拿到了新 offer，薪资也达到预期。",
            case_id=case_id,
            outcome="应验",
        )
    )
    assert feedback_result == f"已将反馈写入卦例 {case_id}（应验）。"
    saved_case = plugin._test_store["liuyao_case_library"]["cases"][0]
    assert saved_case["feedback"][0]["outcome"] == "应验"
    assert "新 offer" in saved_case["feedback"][0]["text"]

    search_result = asyncio.run(
        plugin.search_liuyao_cases_tool(
            event,
            query="换工作 offer",
            intent="事业",
            limit=3,
        )
    )
    assert f"卦例 {case_id}" in search_result
    assert "卦爻据：" in search_result
    assert "三个月内先难后成" in search_result
    assert "应验—两个月后拿到了新 offer" in search_result


def test_plain_command_sends_local_chart_without_duplicate_text(tmp_path) -> None:
    class FakeRenderer:
        def __init__(self):
            self.kwargs = None

        def render(self, cast, **kwargs):
            assert cast.primary_number in range(1, 65)
            self.kwargs = kwargs
            output = tmp_path / "command-chart.png"
            output.write_bytes(b"fake-png")
            return output

    plugin = _make_enabled_plugin()
    renderer = FakeRenderer()
    plugin.renderer = renderer
    event = _Event("member")

    result = asyncio.run(plugin._content_reply(event, "事业 今年是否适合换工作"))

    assert len(event.sent) == 1
    assert event.sent[0]["image"].endswith("command-chart.png")
    assert not (tmp_path / "command-chart.png").exists()
    assert renderer.kwargs["comment_title"] == "排盘提示"
    assert renderer.kwargs["agent_name"] == "本地排盘"
    assert renderer.kwargs["intent_label"] == "事业"
    assert result == ""

    second_event = _Event("member")

    async def collect_root_results():
        return [
            item
            async for item in plugin.liuyao_command(
                second_event,
                "事业 这个项目如何推进",
            )
        ]

    assert asyncio.run(collect_root_results()) == []
    assert len(second_event.sent) == 1


def test_agent_chart_timeout_has_explicit_fallback_status() -> None:
    class TimeoutContext:
        async def get_current_chat_provider_id(self, *, umo):
            assert umo == "aiocqhttp:GroupMessage:10001"
            return "provider-1"

        async def llm_generate(self, *, chat_provider_id, prompt):
            del chat_provider_id, prompt
            raise asyncio.TimeoutError

    plugin = _make_enabled_plugin()
    plugin.config["agent_comment_timeout_seconds"] = 5
    plugin.context = TimeoutContext()
    cast = CastResult((7, 8, 9, 6, 7, 8))

    intent, agent_name, comment, status = asyncio.run(
        plugin._enrich_agent_chart(
            _Event("member"),
            cast,
            intent="career",
            question="今年适合换工作吗",
            agent_name="可可子",
        )
    )

    assert intent == "career"
    assert agent_name == "可可子"
    assert comment
    assert status == "AI补全超时（5 秒），已使用本地保守提示（事业）"


def test_agent_cast_falls_back_to_text_when_renderer_is_unavailable() -> None:
    plugin = _make_enabled_plugin()
    plugin.renderer = None
    event = _Event("member")

    result = asyncio.run(
        plugin.cast_liuyao_tool(
            event,
            mode="instant",
            intent="general",
            question="本周这个项目能否签约",
        )
    )

    assert len(event.sent) == 2
    assert "本卦：" in event.sent[0]["text"]
    assert "断语：" in event.sent[1]["text"]
    assert "完整正文已直接发送" in result
    assert "本卦：" not in result
    assert "断语：" not in result
