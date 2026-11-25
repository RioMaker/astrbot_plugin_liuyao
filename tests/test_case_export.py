from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
from io import BytesIO
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest
from test_permissions import _make_enabled_plugin

import admin_page
from case_export import build_case_export
from user_management import CASES_KEY


@pytest.fixture
def export_endpoint(monkeypatch):
    plugin = _make_enabled_plugin()
    plugin.get_kv_data = AsyncMock(return_value={})
    plugin.put_kv_data = AsyncMock()
    routes = {}
    plugin.context = SimpleNamespace(
        register_web_api=lambda route, handler, methods, desc: routes.update(
            {route: (handler, methods)}
        )
    )
    web = ModuleType("astrbot.api.web")
    web.request = SimpleNamespace(username="admin", query={})
    web.json_response = lambda value: (value, 200)
    web.error_response = lambda message, status_code=400: (message, status_code)
    web.stream_response = lambda content, **kwargs: SimpleNamespace(content=content, **kwargs)
    monkeypatch.setitem(sys.modules, "astrbot.api.web", web)
    plugin._register_admin_page()
    handler, methods = routes["/astrbot_plugin_liuyao/manage/export/cases"]
    assert methods == ["GET"]
    return plugin, web.request, handler


async def read_export(handler, filename_prefix="liuyao_cases"):
    response = await handler()
    content = b"".join([chunk async for chunk in response.content])
    assert response.content_type == "application/zip"
    assert response.headers["Cache-Control"] == "no-store"
    assert int(response.headers["Content-Length"]) == len(content)
    assert re.fullmatch(
        rf'attachment; filename="{filename_prefix}_\d{{8}}_\d{{6}}\.zip"',
        response.headers["Content-Disposition"],
    )
    with ZipFile(BytesIO(content)) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {"liuyao_case_library.json", "README.md"}
        return (
            json.loads(archive.read("liuyao_case_library.json")),
            archive.read("README.md").decode("utf-8"),
        )


def test_export_is_complete_lossless_read_only_and_ignores_filters(export_endpoint):
    plugin, request, handler = export_endpoint
    cases = [
        {
            "id": f"LY-{index}",
            "serial": index + 1,
            "group_id": str(index % 3),
            "caster_id": str(index % 5),
            "question": "今年的项目能完成吗？🌱\n保留换行",
            "status": "analyzed" if index % 2 else "awaiting_analysis",
            "analysis": "原分析\n断语：仍需观察",
            "cast": {"lines": [6, 7, 8, 9, 7, 8], "chart": {"schema_version": 1}},
            "feedback": [{"text": "延期了两周", "valid": True}] if index % 2 else [],
            "admin_note": "待回访",
            "feedback_review": {"contexts": [{"role": "user", "content": "原记录"}]},
            "future_field": {"retain": [None, False, 0]},
        }
        for index in range(45)
    ]
    payload = {
        "schema_version": 2,
        "cases": cases,
        "next_serial": 900,
        "user_scores": {"0": 70},
        "valid_feedback_events": {"0": ["2026-11-12T09:00:00+08:00"] * 2},
        "future_metadata": {"keep": True},
    }
    original = copy.deepcopy(payload)
    plugin.config.update(case_library_enabled=False, case_library_max_records=20)
    plugin.get_kv_data.return_value = payload
    request.query = {"search": "不存在", "page": "invalid", "sender": "0", "state": "pending"}
    exported, guide = asyncio.run(read_export(handler))
    assert exported == original == payload
    assert len(exported["cases"]) == 45
    plugin.get_kv_data.assert_awaited_once_with(
        CASES_KEY, {"schema_version": 2, "cases": [], "next_serial": 1}
    )
    plugin.put_kv_data.assert_not_awaited()
    for field in (
        "next_serial",
        "schema_version",
        "moving_lines",
        "primary_relatives",
        "feedback_review",
        "legacy_rejected_feedback",
        "user_scores",
        "valid_feedback_events",
        "system_prompt",
        "xun_kong",
        "admin_updated_by",
    ):
        assert f"`{field}`" in guide
    assert "自下而上" in guide and "get_kv_data" in guide and "5000" in guide


