from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_permissions import _make_enabled_plugin

from token_usage import (
    TOKEN_USAGE_KEY,
    case_usage_key,
    extract_usage,
    overview,
    summarize,
    usage_rows,
)
from user_management import ManagementError, case_revision


def response(text=ANALYSIS, input_tokens=100, output_tokens=20):
    return SimpleNamespace(
        completion_text=text,
        role="assistant",
        usage=SimpleNamespace(input_other=input_tokens - 10, input_cached=10, output=output_tokens),
    )


@pytest.mark.parametrize(
    "usage, expected",
    [
        ({"input_other": 70, "input_cached": 30, "output": 20}, (100, 20, 30, 120)),
        ({"input_other": 0, "input_cached": 0, "output": 0}, (0, 0, 0, 0)),
        (
            {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_tokens_details": {"cached_tokens": 30},
            },
            (100, 20, 30, 120),
        ),
        (
            {
                "input_tokens": 80,
                "output_tokens": 10,
                "input_tokens_details": {"cached_tokens": 20},
            },
            (80, 10, 20, 90),
        ),
        (
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 30,
                "cache_creation_input_tokens": 40,
            },
            (170, 20, 30, 190),
        ),
        (
            {
                "prompt_token_count": 100,
                "candidates_token_count": 20,
                "thoughts_token_count": 10,
                "cached_content_token_count": 30,
                "total_token_count": 130,
            },
            (100, 30, 30, 130),
        ),
        ({"total_tokens": 42}, (None, None, None, 42)),
        ({"prompt_tokens": 40}, (40, None, None, None)),
    ],
)
def test_usage_formats_do_not_double_count_cached_or_reasoning_tokens(usage, expected):
    for data in (usage, SimpleNamespace(**usage)):
        for wrapper in (
            {"usage": data},
            SimpleNamespace(raw_completion=SimpleNamespace(usage=data)),
        ):
            assert tuple(extract_usage(wrapper).values()) == expected


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": -1},
        {"total_tokens": True},
        {"total_tokens": "120"},
        {"total_tokens": 2.5},
    ],
)
def test_missing_or_invalid_usage_is_not_zero(usage):
    assert extract_usage(SimpleNamespace(usage=usage)) is None


def test_raw_usage_fallback_and_gemini_metadata():
    assert (
        extract_usage(
            {
                "usage": {},
                "raw_completion": {
                    "usage_metadata": {
                        "prompt_token_count": 4,
                        "candidates_token_count": 2,
                    }
                },
            }
        )["total_tokens"]
        == 6
    )


def test_cast_tracks_review_interpretation_reuse_and_feedback_without_overwriting_original():
    async def run():
        plugin, event = prepare()
        plugin.context.llm_generate.side_effect = [
            response('{"reject":false,"intent":"事业"}', 40, 10),
            response(),
        ]
        await plugin._dispatch_liuyao(event, QUESTION)
        page = await plugin._page_cases(query={})
        case_id = page["items"][0]["id"]
        case = (await plugin._page_case(case_id, query={}))["case"]
        assert page["items"][0]["token_usage"]["total_tokens"] == 170
        original = copy.deepcopy(case)
        plugin.context.llm_generate.side_effect = [
            response('{"reject":false,"intent":"事业"}', 40, 10),
            response(),
        ]
        event.message_obj.message_id = "reuse"
        await plugin.reuse_liuyao_tool(event, question="补充情况")
        plugin.context.llm_generate.side_effect = [response("invalid feedback JSON", 50, 10)]
        with pytest.raises(ValueError):
            await plugin._review_case_feedback(event, case, "补充反馈")
        data = await plugin._page_usage(query={})
        assert data["total"] == 1
        assert data["summary"]["total_tokens"] == 400
        assert data["summary"]["calls"] == 5
        assert data["summary"]["casts"] == 1
        assert (await plugin._page_case(case_id, query={}))["case"] == original
        detail = await plugin._page_usage_detail(case_usage_key(case), query={})
        assert detail["record"]["serial"] == case["serial"]
        assert set(detail["stages"]) == {
            "pre_review",
            "interpretation",
            "reuse_review",
            "reuse_interpretation",
            "feedback",
        }
        # Case deletion and plugin reload must not decrease any token totals.
        await plugin._admin_change_case(
            case_id, {"action": "delete", "revision": case_revision(case)}, "admin"
        )
        reloaded = _make_enabled_plugin()
        reloaded.get_kv_data = plugin.get_kv_data
        reloaded.put_kv_data = plugin.put_kv_data
        assert (await reloaded._page_cases(query={}))["total"] == 0
        assert (await reloaded._page_usage(query={}))["summary"] == data["summary"]
        users = await reloaded._page_users(query={})
        assert users["items"][0]["token_usage"]["total_tokens"] == 400
        assert (await reloaded._page_user("20002", query={}))["token_overview"]["all"][
            "total_tokens"
        ] == 400

    asyncio.run(run())


