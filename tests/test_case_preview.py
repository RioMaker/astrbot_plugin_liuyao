from __future__ import annotations

import asyncio
import base64
import copy
import sys
import threading
from datetime import datetime
from io import BytesIO
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from PIL import Image
from test_permissions import _make_enabled_plugin

from divination import CastResult
from paipan import chart_for
from renderer import LiuyaoImageRenderer
from user_management import CASES_KEY


@pytest.fixture
def preview_endpoint(monkeypatch):
    plugin = _make_enabled_plugin()
    cast = CastResult(
        (7, 8, 9, 6, 7, 8), cast_at=datetime.fromisoformat("2026-10-25T09:40:53+08:00")
    )
    record = {
        "id": "LY-preview",
        "serial": 302,
        "created_at": "2026-10-26T15:00:00+08:00",
        "caster_name": "测试群友",
        "caster_id": "20002",
        "group_id": "10001",
        "intent_label": "事业",
        "question": "这个项目能否按期完成？",
        "method": "随机起卦",
        "cast": {"lines": list(cast.lines), "chart": chart_for(cast, cast.cast_at)},
        "analysis": "历史解读",
        "verdict": "历史结论",
    }
    plugin.get_kv_data = AsyncMock(return_value={"cases": [record]})
    plugin.put_kv_data = AsyncMock()
    plugin.renderer = SimpleNamespace(render=Mock(wraps=LiuyaoImageRenderer(plugin.corpus).render))
    routes = {}
    plugin.context = SimpleNamespace(
        llm_generate=AsyncMock(),
        register_web_api=lambda route, handler, methods, desc: routes.update(
            {route: (handler, methods)}
        ),
    )
    web = ModuleType("astrbot.api.web")
    web.request = SimpleNamespace(username="admin", json=AsyncMock(return_value={}))
    web.json_response = lambda value: (value, 200)
    web.error_response = lambda message, status_code=400: (message, status_code)
    monkeypatch.setitem(sys.modules, "astrbot.api.web", web)
    plugin._register_admin_page()
    handler, methods = routes["/astrbot_plugin_liuyao/manage/cases/<case_id>/render"]
    assert methods == ["POST"]
    return plugin, record, web.request, handler


def assert_png(data):
    assert data["case_id"] == "LY-preview"
    prefix, encoded = data["image_data_url"].split(",", 1)
    assert prefix == "data:image/png;base64"
    with Image.open(BytesIO(base64.b64decode(encoded, validate=True))) as image:
        assert image.format == "PNG"
        assert image.width == 1240 and image.height > 700
        image.verify()


def test_preview_renders_original_snapshot_fresh_without_writes_or_ai(preview_endpoint):
    plugin, record, _request, handler = preview_endpoint
    original = copy.deepcopy(plugin.get_kv_data.return_value)
    plugin.config["case_library_enabled"] = False
    paths = []
    for _ in range(2):
        data, status = asyncio.run(handler(case_id=record["id"]))
        assert status == 200
        assert_png(data)
        args, kwargs = plugin.renderer.render.call_args
        assert args[0].lines == tuple(record["cast"]["lines"])
        assert args[0].chart == record["cast"]["chart"]
        assert kwargs["cast_at"].isoformat() == "2026-10-25T09:40:53+08:00"
        for key in ("caster_name", "caster_id", "group_id", "intent_label", "question", "method"):
            assert kwargs[key] == record[key]
        paths.append(kwargs["output_path"])
        assert not paths[-1].parent.exists()
    assert paths[0] != paths[1]
    assert plugin.get_kv_data.return_value == original
    assert all(call.args == (CASES_KEY, {}) for call in plugin.get_kv_data.call_args_list)
    plugin.put_kv_data.assert_not_awaited()
    plugin.context.llm_generate.assert_not_awaited()


@pytest.mark.parametrize("timestamp", ["2026-10-25T09:40:53+08:00", "2026-10-25T09:40:53", ""])
def test_legacy_preview_uses_only_original_time(preview_endpoint, timestamp):
    plugin, record, _request, handler = preview_endpoint
    del record["cast"]["chart"]
    record["created_at"] = timestamp
    data, status = asyncio.run(handler(case_id=record["id"]))
    assert status == 200
    assert_png(data)
    args, kwargs = plugin.renderer.render.call_args
    assert args[0].chart is None
    assert kwargs["cast_at"] == (datetime.fromisoformat(timestamp) if timestamp else None)
    calendar = chart_for(args[0], kwargs["cast_at"])["calendar"]
    if "+08:00" in timestamp:
        assert calendar["cast_at"] == timestamp
    else:
        assert calendar["status"].startswith("未计算")
    plugin.put_kv_data.assert_not_awaited()


def test_preview_requires_login_and_an_active_plugin(preview_endpoint):
    plugin, record, request, handler = preview_endpoint
    request.username = None
    assert asyncio.run(handler(case_id=record["id"]))[1] == 401
    request.username = "admin"
    plugin._admin_page_active = False
    assert asyncio.run(handler(case_id=record["id"]))[1] == 503
    plugin.get_kv_data.assert_not_awaited()
    plugin.renderer.render.assert_not_called()


def test_preview_missing_case_and_unavailable_renderer(preview_endpoint):
    plugin, record, _request, handler = preview_endpoint
    assert asyncio.run(handler(case_id="missing"))[1] == 404
    plugin.renderer.render.assert_not_called()
    plugin.renderer = None
    message, status = asyncio.run(handler(case_id=record["id"]))
    assert status == 503 and "渲染器不可用" in message


@pytest.mark.parametrize(
    "cast",
    [None, "invalid", {}, {"lines": [7] * 5}, {"lines": [0] * 6}, {"lines": [7] * 6, "chart": {}}],
)
def test_invalid_saved_data_is_a_recoverable_error(preview_endpoint, cast):
    plugin, record, _request, handler = preview_endpoint
    record["cast"] = cast
    message, status = asyncio.run(handler(case_id=record["id"]))
    assert status == 422 and "数据不完整" in message
    plugin.put_kv_data.assert_not_awaited()


def test_render_failure_cleans_partial_files_and_does_not_expose_paths(preview_endpoint):
    plugin, record, _request, handler = preview_endpoint
    paths = []

    def fail(cast, **kwargs):
        path = kwargs["output_path"]
        paths.append(path)
        path.write_bytes(b"partial")
        raise OSError("private font path")

    plugin.renderer.render.side_effect = fail
    message, status = asyncio.run(handler(case_id=record["id"]))
    assert status == 500 and "private font path" not in message
    assert paths and not paths[0].parent.exists()
    plugin.put_kv_data.assert_not_awaited()


def test_render_runs_off_loop_with_detached_snapshot_and_no_writer_lock(preview_endpoint):
    plugin, record, _request, handler = preview_endpoint
    original = copy.deepcopy(record)
    main_thread = threading.get_ident()
    renderer = plugin.renderer.render._mock_wraps

    def render(cast, **kwargs):
        assert threading.get_ident() != main_thread
        assert not plugin._case_lock_instance().locked()
        assert cast.chart is not record["cast"]["chart"]
        record["cast"]["chart"]["calendar"]["day"] = "稍后修改"
        assert cast.chart == original["cast"]["chart"]
        return renderer(cast, **kwargs)

    plugin.renderer.render.side_effect = render
    data, status = asyncio.run(handler(case_id=record["id"]))
    assert status == 200
    assert_png(data)