@pytest.mark.parametrize("legacy_list", [False, True], ids=["library", "legacy-list"])
def test_feedback_export_preserves_closed_cases_and_metadata(export_endpoint, legacy_list):
    plugin, request, handler = export_endpoint
    reviewed = {
        "id": "reviewed",
        "serial": 42,
        "feedback": [{"valid": False}, {"valid": True, "text": "提前完工"}],
        "future_field": {"keep": True},
    }
    legacy = {"id": "legacy", "feedback": [{"text": "没有应验"}]}
    cases = [
        reviewed,
        {"id": "rejected", "status": "feedback_recorded", "feedback": [{"valid": False}]},
        {"id": "retry", "feedback_review": {"last_decision": {"action": "retry"}}},
        {"id": "pending", "analysis": "原断", "feedback": []},
        legacy,
        {"id": "malformed", "feedback": None},
        "historical",
    ]
    metadata = {"next_serial": 87, "user_scores": {"bob": 6}, "future_metadata": [1]}
    payload = cases if legacy_list else {**metadata, "cases": cases}
    original = copy.deepcopy(payload)
    plugin.get_kv_data.return_value = payload
    request.query = {"scope": "feedback", "state": "pending", "page": "2", "search": "无匹配"}

    exported, _guide = asyncio.run(read_export(handler, "liuyao_cases_feedback"))

    expected = [reviewed, legacy]
    assert exported == (expected if legacy_list else {**metadata, "cases": expected})
    assert payload == original
    plugin.put_kv_data.assert_not_awaited()


def test_feedback_export_with_no_matches_returns_an_empty_case_list(export_endpoint):
    plugin, request, handler = export_endpoint
    plugin.get_kv_data.return_value = {"cases": [{"id": "chart-only"}], "next_serial": 5}
    request.query = {"scope": "feedback"}
    exported, _guide = asyncio.run(read_export(handler, "liuyao_cases_feedback"))
    assert exported == {"cases": [], "next_serial": 5}


def test_unknown_export_scope_is_rejected_before_reading_the_library(export_endpoint):
    plugin, request, handler = export_endpoint
    request.query = {"scope": "mistyped"}
    assert asyncio.run(handler())[1] == 400
    plugin.get_kv_data.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        [{"id": "old", "unknown": "保留"}, {"missing_id": True}, "historical"],
        {"schema_version": 1, "cases": [{"id": "old"}, {"id": "duplicate", "serial": 0}]},
    ],
)
def test_export_preserves_legacy_shapes_without_migration(payload):
    with ZipFile(BytesIO(build_case_export(payload))) as archive:
        assert json.loads(archive.read("liuyao_case_library.json")) == payload
        assert archive.read("README.md").decode("utf-8").startswith("# 六爻卦例导出说明")


def test_export_of_missing_key_returns_empty_library(export_endpoint):
    plugin, _request, handler = export_endpoint
    plugin.get_kv_data.side_effect = lambda key, default: default
    exported, _guide = asyncio.run(read_export(handler))
    assert exported == {"schema_version": 2, "cases": [], "next_serial": 1}
    plugin.put_kv_data.assert_not_awaited()


def test_export_checks_identity_and_unloaded_plugin_before_reading(export_endpoint):
    plugin, request, handler = export_endpoint
    request.username = None
    assert asyncio.run(handler())[1] == 401
    request.username = "admin"
    plugin._admin_page_active = False
    assert asyncio.run(handler())[1] == 503
    plugin.get_kv_data.assert_not_awaited()


def test_export_storage_failure_does_not_return_a_successful_empty_backup(export_endpoint):
    plugin, _request, handler = export_endpoint
    plugin.get_kv_data.side_effect = OSError("private storage details")
    message, status = asyncio.run(handler())
    assert status == 500 and "private storage details" not in message
    plugin.put_kv_data.assert_not_awaited()


def test_export_snapshot_is_detached_and_releases_writer_lock(export_endpoint, monkeypatch):
    plugin, _request, handler = export_endpoint
    payload = {"cases": [{"id": "old", "feedback": [{"text": "原反馈"}]}]}
    plugin.get_kv_data.return_value = payload
    original = copy.deepcopy(payload)

    def pack(snapshot, *, feedback_only):
        assert not plugin._case_lock_instance().locked()
        payload["cases"][0]["feedback"][0]["text"] = "稍后的修改"
        return build_case_export(snapshot, feedback_only=feedback_only)

    async def get_data(key, default):
        assert plugin._case_lock_instance().locked()
        return payload

    plugin.get_kv_data.side_effect = get_data
    monkeypatch.setattr(admin_page, "build_case_export", pack)
    exported, _guide = asyncio.run(read_export(handler))
    assert exported == original
    assert payload != original
    plugin.put_kv_data.assert_not_awaited()
