from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_ai_daily_integration import used_count

from model_settings import MODEL_OVERRIDES

REVIEW_KEY = "command_pre_review_provider_id"
INTERPRETATION_KEY = "command_interpretation_provider_id"
REVIEW = '{"reject":false,"intent":"事业"}'


def test_model_selectors_default_to_system_provider():
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "_conf_schema.json").read_text(encoding="utf-8")
    )
    for key in (*MODEL_OVERRIDES, *MODEL_OVERRIDES.values()):
        assert schema[key]["type"] == "string"
        assert schema[key]["default"] == ""
        assert schema[key]["_special"] == "select_provider"


@pytest.mark.parametrize("entrypoint", ["command", "alias", "tool"])
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, ["plugin-model", "plugin-model"]),
        ({REVIEW_KEY: "", INTERPRETATION_KEY: ""}, ["plugin-model", "plugin-model"]),
        ({REVIEW_KEY: " \t", INTERPRETATION_KEY: None}, ["plugin-model", "plugin-model"]),
        ({REVIEW_KEY: "review-model"}, ["review-model", "plugin-model"]),
        ({INTERPRETATION_KEY: "reading-model"}, ["plugin-model", "reading-model"]),
        (
            {REVIEW_KEY: " review-model ", INTERPRETATION_KEY: "reading-model"},
            ["review-model", "reading-model"],
        ),
    ],
)
def test_stage_models_apply_to_all_cast_entrypoints(config, expected, entrypoint):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update(config)
        if "plugin-model" not in expected:
            plugin.context.get_current_chat_provider_id.side_effect = AssertionError(
                "explicit models must work without a session provider"
            )
        if entrypoint == "command":
            assert [reply async for reply in plugin.liuyao_command(event, QUESTION)] == []
        elif entrypoint == "alias":
            assert [
                reply async for reply in plugin.divination_command(event, f"六爻 {QUESTION}")
            ] == []
        else:
            await plugin.cast_liuyao_tool(event, question=QUESTION)

        assert [
            call.kwargs["chat_provider_id"] for call in plugin.context.llm_generate.await_args_list
        ] == expected
        for call in plugin.context.get_current_chat_provider_id.await_args_list:
            assert call.kwargs == {"umo": event.unified_msg_origin}
        assert used_count(plugin) == 1
        plugin._render_and_send_chart.assert_awaited_once()
        assert plugin._send_reply_payload.call_args.args[3] == ANALYSIS
        runs = await plugin._token_runs()
        assert len(runs) == 1
        assert [(call["stage"], call["provider"]) for call in runs[0]["calls"]] == list(
            zip(["pre_review", "interpretation"], expected, strict=True)
        )

    asyncio.run(run())


def test_reuse_applies_model_changes_and_clearing_restores_current_session():
    async def run():
        plugin, event = prepare(limit=3)
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        plugin.config.update({REVIEW_KEY: "review-model", INTERPRETATION_KEY: "reading-model"})
        for message_id in ("configured-reuse", "default-reuse"):
            plugin.context.llm_generate.side_effect = [
                SimpleNamespace(completion_text=REVIEW),
                SimpleNamespace(completion_text=ANALYSIS),
            ]
            event.message_obj.message_id = message_id
            await plugin.reuse_liuyao_tool(event, question="客户已同意，下一步呢")
            plugin.config.update({REVIEW_KEY: "", INTERPRETATION_KEY: ""})
            plugin.context.get_current_chat_provider_id.return_value = "new-session-model"

        assert [
            call.kwargs["chat_provider_id"] for call in plugin.context.llm_generate.await_args_list
        ] == [
            "plugin-model",
            "plugin-model",
            "review-model",
            "reading-model",
            "new-session-model",
            "new-session-model",
        ]
        assert used_count(plugin) == 3
        runs = await plugin._token_runs()
        reuse_calls = [call for run in runs if run["kind"] == "reuse" for call in run["calls"]]
        assert {call["stage"] for call in reuse_calls} == {
            "reuse_review",
            "reuse_interpretation",
        }
        assert [call["provider"] for call in reuse_calls] == [
            "review-model",
            "reading-model",
            "new-session-model",
            "new-session-model",
        ]

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["pre_review", "interpretation"])
@pytest.mark.parametrize("failure", [ValueError, TimeoutError])
def test_selected_provider_failure_keeps_chart_without_switching_models(stage, failure):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update({REVIEW_KEY: "review-model", INTERPRETATION_KEY: "reading-model"})
        replies = [] if stage == "pre_review" else [SimpleNamespace(completion_text=REVIEW)]
        plugin.context.llm_generate.side_effect = [*replies, failure("model unavailable")]
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert plugin.context.llm_generate.await_count == (1 if stage == "pre_review" else 2)
        plugin.context.get_current_chat_provider_id.assert_not_awaited()
        plugin._render_and_send_chart.assert_awaited_once()
        assert "失败" in plugin._send_reply_payload.call_args.args[3] or (
            "预审暂不可用" in plugin._send_reply_payload.call_args.args[3]
        )
        assert "liuyao_case_library" not in plugin._test_store
        assert used_count(plugin) == 1

    asyncio.run(run())


@pytest.mark.parametrize("review_model", ["", "review-model"])
def test_missing_system_model_does_not_borrow_the_other_stage_model(review_model):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update(
            {REVIEW_KEY: review_model, INTERPRETATION_KEY: "" if review_model else "reading-model"}
        )
        plugin.context.get_current_chat_provider_id.return_value = ""
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert plugin.context.llm_generate.await_count == (1 if review_model else 0)
        assert used_count(plugin) == (1 if review_model else 0)
        plugin._render_and_send_chart.assert_awaited_once()
        assert "liuyao_case_library" not in plugin._test_store

    asyncio.run(run())


def test_review_rejection_never_resolves_or_calls_default_interpretation_model():
    async def run():
        plugin, event = prepare(limit=1, replies=['{"reject":true,"intent":"综合"}'])
        plugin.config[REVIEW_KEY] = "review-model"
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        plugin.context.get_current_chat_provider_id.assert_not_awaited()
        plugin.context.llm_generate.assert_awaited_once()
        assert plugin.context.llm_generate.call_args.kwargs["chat_provider_id"] == "review-model"
        assert "不能提供" in plugin._send_reply_payload.call_args.args[3]
        plugin._render_and_send_chart.assert_awaited_once()

    asyncio.run(run())


def test_model_overrides_cannot_bypass_daily_quota():
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update({REVIEW_KEY: "review-model", INTERPRETATION_KEY: "reading-model"})
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        plugin.context.llm_generate.reset_mock()
        event.message_obj.message_id = "quota-exhausted"
        await plugin.cast_liuyao_tool(event, question="明天能签约吗")
        plugin.context.llm_generate.assert_not_awaited()
        plugin.context.get_current_chat_provider_id.assert_not_awaited()
        assert used_count(plugin) == 1
        assert "额度已用完" in plugin._send_caster_reply.call_args.args[3]

    asyncio.run(run())