@pytest.mark.parametrize("library", [True, False])
def test_rejected_reviews_are_counted_even_without_archived_case(library):
    async def run():
        plugin, event = prepare(library=library)
        plugin.context.llm_generate.side_effect = [response('{"reject":true,"intent":"综合"}')]
        await plugin._dispatch_liuyao(event, QUESTION)
        data = await plugin._page_usage(query={})
        assert data["summary"]["casts"] == 1
        assert data["summary"]["total_tokens"] == 120
        assert not data["items"][0]["case_id"]
        assert (await plugin._page_cases(query={}))["total"] == 0

    asyncio.run(run())


def test_disabled_library_still_links_reuse_and_zero_ai_casts_are_distinct_from_legacy():
    async def run():
        plugin, event = prepare(library=False, limit=1)
        plugin.context.llm_generate.side_effect = [
            response('{"reject":false,"intent":"事业"}'),
            response(),
        ]
        await plugin._dispatch_liuyao(event, QUESTION)
        event.message_obj.message_id = "no-quota"
        await plugin._dispatch_liuyao(event, "另一件事")
        rows = (await plugin._page_usage(query={"search": "另一件事"}))["items"]
        assert len(rows) == 1 and rows[0]["usage"]["tracked"]
        assert rows[0]["usage"]["calls"] == 0 and rows[0]["usage"]["total_tokens"] == 0
        assert not rows[0]["usage"]["historical"]
        assert not summarize([])["tracked"]

    asyncio.run(run())


def test_unknown_response_and_ping_are_reported_separately_from_casts():
    async def run():
        plugin, event = prepare()
        plugin.context.llm_generate.side_effect = [SimpleNamespace(completion_text="PONG")]
        assert [reply async for reply in plugin.llm_ping_command(event)]
        data = await plugin._page_usage(query={})
        assert data["summary"]["casts"] == 0
        assert data["summary"]["unknown_calls"] == 1
        assert data["summary"]["reported_calls"] == 0
        assert data["items"][0]["kind"] == "ping"

    asyncio.run(run())


@pytest.mark.parametrize("failure", [RuntimeError("failed"), asyncio.CancelledError()])
def test_failed_and_cancelled_calls_persist_as_unknown(failure):
    async def run():
        plugin, event = prepare()
        plugin.context.llm_generate.side_effect = failure
        token_run = plugin._new_token_run(event, kind="ping")
        with pytest.raises(type(failure)):
            await plugin._token_llm(token_run, "ping", chat_provider_id="mock", prompt="x")
        usage = (await plugin._page_usage(query={}))["summary"]
        assert usage["calls"] == usage["unknown_calls"] == 1
        assert usage["reported_calls"] == 0

    asyncio.run(run())


def test_timeout_persists_unknown_and_never_blocks_followup_recording():
    async def run():
        plugin, event = prepare()
        entered = asyncio.Event()

        async def blocked(**kwargs):
            entered.set()
            await asyncio.Future()

        plugin.context.llm_generate.side_effect = blocked
        token_run = plugin._new_token_run(event, kind="ping")
        task = asyncio.create_task(plugin._token_llm(token_run, "ping", prompt="x"))
        await entered.wait()
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(task, 0.01)
        assert entered.is_set()
        assert (await plugin._page_usage(query={}))["summary"]["unknown_calls"] == 1
        plugin.context.llm_generate.side_effect = None
        plugin.context.llm_generate.return_value = response()
        await plugin._token_llm(token_run, "ping", prompt="x")
        assert (await plugin._page_usage(query={}))["summary"]["total_tokens"] == 120

    asyncio.run(run())


def test_parallel_calls_and_repeated_response_ids_are_counted_once_per_actual_call():
    async def run():
        plugin, event = prepare()
        plugin.context.llm_generate.side_effect = None
        plugin.context.llm_generate.return_value = response()
        token_run = plugin._new_token_run(event, kind="ping")
        await asyncio.gather(*(plugin._token_llm(token_run, "ping", prompt="x") for _ in range(25)))
        totals = (await plugin._page_usage(query={}))["summary"]
        assert totals["total_tokens"] == 3000 and totals["calls"] == 25

    asyncio.run(run())


def test_persistence_failure_before_call_prevents_untracked_request():
    async def run():
        plugin, event = prepare()
        plugin.put_kv_data = AsyncMock(side_effect=OSError("disk full"))
        with pytest.raises(OSError):
            await plugin._token_llm(plugin._new_token_run(event, kind="ping"), "ping", prompt="x")
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


def test_failed_final_write_retains_pending_record_and_does_not_destroy_model_reply():
    async def run():
        plugin, event = prepare()
        plugin.context.llm_generate.side_effect = [response()]
        original = plugin.put_kv_data
        count = 0

        async def fail_final(key, value):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk full")
            await original(key, value)

        plugin.put_kv_data = fail_final
        result = await plugin._token_llm(
            plugin._new_token_run(event, kind="ping"), "ping", prompt="x"
        )
        assert result.completion_text == ANALYSIS
        data = await plugin._page_usage(query={})
        assert data["storage_warning"] and data["summary"]["unknown_calls"] == 1

    asyncio.run(run())


