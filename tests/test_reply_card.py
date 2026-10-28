"""Prompt visibility, reply length budget and the 解卦玉笺 text card."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

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
    error = staticmethod(lambda *args, **kwargs: None)
    warning = staticmethod(lambda *args, **kwargs: None)
    exception = staticmethod(lambda *args, **kwargs: None)


class _At:
    def __init__(self, qq=None):
        self.qq = qq


class _Plain:
    def __init__(self, text=""):
        self.text = text


class _Image:
    def __init__(self, path=""):
        self.path = path

    @classmethod
    def fromFileSystem(cls, path):
        return cls(path)


astrbot = types.ModuleType("astrbot")
api = types.ModuleType("astrbot.api")
event_api = types.ModuleType("astrbot.api.event")
star_api = types.ModuleType("astrbot.api.star")
core = types.ModuleType("astrbot.core")
core_star = types.ModuleType("astrbot.core.star")
core_filter = types.ModuleType("astrbot.core.star.filter")
command_api = types.ModuleType("astrbot.core.star.filter.command")
components = types.ModuleType("astrbot.api.message_components")
components.At = _At
components.Plain = _Plain
components.Image = _Image
api.logger = _Logger()
api.AstrBotConfig = dict
api.message_components = components
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
sys.modules.setdefault("astrbot.api.message_components", components)
sys.modules.setdefault("astrbot.core", core)
sys.modules.setdefault("astrbot.core.star", core_star)
sys.modules.setdefault("astrbot.core.star.filter", core_filter)
sys.modules.setdefault("astrbot.core.star.filter.command", command_api)

import prompts  # noqa: E402
import reply_policy  # noqa: E402
from corpus import ZhouyiCorpus  # noqa: E402
from main import LiuyaoPlugin, _reply_card_chain  # noqa: E402
from reading import ReadingService  # noqa: E402
from textcard import LiuyaoTextCardRenderer  # noqa: E402

LONG_ANALYSIS = (
    "断：可以换，但不宜急。眼下先谈成再辞旧，入秋之后动身更稳。\n"
    "本卦泽火革之上六，动而化风火家人。革卦主除旧布新，与跳槽相应；"
    "上六爻辞“君子豹变，小人革面”，说明变化已经成形，关键在变后能否安定下来。\n"
    "反证在于妻财得月建生扶、日辰相克，说明新东家给的条件未必一次到位，谈薪必须落到纸面。\n"
    "建议：本月内完成两到三场面谈，把薪酬、职级与试用期写进合同。\n"
    "断语：入秋前后可成，先谈定再辞旧。"
)


class _Config(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0

    def save_config(self):
        self.saved += 1


class _Context:
    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.prompts: list[str] = []

    async def get_current_chat_provider_id(self, *, umo):
        return "provider-1"

    async def llm_generate(self, *, chat_provider_id, prompt):
        self.prompts.append(prompt)
        return types.SimpleNamespace(
            completion_text=self.replies.pop(0) if self.replies else "",
            role="assistant",
        )


class _Event:
    def __init__(self):
        self.sent = []
        self.unified_msg_origin = "aiocqhttp:GroupMessage:10001"
        self.message_obj = types.SimpleNamespace(
            raw_message={"sender": {"role": "member"}, "message_id": "m-1"}
        )

    def get_group_id(self):
        return "10001"

    def get_sender_id(self):
        return "20002"

    def get_sender_name(self):
        return "测试群友"

    def is_admin(self):
        return False

    def plain_result(self, text):
        return {"text": text}

    def image_result(self, path):
        return {"image": path}

    def chain_result(self, chain):
        return {"chain": chain}

    async def send(self, message):
        self.sent.append(message)


class _FakeCard:
    def __init__(self, path: Path | None = None, fail: bool = False):
        self.path = path
        self.fail = fail
        self.kwargs = None

    def render(self, **kwargs):
        if self.fail:
            raise RuntimeError("card renderer exploded")
        self.kwargs = kwargs
        return self.path


def _make_plugin(config: dict, context=None) -> LiuyaoPlugin:
    plugin = object.__new__(LiuyaoPlugin)
    plugin.config = _Config(config)
    plugin.context = context
    plugin.corpus = ZhouyiCorpus(ROOT / "data" / "zhouyi.json")
    plugin.readings = ReadingService(plugin.corpus, ROOT / "data" / "intents.json")
    plugin.renderer = None
    plugin.text_card = None
    store = {"method_switches": {"10001": {"liuyao": True}}}

    async def get_data(key, default=None):
        return store.get(key, default)

    async def put_data(key, value):
        store[key] = value

    plugin.get_kv_data = get_data
    plugin.put_kv_data = put_data
    plugin._case_lock = asyncio.Lock()
    plugin._cast_lock = asyncio.Lock()
    plugin._switch_lock = asyncio.Lock()
    plugin._pending_agent_cases = {}

    async def _chart_and_send(event, cast, **kwargs):
        del cast, kwargs
        return "已发送到当前会话"

    plugin._render_and_send_chart = _chart_and_send
    return plugin


async def _run_content(plugin, event, question, *, via_tool):
    if via_tool:
        return await plugin.cast_liuyao_tool(event, question=question)
    return await plugin._content_reply(event, question)


# ----------------------------------------------------------------------
# Prompt visibility, editing and restoring the default
# ----------------------------------------------------------------------
def test_prompt_fields_are_textareas_with_empty_schema_defaults() -> None:
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    for key in prompts.PROMPT_DEFAULTS:
        assert schema[key]["type"] == "text", key
        # An empty schema default keeps "empty means built-in" working, so the
        # plugin fills the real text into the config for display instead.
        assert schema[key]["default"] == "", key
        assert "恢复内置默认" in schema[key]["hint"], key
    assert schema["agent_final_reply_prompt"]["type"] == "text"
    assert schema["agent_final_reply_prompt"]["default"] == ""


def test_builtin_prompts_carry_the_placeholders_they_are_rendered_with() -> None:
    for placeholder in ("{question}", "{chart}", "{max_chars}"):
        assert placeholder in prompts.INTERPRETATION_PROMPT, placeholder
    for placeholder in ("{question}", "{hexagram}", "{verdict}", "{feedback}"):
        assert placeholder in prompts.FEEDBACK_REVIEW_PROMPT, placeholder
    assert "{question}" in prompts.PRE_REVIEW_PROMPT
    assert "{readings}" in prompts.AGENT_RECOVERY_PROMPT
    assert "{agent_name}" in prompts.CHART_COMMENT_PROMPT


def test_empty_prompt_config_is_filled_once_with_the_builtin_text() -> None:
    plugin = _make_plugin({})
    plugin._ensure_prompt_defaults()
    for key, text in prompts.PROMPT_DEFAULTS.items():
        assert plugin.config[key] == text
    assert plugin.config.saved == 1

    plugin._ensure_prompt_defaults()
    assert plugin.config.saved == 1, "已填写的配置不应反复保存"


def test_custom_prompt_is_kept_and_placeholders_are_substituted() -> None:
    plugin = _make_plugin({"command_interpretation_prompt": "自定义：{question}｜{max_chars}"})
    plugin._ensure_prompt_defaults()
    assert plugin.config["command_interpretation_prompt"] == "自定义：{question}｜{max_chars}"
    rendered = plugin._configured_prompt(
        "command_interpretation_prompt",
        prompts.INTERPRETATION_PROMPT,
        question="今年适合换工作吗",
        chart="排盘",
        max_chars="不超过 280 字",
    )
    assert rendered == "自定义：今年适合换工作吗｜不超过 280 字"


def test_constructor_writes_prompt_defaults_into_the_config() -> None:
    plugin = LiuyaoPlugin(_Context(), _Config({"ai_daily_limit": 0}))
    assert plugin.config["command_interpretation_prompt"] == prompts.INTERPRETATION_PROMPT
    assert plugin.config.saved == 1


def test_max_chars_instruction_handles_the_unlimited_setting() -> None:
    assert LiuyaoPlugin._max_chars_instruction(280) == "不超过 280 字"
    assert "不限" in LiuyaoPlugin._max_chars_instruction(0)


# ----------------------------------------------------------------------
# Length budget
# ----------------------------------------------------------------------
def test_limit_reply_keeps_the_verdict_line_within_the_budget() -> None:
    limited = reply_policy.limit_reply(LONG_ANALYSIS, 120)
    assert len(limited) <= 120
    assert limited.endswith("断语：入秋前后可成，先谈定再辞旧。")
    assert "断语：" in limited
    assert "建议：本月内" not in limited


def test_limit_reply_is_a_noop_when_short_or_disabled() -> None:
    assert reply_policy.limit_reply("短句。", 280) == "短句。"
    assert reply_policy.limit_reply("短句。", 0) == "短句。"
    assert reply_policy.limit_reply(LONG_ANALYSIS, 40, trim=False) == LONG_ANALYSIS


def test_limit_reply_without_verdict_still_respects_the_budget() -> None:
    text = "第一句。第二句。第三句。第四句。第五句。第六句。"
    limited = reply_policy.limit_reply(text, 12)
    assert len(limited) <= 12
    assert limited.endswith("。")


def test_limit_reply_never_drops_the_verdict_when_the_budget_is_tiny() -> None:
    limited = reply_policy.limit_reply(LONG_ANALYSIS, 8)
    assert limited == "断语：入秋前后可成，先谈定再辞旧。"


def test_split_verdict_uses_only_the_last_line() -> None:
    body, verdict = reply_policy.split_verdict("上文。\n断语：旧断。\n补充。\n断语：新断。")
    assert verdict == "新断。"
    assert "旧断" in body and "新断" not in body


@pytest.mark.parametrize(
    ("mode", "length", "threshold", "expected"),
    [
        ("关闭", 900, 0, False),
        ("超长时", 199, 200, False),
        ("超长时", 200, 200, True),
        ("始终", 10, 200, True),
        ("always", 10, 200, True),
        ("未知值", 250, 200, True),
    ],
)
def test_card_mode_decides_the_delivery(mode, length, threshold, expected) -> None:
    assert reply_policy.should_render_card(mode, length, threshold) is expected


# ----------------------------------------------------------------------
# Card rendering
# ----------------------------------------------------------------------
def test_card_renderer_produces_png_and_grows_with_content(tmp_path) -> None:
    renderer = LiuyaoTextCardRenderer()
    short = renderer.render(
        question="这个项目能否在月底前签约？",
        subtitle="事业 · 第9卦 小畜之第37卦 家人",
        body="断：成。",
        verdict="月底可签。",
        meta="2026-10-23 08:31 · 测试群友",
        output_path=tmp_path / "short.png",
    )
    long = renderer.render(
        question="这个项目能否在月底前签约？",
        subtitle="事业 · 第9卦 小畜之第37卦 家人",
        body=LONG_ANALYSIS,
        verdict="月底可签。",
        meta="2026-10-23 08:31 · 测试群友",
        output_path=tmp_path / "long.png",
    )
    from PIL import Image

    with Image.open(short) as short_image, Image.open(long) as long_image:
        assert short_image.size[0] == 1080
        assert long_image.size[1] > short_image.size[1]
        assert short.stat().st_size > 0 and long.stat().st_size > 0


def test_card_renderer_survives_empty_input(tmp_path) -> None:
    path = LiuyaoTextCardRenderer().render(output_path=tmp_path / "empty.png")
    assert path.is_file() and path.stat().st_size > 0


def test_reply_card_chain_mentions_caster_then_image(tmp_path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(b"png")
    chain = _reply_card_chain("20002", str(png))
    assert isinstance(chain[0], _At) and chain[0].qq == "20002"
    assert isinstance(chain[1], _Image) and chain[1].path == str(png)


# ----------------------------------------------------------------------
# Command path integration
# ----------------------------------------------------------------------
def _review_json() -> str:
    return json.dumps({"reject": False, "reason": "可以分类", "intent": "事业"})


@pytest.mark.parametrize("via_tool", [False, True])
def test_long_reply_is_sent_as_a_card(tmp_path, via_tool) -> None:
    png = tmp_path / "card.png"
    png.write_bytes(b"png")
    context = _Context([_review_json(), LONG_ANALYSIS])
    plugin = _make_plugin(
        {
            "ai_daily_limit": 0,
            "case_library_enabled": False,
            "command_reply_image_mode": "始终",
            "command_reply_max_chars": 280,
        },
        context,
    )
    card = _FakeCard(path=png)
    plugin.text_card = card
    event = _Event()

    asyncio.run(_run_content(plugin, event, "今年适合换工作吗", via_tool=via_tool))

    assert card.kwargs is not None
    assert card.kwargs["question"] == "今年适合换工作吗"
    assert card.kwargs["verdict"] == "入秋前后可成，先谈定再辞旧。"
    assert "断语：" not in card.kwargs["body"]
    chains = [item["chain"] for item in event.sent if "chain" in item]
    assert any(isinstance(part, _Image) for chain in chains for part in chain)
    assert not any(
        "君子豹变" in part.text for chain in chains for part in chain if isinstance(part, _Plain)
    )


@pytest.mark.parametrize("via_tool", [False, True])
def test_interpretation_prompt_carries_the_char_budget(tmp_path, via_tool) -> None:
    png = tmp_path / "card.png"
    png.write_bytes(b"png")
    context = _Context([_review_json(), LONG_ANALYSIS])
    plugin = _make_plugin(
        {
            "ai_daily_limit": 0,
            "case_library_enabled": False,
            "command_reply_image_mode": "关闭",
            "command_reply_max_chars": 180,
        },
        context,
    )
    asyncio.run(_run_content(plugin, _Event(), "今年适合换工作吗", via_tool=via_tool))

    interpretation = context.prompts[-1]
    assert "不超过 180 字" in interpretation
    assert "断语：" in interpretation


@pytest.mark.parametrize("via_tool", [False, True])
def test_card_failure_falls_back_to_plain_text(tmp_path, via_tool) -> None:
    context = _Context([_review_json(), LONG_ANALYSIS])
    plugin = _make_plugin(
        {
            "ai_daily_limit": 0,
            "case_library_enabled": False,
            "command_reply_image_mode": "始终",
            "command_reply_max_chars": 280,
        },
        context,
    )
    plugin.text_card = _FakeCard(fail=True)
    event = _Event()

    asyncio.run(_run_content(plugin, event, "今年适合换工作吗", via_tool=via_tool))

    chains = [item["chain"] for item in event.sent if "chain" in item]
    assert chains, "回退时应发送纯文本"
    assert not any(isinstance(part, _Image) for chain in chains for part in chain)
    texts = [part.text for chain in chains for part in chain if isinstance(part, _Plain)]
    assert any("君子豹变" in text for text in texts)


@pytest.mark.parametrize("via_tool", [False, True])
def test_short_reply_stays_plain_text_when_mode_is_long_only(via_tool) -> None:
    short = "断：成。\n本卦风天小畜二爻动，宜借熟人引荐。\n断语：本周内可定。"
    context = _Context([_review_json(), short])
    plugin = _make_plugin(
        {
            "ai_daily_limit": 0,
            "case_library_enabled": False,
            "command_reply_image_mode": "超长时",
            "command_reply_image_threshold": 200,
        },
        context,
    )
    plugin.text_card = _FakeCard()
    event = _Event()

    asyncio.run(_run_content(plugin, event, "这个项目能否在月底前签约", via_tool=via_tool))

    chains = [item["chain"] for item in event.sent if "chain" in item]
    assert not any(isinstance(part, _Image) for chain in chains for part in chain)
    texts = [part.text for chain in chains for part in chain if isinstance(part, _Plain)]
    assert any("断语：本周内可定。" in text for text in texts)


def test_stale_local_view_hint_is_gone() -> None:
    for name in ("cast_policy.py", "agent_analysis.py"):
        source = (ROOT / name).read_text(encoding="utf-8")
        assert "/六爻 解读" not in source, name
