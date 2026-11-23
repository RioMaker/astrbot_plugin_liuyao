from __future__ import annotations

import asyncio
import copy
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_token_usage import response

from main import LiuyaoPlugin
from token_usage import TOKEN_USAGE_KEY, usage_rows
from user_management import CASES_KEY, ManagementError

REVIEW = '{"reject":false,"valid_question":true,"intent":"事业"}'
KEY = "reading-test-00000001"
PARAMS = {"source": "manual", "lines": "789678", "cast_at": "2026-10-22T08:30"}


@pytest.fixture
def dashboard(tmp_path, monkeypatch):
    base, event = prepare(limit=3)
    monkeypatch.setattr("workshop.history_path", lambda: tmp_path / "workshop.db")
    base.context.get_using_provider = lambda: SimpleNamespace(
        meta=lambda: SimpleNamespace(id="system-default")
    )
    plugin = LiuyaoPlugin(
        base.context,
        {
            **base.config,
            "command_pre_review_provider_id": "review",
            "command_interpretation_provider_id": "interpret",
            "admin_interpretation_provider_id": "admin-only",
            "command_interpretation_prompt": "当前生效规则\n{question}\n{chart}\n{max_chars}",
        },
    )
    plugin.get_kv_data, plugin.put_kv_data = base.get_kv_data, base.put_kv_data
    plugin._test_store = base._test_store
    plugin._render_and_send_chart = base._render_and_send_chart
    plugin._send_reply_payload = base._send_reply_payload
    plugin._send_caster_reply = base._send_caster_reply
    return plugin, event


async def start(plugin, **changes):
    payload = {"request_id": KEY, "question": QUESTION, **PARAMS, **changes}
    return (await plugin._admin_start_reading_test(payload=payload, actor="operator"))["id"]


async def finished(plugin, test_id):
    await asyncio.gather(*list(plugin._reading_test_tasks))
    return await plugin._page_reading_test(str(test_id), query={})


@pytest.mark.parametrize("source", ["manual", "case"])
def test_same_requests_as_normal_command_isolated_history_and_tokens(
    dashboard, source, monkeypatch
):
    plugin, event = dashboard
    monkeypatch.setattr("cast_policy.cast_instant", lambda: pytest.fail("unexpected RNG"))

    async def run():
        normal = "789678 时间=202610220830 " + QUESTION
        await plugin._manual_command_reply(event, normal, analyze=source == "manual")
        case = plugin._test_store[CASES_KEY]["cases"][0]
        if source == "case":
            await plugin._manual_command_reply(event, f"#{case['serial']}", analyze=True)
            case = plugin._test_store[CASES_KEY]["cases"][0]
            case["feedback"].append({"valid": True, "text": "这是揭晓的答案"})
        real_calls = [c.kwargs for c in plugin.context.llm_generate.await_args_list]
        original = copy.deepcopy(plugin._test_store)
        plugin._render_and_send_chart.reset_mock()
        plugin._send_reply_payload.reset_mock()
        plugin._send_caster_reply.reset_mock()
        plugin.config["default_enabled"] = False
        plugin.context.llm_generate.reset_mock()
        plugin.context.llm_generate.side_effect = [response(REVIEW), response()]
        kwargs = {"source": source, "serial": case["serial"]}
        if source == "case":
            kwargs["question"] = ""
        ids = await asyncio.gather(start(plugin, **kwargs), start(plugin, **kwargs))
        assert ids[0] == ids[1]
        result = await finished(plugin, ids[0])
        report = result["report"]
        assert result["status"] == "success" and report["text"] == ANALYSIS
        assert report["verdict"] == "条件落实后可按期完成。"
        steps = report["steps"]
        assert all(
            {"input_summary", "inputs", "output_summary", "outputs"} <= s.keys() for s in steps
        )
        assert steps[0]["outputs"]["排盘快照"] == case["cast"]["chart"]
        assert steps[0]["outputs"]["六爻（自下而上）"] == [7, 8, 9, 6, 7, 8]
        requests = [s for s in steps if s["title"].endswith("模型调用")]
        assert [s["inputs"]["request"] for s in requests] == real_calls
        assert real_calls == [c.kwargs for c in plugin.context.llm_generate.await_args_list]
        assert all("这是揭晓的答案" not in c["prompt"] for c in real_calls)
        assert requests[0]["outputs"]["completion_text"] == REVIEW
        assert requests[1]["outputs"]["completion_text"] == ANALYSIS
        assert requests[1]["inputs"]["request"]["chat_provider_id"] == "interpret"
        assert report["usage"]["calls"] == 2 and report["usage"]["total_tokens"] == 240
        assert {k: v for k, v in plugin._test_store.items() if k != TOKEN_USAGE_KEY} == {
            k: v for k, v in original.items() if k != TOKEN_USAGE_KEY
        }
        runs = [r for r in await plugin._token_runs() if r["kind"] == "reading_test"]
        assert len(runs) == 1 and runs[0]["cast_key"] == report["run_id"]
        assert runs[0]["caster_id"] == runs[0]["group_id"] == ""
        assert usage_rows(runs, "users") == usage_rows(runs, "groups") == []
        assert all("report" not in r for r in runs)
        plugin._render_and_send_chart.assert_not_awaited()
        plugin._send_reply_payload.assert_not_awaited()
        plugin._send_caster_reply.assert_not_awaited()
        assert [c["id"] for c in await plugin._own_feedback_cases(event)] == [case["id"]]
        plugin._workshop_history = None
        assert await plugin._page_reading_test(ids[0], query={}) == result
        listing = await plugin._page_reading_tests(query={})
        assert listing["total"] == 1 and "report" not in listing["items"][0]

    asyncio.run(run())


