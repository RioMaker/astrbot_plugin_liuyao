from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_ai_daily_integration import used_count
from test_token_usage import response

from token_usage import TOKEN_USAGE_KEY

PRIMARY_KEY = "command_interpretation_provider_id"
BACKUP_KEY = "command_interpretation_backup_provider_id"
REVIEW = '{"reject":false,"valid_question":true,"intent":"事业"}'


def failed_response(kind):
    if kind == "exception":
        return RuntimeError("primary unavailable")
    if kind == "timeout":
        return TimeoutError("primary timed out")
    reply = response(
        {"empty": " \n", "formatted_empty": "---", "stopped": "Output stopped."}.get(
            kind, "provider error"
        )
    )
    if kind == "error_role":
        reply.role = "err"
    return reply


def called_providers(plugin):
    return [call.kwargs["chat_provider_id"] for call in plugin.context.llm_generate.await_args_list]


@pytest.mark.parametrize("entrypoint", ["command", "alias", "tool", "reuse"])
@pytest.mark.parametrize(
    "failure", ["exception", "timeout", "error_role", "empty", "formatted_empty", "stopped"]
)
def test_failover_preserves_chart_prompt_quota_and_actual_token_records(entrypoint, failure):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update({PRIMARY_KEY: "primary-model", BACKUP_KEY: " backup-model "})
        plugin._prepare_cast = AsyncMock(wraps=plugin._prepare_cast)
        if entrypoint == "reuse":
            await plugin._prepare_cast(
                event, None, question=QUESTION, intent="career", method="测试"
            )
        plugin.context.llm_generate.side_effect = [
            response(REVIEW, 40, 10),
            failed_response(failure),
            response(),
        ]
        config = dict(plugin.config)
        if entrypoint == "command":
            assert [reply async for reply in plugin.liuyao_command(event, QUESTION)] == []
        elif entrypoint == "alias":
            assert [
                reply async for reply in plugin.divination_command(event, f"六爻 {QUESTION}")
            ] == []
        elif entrypoint == "tool":
            await plugin.cast_liuyao_tool(event, question=QUESTION)
        else:
            await plugin.reuse_liuyao_tool(event, question="下一步怎样落实")

        assert called_providers(plugin) == ["plugin-model", "primary-model", "backup-model"]
        calls = plugin.context.llm_generate.await_args_list
        assert calls[1].kwargs["prompt"] == calls[2].kwargs["prompt"]
        assert used_count(plugin) == 1
        assert plugin.config == config
        plugin._prepare_cast.assert_awaited_once()
        plugin._render_and_send_chart.assert_awaited_once()
        assert plugin._send_reply_payload.call_args.args[3] == ANALYSIS
        assert len(plugin._test_store["liuyao_case_library"]["cases"]) == 1
        records = (await plugin._token_runs())[0]["calls"]
        stage = "reuse_interpretation" if entrypoint == "reuse" else "interpretation"
        assert [call["stage"] for call in records] == [
            "reuse_review" if entrypoint == "reuse" else "pre_review",
            stage,
            stage,
        ]
        assert [call["provider"] for call in records] == called_providers(plugin)
        assert records[0]["usage"]["total_tokens"] == 50
        assert records[2]["usage"]["total_tokens"] == 120
        assert records[1]["usage"] == (
            None if failure in {"exception", "timeout"} else records[2]["usage"]
        )

    asyncio.run(run())


@pytest.mark.parametrize("backup", [None, "", " \t", "backup-model", " plugin-model "])
def test_healthy_primary_never_calls_backup_or_changes_selection(backup):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config[BACKUP_KEY] = backup
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert called_providers(plugin) == ["plugin-model", "plugin-model"]
        assert used_count(plugin) == 1
        assert plugin.config[BACKUP_KEY] == backup

    asyncio.run(run())


@pytest.mark.parametrize("explicit_primary", [False, True])
@pytest.mark.parametrize("backup", [None, "", " \t", " plugin-model "])
def test_missing_or_identical_backup_does_not_retry_primary(explicit_primary, backup):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config[BACKUP_KEY] = backup
        if explicit_primary:
            plugin.config[PRIMARY_KEY] = "plugin-model"
        plugin.context.llm_generate.side_effect = [response(REVIEW), RuntimeError("unavailable")]
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert called_providers(plugin) == ["plugin-model", "plugin-model"]
        assert used_count(plugin) == 0
        assert "不消耗 AI 解卦次数" in plugin._send_reply_payload.call_args.args[3]
        plugin._render_and_send_chart.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("gift", [False, True])
