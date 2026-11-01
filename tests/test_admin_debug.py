from __future__ import annotations

import asyncio
import copy
import json
import re
from types import SimpleNamespace

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_ai_daily_integration import used_count
from test_token_usage import response

from debug_trace import DebugTrace
from model_settings import ADMIN_MODEL_KEYS
from token_usage import TOKEN_USAGE_KEY
from user_management import ManagementError

REVIEW = '{"reject":false,"intent":"事业"}'


class Config(dict):
    def save_config(self):
        self.persisted = dict(self)


def admin_plugin():
    plugin, event = prepare(limit=1)
    event._astrbot_admin = True
    plugin.config = Config(plugin.config)
    plugin.config.update(zip(ADMIN_MODEL_KEYS, ["admin-review", "admin-reading"], strict=True))
    provider = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="admin-review", model="model-a"),
        get_model=lambda: "model-a",
        provider_config={"api_key": "SECRET_PROVIDER_KEY"},
    )
    other = SimpleNamespace(meta=lambda: SimpleNamespace(id="admin-reading", model="model-b"))
    plugin.context.get_all_providers = lambda: [provider, other]
    plugin.context.get_provider_by_id = lambda _id: provider
    plugin.context.llm_generate.side_effect = [response(REVIEW, 40, 10), response()]
    return plugin, event


async def command(plugin, event, question=QUESTION):
    return [item async for item in plugin.liuyao_test_command(event, question)]


def trace_steps(event):
    parts = {}
    for message in event.sent:
        header, _, body = message.get("text", "").partition("\n")
        match = re.match(r"【六爻调试 [a-f0-9]+ · \d+ · (.+?)】", header)
        if match:
            parts.setdefault(match[1], []).append(body)
    result = {}
    for title, chunks in parts.items():
        try:
            result[title] = json.loads("".join(chunks))
        except ValueError:
            result[title] = "".join(chunks)
    return result


@pytest.mark.parametrize("role", ["member", "admin", "owner"])
def test_group_roles_cannot_use_debug_or_administrator_models(role):
    async def run():
        plugin, event = admin_plugin()
        event._astrbot_admin = False
        event.message_obj.raw_message["sender"]["role"] = role
        result = await command(plugin, event)
        assert "仅限 AstrBot 管理员" in result[0]["text"]
        assert not event.sent
        plugin.context.llm_generate.assert_not_awaited()
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert all(
            call.kwargs["chat_provider_id"] == "plugin-model"
            for call in plugin.context.llm_generate.await_args_list
        )
        assert not event.sent

    asyncio.run(run())


@pytest.mark.parametrize(
    "admin_values,common_values,expected",
    [
        (
            ("admin-review", "admin-reading"),
            ("common-review", "common-reading"),
            ["admin-review", "admin-reading"],
        ),
        (("", "admin-reading"), ("common-review", ""), ["common-review", "admin-reading"]),
        (("", ""), ("", "common-reading"), ["plugin-model", "common-reading"]),
    ],
)
def test_administrator_overrides_and_independent_fallback_for_normal_cast(
    admin_values, common_values, expected
):
    async def run():
        plugin, event = admin_plugin()
        plugin.config.update(zip(ADMIN_MODEL_KEYS, admin_values, strict=True))
        plugin.config.update(
            command_pre_review_provider_id=common_values[0],
            command_interpretation_provider_id=common_values[1],
        )
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert [
            c.kwargs["chat_provider_id"] for c in plugin.context.llm_generate.await_args_list
        ] == expected
        assert used_count(plugin) == 1
        assert not event.sent

    asyncio.run(run())


def test_debug_emits_actual_requests_results_usage_and_preserves_production_data():
    async def run():
        plugin, event = admin_plugin()
        await plugin.cast_liuyao_tool(event, question="普通起卦")
        previous = copy.deepcopy(plugin._test_store)
        plugin.context.llm_generate.reset_mock()
        plugin.context.llm_generate.side_effect = [response(REVIEW, 40, 10), response()]
        plugin.config["command_interpretation_prompt"] = (
            "完整测试提示\n" * 600 + "{chart}\n结尾标记"
        )
        event.message_obj.message_id = "debug-after-quota-exhausted"
        assert await command(plugin, event) == []
        assert plugin.context.llm_generate.await_count == 2
        steps = trace_steps(event)
        for label, call in zip(
            ["预审", "解卦"], plugin.context.llm_generate.await_args_list, strict=True
        ):
            assert steps[f"{label}请求"]["request"] == call.kwargs
            assert steps[f"{label}请求"]["模型来源"] == "管理员专用配置"
            record = steps[f"{label}响应"]["调用记录"][0]
            assert record["elapsed_ms"] >= 0 and record["status"] == "returned"
        assert steps["预审响应"]["completion_text"] == REVIEW
        assert steps["解卦响应"]["completion_text"] == ANALYSIS
        assert steps["预审解析与分类"]["最终分类"] == "career"
        assert len(steps["排盘结果"]["六爻（自下而上）"]) == 6
        assert steps["调试汇总"]["已知总 Token"] == 170
        assert steps["调试汇总"]["总量未知调用次数"] == 0
        assert all(len(item["text"]) < 1700 for item in event.sent)
        assert "SECRET_PROVIDER_KEY" not in json.dumps(event.sent)
        assert {k: v for k, v in plugin._test_store.items() if k != TOKEN_USAGE_KEY} == {
            k: v for k, v in previous.items() if k != TOKEN_USAGE_KEY
        }
        runs = await plugin._token_runs()
        debug = next(r for r in runs if r["kind"] == "debug")
        assert debug["id"] == steps["调试汇总"]["run_id"]
        assert sum(c["usage"]["total_tokens"] for c in debug["calls"]) == 170
        assert "完整测试提示" not in json.dumps(
            plugin._test_store[TOKEN_USAGE_KEY], ensure_ascii=False
        )

    asyncio.run(run())