def test_random_default_provider_backup_and_full_prompt(dashboard, monkeypatch):
    plugin, _ = dashboard
    plugin.config.update(
        command_pre_review_provider_id="", command_interpretation_backup_provider_id="backup"
    )
    from divination import CastResult

    monkeypatch.setattr("cast_policy.cast_instant", lambda: CastResult((6, 7, 8, 9, 7, 8)))
    plugin.context.llm_generate.side_effect = [
        response(REVIEW),
        RuntimeError("secret-token"),
        response(),
    ]

    async def run():
        result = await finished(plugin, await start(plugin, source="random"))
        assert result["status"] == "success"
        steps = result["report"]["steps"]
        assert steps[0]["outputs"]["六爻（自下而上）"] == [6, 7, 8, 9, 7, 8]
        models = [s for s in steps if s["title"].endswith("模型调用")]
        assert [s["inputs"]["request"]["chat_provider_id"] for s in models] == [
            "system-default",
            "interpret",
            "backup",
        ]
        assert models[1]["inputs"]["request"]["prompt"] == models[2]["inputs"]["request"]["prompt"]
        assert models[1]["outputs"]["error_type"] == "RuntimeError"
        assert "secret-token" not in json.dumps(result)
        assert result["report"]["usage"]["unknown_calls"] == 1
        assert any(s["title"] == "解卦切换备用模型" for s in steps)

    asyncio.run(run())


@pytest.mark.parametrize(
    ("question", "reply", "status", "calls"),
    [
        ("起卦", "", "skipped", 0),
        (QUESTION, '{"reject":true,"valid_question":true,"reason":"不适合"}', "skipped", 1),
        (QUESTION, "invalid json", "failed", 1),
    ],
)
def test_stopped_paths_keep_actual_steps(dashboard, question, reply, status, calls):
    plugin, _ = dashboard
    plugin.context.llm_generate.side_effect = [response(reply)]

    async def run():
        result = await finished(plugin, await start(plugin, question=question))
        assert result["status"] == status
        assert (
            result["report"]["usage"]["calls"] == plugin.context.llm_generate.await_count == calls
        )
        assert not result["report"]["verdict"]
        assert all(s["title"] != "解卦模型调用" for s in result["report"]["steps"])

    asyncio.run(run())


def test_invalid_inputs_do_not_create_runs(dashboard):
    plugin, _ = dashboard

    async def run():
        for kwargs in (
            {"source": "case", "serial": 404},
            {"lines": "78965"},
            {"cast_at": "2026-02-30T08:00"},
        ):
            with pytest.raises(ManagementError):
                await start(plugin, **kwargs)
        assert (await plugin._page_reading_tests(query={}))["total"] == 0
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


def test_slow_request_survives_page_retry_and_retains_interrupted_trace(dashboard):
    plugin, _ = dashboard

    async def run():
        entered = asyncio.Event()

        async def wait_forever(**kwargs):
            entered.set()
            await asyncio.Future()

        plugin.context.llm_generate = AsyncMock(side_effect=wait_forever)
        number = await start(plugin)
        await asyncio.wait_for(entered.wait(), 3)
        assert number == await start(plugin)
        assert (await plugin._page_reading_test(number, query={}))["status"] == "running"
        await plugin.terminate()
        result = await plugin._page_reading_test(number, query={})
        assert result["status"] == "interrupted" and plugin.context.llm_generate.await_count == 1
        assert result["report"]["steps"][-1]["outputs"]["error_type"] == "CancelledError"

    asyncio.run(run())


def test_report_endpoints_require_dashboard_login(dashboard, monkeypatch):
    plugin, _ = dashboard
    routes = {}
    plugin.context.register_web_api = lambda path, handler, *_: routes.update({path: handler})
    web = ModuleType("astrbot.api.web")
    web.request = SimpleNamespace(username=None, query={}, json=AsyncMock(return_value={}))
    web.error_response = lambda message, status_code: status_code
    web.json_response = lambda value: value
    monkeypatch.setitem(sys.modules, "astrbot.api.web", web)
    plugin._register_admin_page()
    prefix = "/astrbot_plugin_liuyao/manage/reading-tests"
    for suffix, kwargs in [("", {}), ("/<test_id>", {"test_id": "1"}), ("/start", {})]:
        assert asyncio.run(routes[prefix + suffix](**kwargs)) == 401
    plugin.context.llm_generate.assert_not_awaited()


def test_shutdown_before_first_step_closes_record(dashboard):
    plugin, _ = dashboard

    async def run():
        number = await start(plugin, source="random")
        await plugin.terminate()
        row = await plugin._page_reading_test(number, query={})
        assert row["status"] == "interrupted"
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


def test_saved_case_without_a_time_does_not_acquire_todays_calendar(dashboard):
    plugin, _ = dashboard
    plugin._test_store[CASES_KEY] = [
        {
            "id": "legacy",
            "serial": 71,
            "question": QUESTION,
            "cast": {"lines": [7, 8, 9, 6, 7, 8]},
        }
    ]

    async def run():
        number = await start(plugin, source="case", serial=71, question="")
        result = await finished(plugin, number)
        assert result["status"] == "success"
        chart = result["report"]["steps"][0]["outputs"]
        assert chart["起卦时间"] is None
        assert "缺少原始起卦时间" in chart["排盘快照"]["calendar"]["status"]
        assert "缺少原始起卦时间" in plugin.context.llm_generate.await_args.kwargs["prompt"]

    asyncio.run(run())