@pytest.mark.parametrize("failure", ["exception", "timeout", "error_role", "empty", "stopped"])
def test_failed_backup_stops_and_releases_daily_or_gift_reservation(gift, failure):
    async def run():
        plugin, event = prepare(limit=1)
        if gift:
            await plugin.cast_liuyao_tool(event, question=QUESTION)
            await plugin._admin_grant(
                "20002", {"amount": 1, "request_id": "failover-gift-request"}, "admin"
            )
            event.message_obj.message_id = "gift-failover"
        plugin.config.update({PRIMARY_KEY: "primary-model", BACKUP_KEY: "backup-model"})
        plugin.context.llm_generate.reset_mock()
        plugin.context.llm_generate.side_effect = [
            response(REVIEW),
            RuntimeError("primary unavailable"),
            failed_response(failure),
        ]
        before = copy.deepcopy(plugin._test_store.get("liuyao_case_library"))
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert called_providers(plugin) == ["plugin-model", "primary-model", "backup-model"]
        assert used_count(plugin) == int(gift)
        assert await plugin._gift_remaining(event) == int(gift)
        assert plugin._test_store.get("liuyao_case_library") == before
        assert "不消耗 AI 解卦次数" in plugin._send_reply_payload.call_args.args[3]
        original = copy.deepcopy(plugin._test_store["cast_policy:20002"])

        event.message_obj.message_id = "successful-retry"
        plugin.context.llm_generate.side_effect = [response(REVIEW), response()]
        await plugin.reuse_liuyao_tool(event)
        assert called_providers(plugin)[-2:] == ["plugin-model", "primary-model"]
        assert used_count(plugin) == 1
        assert await plugin._gift_remaining(event) == 0
        assert plugin._test_store["cast_policy:20002"] == original

    asyncio.run(run())


@pytest.mark.parametrize(
    "review",
    [
        RuntimeError("review unavailable"),
        SimpleNamespace(completion_text="invalid JSON"),
        SimpleNamespace(completion_text='{"reject":true,"valid_question":true}'),
        SimpleNamespace(completion_text='{"reject":false,"valid_question":false}'),
    ],
)
def test_failover_never_bypasses_review_failure_or_rejection(review):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config[BACKUP_KEY] = "backup-model"
        plugin.context.llm_generate.side_effect = [review]
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert called_providers(plugin) == ["plugin-model"]
        assert used_count(plugin) == 0
        assert "liuyao_case_library" not in plugin._test_store

    asyncio.run(run())


def test_backup_can_handle_missing_session_primary_after_successful_review():
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update(command_pre_review_provider_id="review-model")
        plugin.config[BACKUP_KEY] = "backup-model"
        plugin.context.get_current_chat_provider_id.return_value = ""
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert called_providers(plugin) == ["review-model", "backup-model"]
        assert used_count(plugin) == 1
        assert plugin._send_reply_payload.call_args.args[3] == ANALYSIS

    asyncio.run(run())


def test_real_primary_timeout_cancels_it_before_starting_backup():
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update({PRIMARY_KEY: "primary-model", BACKUP_KEY: "backup-model"})
        cancelled = asyncio.Event()

        async def generate(**kwargs):
            if kwargs["chat_provider_id"] == "plugin-model":
                return response(REVIEW)
            if kwargs["chat_provider_id"] == "primary-model":
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()
            assert cancelled.is_set()
            return response()

        plugin.context.llm_generate.side_effect = generate
        bounded = plugin._bounded_config_int
        plugin._bounded_config_int = lambda key, *args: (
            0.02 if key == "command_analysis_timeout_seconds" else bounded(key, *args)
        )
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert called_providers(plugin) == ["plugin-model", "primary-model", "backup-model"]
        records = (await plugin._token_runs())[0]["calls"]
        assert [call["status"] for call in records] == ["returned", "cancelled", "returned"]
        assert records[1]["usage"] is None
        assert used_count(plugin) == 1

    asyncio.run(run())


@pytest.mark.parametrize("blocked_provider", ["primary-model", "backup-model"])
def test_cancellation_during_primary_or_backup_stops_further_calls(blocked_provider):
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update({PRIMARY_KEY: "primary-model", BACKUP_KEY: "backup-model"})
        entered = asyncio.Event()

        async def generate(**kwargs):
            provider = kwargs["chat_provider_id"]
            if provider == "plugin-model":
                return response(REVIEW)
            if provider == blocked_provider:
                entered.set()
                await asyncio.Future()
            raise RuntimeError("primary unavailable")

        plugin.context.llm_generate.side_effect = generate
        task = asyncio.create_task(plugin.cast_liuyao_tool(event, question=QUESTION))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert called_providers(plugin) == ["plugin-model", "primary-model"] + (
            ["backup-model"] if blocked_provider == "backup-model" else []
        )
        assert used_count(plugin) == 0
        assert "今日已用：0 次，剩余：1 次" in await plugin._ai_quota_status(event)
        assert "liuyao_case_library" not in plugin._test_store

    asyncio.run(run())


def test_failover_cannot_start_an_unrecorded_backup_call():
    async def run():
        plugin, event = prepare(limit=1)
        plugin.config.update({PRIMARY_KEY: "primary-model", BACKUP_KEY: "backup-model"})
        save = plugin.put_kv_data

        async def persist(key, value):
            if key == TOKEN_USAGE_KEY and plugin.context.llm_generate.await_count >= 2:
                raise OSError("ledger unavailable")
            await save(key, value)

        plugin.put_kv_data = persist
        plugin.context.llm_generate.side_effect = [response(REVIEW), RuntimeError("unavailable")]
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert called_providers(plugin) == ["plugin-model", "primary-model"]
        assert used_count(plugin) == 0
        assert "liuyao_case_library" not in plugin._test_store

    asyncio.run(run())