def test_completion_write_survives_repeated_cancellation_without_double_counting():
    async def run():
        plugin, event = prepare()
        plugin.context.llm_generate.side_effect = [response()]
        original = plugin.put_kv_data
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(key, value):
            if any(c["usage"] for r in value["runs"].values() for c in r["calls"]):
                entered.set()
                await release.wait()
            await original(key, value)

        plugin.put_kv_data = slow
        task = asyncio.create_task(
            plugin._token_llm(plugin._new_token_run(event, kind="ping"), "ping", prompt="x")
        )
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        data = await plugin._page_usage(query={})
        assert data["summary"]["total_tokens"] == 120 and data["summary"]["calls"] == 1

    asyncio.run(run())


def sample_run(at, *, user="u1", group="g1"):
    return {
        "id": at + user + group,
        "cast_key": "cast-" + at + user + group,
        "cast_at": at,
        "kind": "cast",
        "created_at": at,
        "caster_id": user,
        "caster_name": user,
        "group_id": group,
        "question": "question",
        "calls": [
            {
                "id": at,
                "at": at,
                "status": "returned",
                "stage": "pre_review",
                "provider": "mock",
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "cached_tokens": 10,
                    "total_tokens": 100,
                },
            }
        ],
    }


def test_beijing_calendar_day_week_month_boundaries_and_year_rollover():
    runs = [
        sample_run(at)
        for at in [
            "2026-12-27T23:59:59+08:00",
            "2026-12-28T00:00:00+08:00",
            "2026-12-31T23:59:59+08:00",
            "2027-01-01T00:00:00+08:00",
            "2027-01-03T23:59:59+08:00",
            "2027-01-04T00:00:00+08:00",
        ]
    ]
    # UTC Jan 3 16:00 is Beijing Monday Jan 4 00:00.
    data = overview(runs, datetime(2027, 1, 3, 16, tzinfo=timezone.utc))
    assert data["all"]["total_tokens"] == 600
    assert data["today"]["total_tokens"] == data["week"]["total_tokens"] == 100
    assert data["month"]["total_tokens"] == 300
    weeks = usage_rows(runs, "weekly")
    assert [(r["id"], r["usage"]["total_tokens"]) for r in weeks] == [
        ("2027-01-04", 100),
        ("2026-12-28", 400),
        ("2026-12-21", 100),
    ]
    assert len(usage_rows(runs, "monthly")) == 2


def test_group_user_date_filters_pagination_and_legacy_case_usage():
    async def run():
        plugin = _make_enabled_plugin()
        runs = [sample_run(f"2026-11-{day:02}T08:00:00+08:00") for day in range(1, 26)]
        runs += [
            sample_run("2026-11-01T09:00:00+08:00", group="g2"),
            sample_run("2026-11-01T10:00:00+08:00", user="u2"),
        ]
        plugin._test_store[TOKEN_USAGE_KEY] = {"runs": {r["id"]: r for r in runs}}
        page = await plugin._page_usage(
            query={
                "group": "g1",
                "sender": "u1",
                "start": "2026-11-01",
                "end": "2026-11-25",
                "page": "2",
            }
        )
        assert page["total"] == 25 and page["pages"] == 2 and len(page["items"]) == 5
        assert page["summary"]["total_tokens"] == 2500
        assert (await plugin._page_usage(query={"start": "2026-11-25", "end": "2026-11-25"}))[
            "summary"
        ]["total_tokens"] == 100
        assert len((await plugin._page_usage(query={"view": "groups"}))["items"]) == 2
        assert len((await plugin._page_usage(query={"view": "users"}))["items"]) == 2
        assert (await plugin._page_usage(query={"sender": "unknown"}))["summary"]["calls"] == 0
        old = sample_run("2026-10-01T08:00:00+08:00")
        old["kind"] = "feedback"
        assert summarize([old])["historical"]
        assert not summarize([old, runs[0]])["historical"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "query",
    [
        {"view": "invalid"},
        {"page": "-1"},
        {"start": "2026-02-30"},
        {"start": "2026-1-1"},
        {"start": "2026-11-02", "end": "2026-11-01"},
        {"group": ["x"]},
        {"sender": "x" * 129},
    ],
)
def test_usage_api_rejects_invalid_filters(query):
    async def run():
        with pytest.raises(ManagementError):
            await _make_enabled_plugin()._page_usage(query=query)

    asyncio.run(run())


def test_usage_fingerprint_normalizes_equivalent_timezones():
    case = {
        "caster_id": "u",
        "group_id": "g",
        "created_at": "2026-11-01T08:00:00+08:00",
        "cast": {"lines": [6, 7, 8, 9, 6, 7]},
    }
    other = {**case, "created_at": "2026-11-01T00:00:00+00:00"}
    assert case_usage_key(case) == case_usage_key(other)
    assert case_usage_key(case) != case_usage_key({**case, "group_id": "other"})