@pytest.mark.parametrize("review", ['{"reject":true,"intent":"综合"}', "invalid JSON"])
def test_rejected_or_malformed_review_reports_usage_and_skips_interpretation(review):
    async def run():
        plugin, event = admin_plugin()
        plugin.context.llm_generate.side_effect = [response(review, 40, 10)]
        await command(plugin, event)
        steps = trace_steps(event)
        assert plugin.context.llm_generate.await_count == 1
        assert "解卦请求" not in steps
        assert steps["预审响应"]["completion_text"] == review
        assert steps["调试汇总"]["已知总 Token"] == 50
        plugin._render_and_send_chart.assert_awaited_once()
        assert "liuyao_case_library" not in plugin._test_store

    asyncio.run(run())


@pytest.mark.parametrize("stage", ["review", "interpretation"])
def test_model_failure_reports_unknown_usage_and_keeps_chart(stage):
    async def run():
        plugin, event = admin_plugin()
        prefix = [] if stage == "review" else [response(REVIEW, 40, 10)]
        plugin.context.llm_generate.side_effect = [*prefix, RuntimeError("secret=PRIVATE_KEY")]
        await command(plugin, event)
        steps = trace_steps(event)
        assert steps["调试汇总"]["总量未知调用次数"] == 1
        assert steps["调试汇总"]["已知总 Token"] == (0 if stage == "review" else 50)
        assert "PRIVATE_KEY" not in json.dumps(event.sent)
        plugin._render_and_send_chart.assert_awaited_once()
        assert used_count(plugin) == 0

    asyncio.run(run())


def test_timeout_and_cancellation_keep_usage_record_and_summary():
    async def run():
        for cancel in (False, True):
            plugin, event = admin_plugin()
            entered = asyncio.Event()

            async def blocked(entered=entered, **kwargs):
                entered.set()
                await asyncio.Future()

            plugin.context.llm_generate.side_effect = blocked
            original = plugin._bounded_config_int
            plugin._bounded_config_int = lambda key, *args, cancel=cancel, original=original: (
                0.02
                if key == "command_analysis_timeout_seconds" and not cancel
                else original(key, *args)
            )
            task = asyncio.create_task(command(plugin, event))
            await entered.wait()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                await task
            steps = trace_steps(event)
            assert steps["调试汇总"]["总量未知调用次数"] == 1
            assert steps["预审调用异常"]["调用记录"][0]["status"] == "cancelled"
            assert (await plugin._token_runs())[0]["calls"][0]["status"] == "cancelled"

    asyncio.run(run())


def test_missing_usage_is_unknown_not_zero():
    async def run():
        plugin, event = admin_plugin()
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text=REVIEW),
            SimpleNamespace(completion_text=ANALYSIS),
        ]
        await command(plugin, event)
        steps = trace_steps(event)
        assert steps["预审响应"]["调用记录"][0]["usage"] is None
        assert steps["调试汇总"]["总量未知调用次数"] == 2

    asyncio.run(run())


@pytest.mark.parametrize("condition", ["empty", "closed", "banned", "identity_error"])
def test_debug_checks_administrator_input_bans_and_group_gate(condition):
    async def run():
        plugin, event = admin_plugin()
        if condition == "closed":
            plugin._test_store["method_switches"] = {}
        elif condition == "banned":
            plugin._test_store["liuyao_users_v1"] = {"users": {"20002": {"banned": True}}}
        elif condition == "identity_error":
            event.is_admin = lambda: (_ for _ in ()).throw(RuntimeError("failed"))
        result = await command(plugin, event, "" if condition == "empty" else QUESTION)
        assert result or "流程停止" in trace_steps(event)
        plugin.context.llm_generate.assert_not_awaited()
        assert used_count(plugin) == 0

    asyncio.run(run())


def test_debug_storage_failure_prevents_unrecorded_model_calls():
    async def run():
        plugin, event = admin_plugin()
        put = plugin.put_kv_data

        async def fail(key, value):
            if key == TOKEN_USAGE_KEY:
                raise OSError("storage failed")
            await put(key, value)

        plugin.put_kv_data = fail
        await command(plugin, event)
        plugin.context.llm_generate.assert_not_awaited()
        assert trace_steps(event)["调试汇总"]["模型调用次数"] == 0

    asyncio.run(run())


