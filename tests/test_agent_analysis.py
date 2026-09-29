from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest
from test_permissions import _Event, _make_enabled_plugin

ANALYSIS = "结论：宜先落实条件。\n依据原卦动爻辨主证与反证。\n断语：条件具备后推进。"
SENT_KEY = "_send_message_to_user_current_session_plain_texts"


class ModelContext:
    def __init__(self, response=None, error=None):
        self.response = response or SimpleNamespace(completion_text=ANALYSIS)
        self.error = error
        self.calls = []

    async def get_current_chat_provider_id(self, *, umo):
        assert umo == "aiocqhttp:GroupMessage:10001"
        return "current-provider"

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


async def prepare(*, library=True):
    plugin = _make_enabled_plugin()
    plugin.config = {
        "agent_send_chart_image": False,
        "agent_generate_chart_comment": False,
        "case_library_enabled": library,
    }
    plugin.context = ModelContext()
    event = _Event("member")
    event.extras = {}
    event.get_extra = lambda key, default=None: event.extras.get(key, default)
    result = await plugin.cast_liuyao_tool(
        event, mode="manual", manual_lines="7 8 9 6 7 8", question="这个项目如何推进"
    )
    return plugin, event, result


def test_empty_agent_reply_recovers_original_cast_and_saves_once():
    async def run():
        plugin, event, original = await prepare()
        policy = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        response = SimpleNamespace(completion_text="", reasoning_content="仅有思考")
        await plugin.capture_liuyao_agent_analysis(event, object(), response)
        await plugin.capture_liuyao_agent_analysis(event, object(), response)
        assert event.sent == [{"text": ANALYSIS}]
        assert len(plugin.context.calls) == 1
        request = plugin.context.calls[0]
        assert request["chat_provider_id"] == "current-provider"
        assert original in request["prompt"]
        assert "tools" not in request
        assert response.completion_text == ""  # Framework must not send it twice.
        assert plugin._test_store["cast_policy:20002"] == policy
        cases = plugin._test_store["liuyao_case_library"]["cases"]
        assert len(cases) == 1
        assert cases[0]["analysis"] == ANALYSIS
        assert cases[0]["status"] == "analyzed"
        assert not plugin._pending_agent_cases
        assert event._liuyao_agent_reading_state is None

    asyncio.run(run())


def test_normal_reply_is_saved_without_extra_request_or_delivery():
    async def run():
        plugin, event, _ = await prepare()
        await plugin.capture_liuyao_agent_analysis(
            event, object(), SimpleNamespace(completion_text=ANALYSIS)
        )
        assert not plugin.context.calls
        assert not event.sent
        assert plugin._test_store["liuyao_case_library"]["cases"][0]["analysis"] == ANALYSIS

    asyncio.run(run())


def test_directly_delivered_analysis_is_saved_without_duplicate():
    async def run():
        plugin, event, _ = await prepare()
        event.extras[SENT_KEY] = [ANALYSIS]
        await plugin.capture_liuyao_agent_analysis(
            event, object(), SimpleNamespace(completion_text="")
        )
        assert not plugin.context.calls
        assert not event.sent
        assert plugin._test_store["liuyao_case_library"]["cases"][0]["analysis"] == ANALYSIS

    asyncio.run(run())


def test_pre_cast_text_does_not_suppress_recovery():
    async def run():
        plugin, event, _ = await prepare()
        event.extras[SENT_KEY] = ["我来起卦"]
        event._liuyao_agent_reading_state["sent_offset"] = 1
        await plugin.capture_liuyao_agent_analysis(event, object(), None)
        assert event.sent == [{"text": ANALYSIS}]

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["empty", "provider", "timeout", "error_role", "tool"])
def test_failed_recovery_sends_notice_and_does_not_save_fake_analysis(failure):
    async def run():
        plugin, event, _ = await prepare()
        if failure == "empty":
            plugin.context.response = SimpleNamespace(completion_text="  ")
        elif failure == "provider":
            plugin.context.error = RuntimeError("provider unavailable")
        elif failure == "timeout":
            plugin.context.error = TimeoutError()
        elif failure == "error_role":
            plugin.context.response = SimpleNamespace(completion_text="error", role="err")
        else:
            plugin.context.response = SimpleNamespace(
                completion_text="调用工具", tools_call_name=["cast_liuyao"]
            )
        policy = copy.deepcopy(plugin._test_store["cast_policy:20002"])
        await plugin.capture_liuyao_agent_analysis(event, object(), None)
        assert len(plugin.context.calls) == 1
        assert len(event.sent) == 1
        assert "reuse_liuyao" in event.sent[0]["text"]
        case = plugin._test_store["liuyao_case_library"]["cases"][0]
        assert case["analysis"] == ""
        assert case["status"] == "awaiting_analysis"
        assert plugin._test_store["cast_policy:20002"] == policy

    asyncio.run(run())


def test_recovery_timeout_covers_provider_lookup(monkeypatch):
    async def run():
        plugin, event, _ = await prepare()
        cancelled = asyncio.Event()

        async def stalled_provider(**kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        plugin.context.get_current_chat_provider_id = stalled_provider
        monkeypatch.setattr(plugin, "_bounded_config_int", lambda *args: 0.01)
        await plugin.capture_liuyao_agent_analysis(event, object(), None)
        assert cancelled.is_set()
        assert not plugin.context.calls
        assert "AI 解读未能返回" in event.sent[0]["text"]

    asyncio.run(run())


def test_recovery_works_when_case_library_disabled():
    async def run():
        plugin, event, _ = await prepare(library=False)
        assert not plugin._pending_agent_cases
        await plugin.capture_liuyao_agent_analysis(event, object(), None)
        assert event.sent == [{"text": ANALYSIS}]
        assert "liuyao_case_library" not in plugin._test_store

    asyncio.run(run())


@pytest.mark.parametrize(
    "response",
    [SimpleNamespace(completion_text="Output stopped."), SimpleNamespace(role="err")],
)
def test_explicit_stop_or_error_is_not_archived_as_analysis(response):
    async def run():
        plugin, event, _ = await prepare()
        await plugin.capture_liuyao_agent_analysis(event, object(), response)
        assert not plugin.context.calls
        assert not event.sent
        assert plugin._test_store["liuyao_case_library"]["cases"][0]["analysis"] == ""

    asyncio.run(run())


def test_unrelated_agent_run_does_not_trigger_recovery():
    plugin = _make_enabled_plugin()
    plugin.context = ModelContext()
    event = _Event("member")
    asyncio.run(plugin.capture_liuyao_agent_analysis(event, object(), None))
    assert not plugin.context.calls
    assert not event.sent