def test_debug_message_failure_does_not_repeat_or_cancel_model_work():
    async def run():
        plugin, event = admin_plugin()
        send = event.send

        async def flaky(message):
            if "预审请求" in message["text"]:
                raise OSError("delivery failed")
            await send(message)

        event.send = flaky
        await command(plugin, event)
        assert plugin.context.llm_generate.await_count == 2
        assert trace_steps(event)["调试汇总"]["调试消息发送失败段数"] > 0

    asyncio.run(run())


def test_trace_cannot_enable_debug_for_non_admin_via_shared_flow():
    async def run():
        plugin, event = admin_plugin()
        event._astrbot_admin = False
        result = await plugin._content_reply(event, QUESTION, trace=DebugTrace(event))
        assert "无权限" in result and not event.sent
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


def test_model_settings_expose_only_safe_fields_save_immediately_and_detect_conflicts():
    async def run():
        plugin, event = admin_plugin()
        page = await plugin._page_models(query={})
        assert "SECRET_PROVIDER_KEY" not in json.dumps(page)
        assert page["providers"] == [
            {"id": "admin-reading", "model": "model-b"},
            {"id": "admin-review", "model": "model-a"},
        ]
        payload = {
            "revision": page["revision"],
            ADMIN_MODEL_KEYS[0]: "",
            ADMIN_MODEL_KEYS[1]: "admin-review",
        }
        result = await plugin._admin_save_models(payload=payload, actor="admin")
        assert result["saved"] and plugin.config.persisted[ADMIN_MODEL_KEYS[1]] == "admin-review"
        assert (
            await plugin._analysis_provider_id(event, "command_pre_review_provider_id")
            == "plugin-model"
        )
        assert (
            await plugin._analysis_provider_id(event, "command_interpretation_provider_id")
            == "admin-review"
        )
        with pytest.raises(ManagementError, match="已变化"):
            await plugin._admin_save_models(payload=payload, actor="admin")

    asyncio.run(run())


@pytest.mark.parametrize("bad", [None, [], 123, "not-a-provider", "x" * 257])
def test_model_settings_reject_invalid_values_without_mutating_config(bad):
    async def run():
        plugin, _event = admin_plugin()
        page = await plugin._page_models(query={})
        before = dict(plugin.config)
        with pytest.raises(ManagementError):
            await plugin._admin_save_models(
                payload={
                    "revision": page["revision"],
                    ADMIN_MODEL_KEYS[0]: bad,
                    ADMIN_MODEL_KEYS[1]: "",
                },
                actor="admin",
            )
        assert plugin.config == before

    asyncio.run(run())


def test_model_settings_roll_back_when_saving_fails():
    async def run():
        plugin, _event = admin_plugin()
        page = await plugin._page_models(query={})
        before = dict(plugin.config)
        plugin.config.save_config = lambda: (_ for _ in ()).throw(OSError("disk failure"))
        with pytest.raises(ManagementError, match="保存失败"):
            await plugin._admin_save_models(
                payload={"revision": page["revision"], **dict.fromkeys(ADMIN_MODEL_KEYS, "")},
                actor="admin",
            )
        assert plugin.config == before

    asyncio.run(run())


def test_debug_command_is_recognized_for_mention_help():
    plugin, _event = admin_plugin()
    assert plugin._is_plugin_command_message("/六爻test 测试问题")


def test_concurrent_debug_requests_keep_traces_and_token_runs_separate():
    async def run():
        plugin, first = admin_plugin()
        second = copy.deepcopy(first)
        second.message_obj.message_id = "second-debug"

        async def generate(**kwargs):
            await asyncio.sleep(0)
            return response(REVIEW if '"reject"' in kwargs["prompt"] else ANALYSIS)

        plugin.context.llm_generate.side_effect = generate
        await asyncio.gather(
            command(plugin, first, "DEBUG_ONE"), command(plugin, second, "DEBUG_TWO")
        )
        assert "DEBUG_TWO" not in json.dumps(first.sent)
        assert "DEBUG_ONE" not in json.dumps(second.sent)
        summaries = [trace_steps(event)["调试汇总"] for event in (first, second)]
        assert all(item["模型调用次数"] == 2 for item in summaries)
        assert len({item["run_id"] for item in summaries}) == 2
        page = await plugin._page_usage(query={})
        assert page["total"] == 2
        assert all(
            item["kind"] == "debug" and not item["usage"]["historical"] for item in page["items"]
        )

    asyncio.run(run())


def test_slow_debug_delivery_does_not_use_model_timeout_budget():
    async def run():
        plugin, event = admin_plugin()
        send = event.send

        async def slow(message):
            if "请求" in message["text"].partition("\n")[0]:
                await asyncio.sleep(0.25)
            await send(message)

        event.send = slow
        original = plugin._bounded_config_int
        plugin._bounded_config_int = lambda key, *args: (
            0.2 if key == "command_analysis_timeout_seconds" else original(key, *args)
        )
        await command(plugin, event)
        steps = trace_steps(event)
        assert steps["解卦响应"]["completion_text"] == ANALYSIS
        assert steps["调试汇总"]["已知总 Token"] == 170

    asyncio.run(run())
