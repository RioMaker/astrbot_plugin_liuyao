import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_analysis import ANALYSIS, prepare
from test_permissions import _make_enabled_plugin

from main import LiuyaoPlugin
from user_management import CASES_KEY, ManagementError


def replies(intent):
    review = {"reject": False, "valid_question": True, "intent": intent}
    return AsyncMock(
        side_effect=[
            SimpleNamespace(completion_text=json.dumps(review, ensure_ascii=False)),
            SimpleNamespace(completion_text=ANALYSIS),
        ]
    )


def test_custom_category_creation_is_durable_unique_and_available_to_the_model(
    tmp_path, monkeypatch
):
    plugin, event = prepare()
    monkeypatch.setattr("workshop.history_path", lambda: tmp_path / "workshop.db")

    async def run():
        legacy = [{"id": "legacy", "unknown_field": {"revision": 2}}]
        plugin._test_store[CASES_KEY] = copy.deepcopy(legacy)
        data = {"label": "宠物走失", "description": "寻找走失的宠物"}
        results = await asyncio.gather(
            *(plugin._admin_create_direction(payload=data, actor="admin") for _ in range(2)),
            return_exceptions=True,
        )
        (created,) = [r for r in results if isinstance(r, dict)]
        (duplicate,) = [r for r in results if isinstance(r, ManagementError)]
        assert duplicate.status == 409
        key = created["intent"]
        for bad in ("", "猜物", "失物", "未分类", "a\nb", "a" * 33):
            with pytest.raises(ManagementError):
                await plugin._admin_create_direction(payload={"label": bad}, actor="admin")
        assert set(plugin._test_store[CASES_KEY]["custom_directions"]) == {key}
        assert plugin._test_store[CASES_KEY]["cases"] == legacy

        reloaded = LiuyaoPlugin(plugin.context, plugin.config)
        reloaded.get_kv_data, reloaded.put_kv_data = plugin.get_kv_data, plugin.put_kv_data
        reloaded._render_and_send_chart = plugin._render_and_send_chart
        reloaded._send_reply_payload, reloaded._send_caster_reply = (
            plugin._send_reply_payload,
            plugin._send_caster_reply,
        )
        await reloaded.initialize()
        reloaded.context.llm_generate = replies("宠物走失")
        await reloaded.cast_liuyao_tool(event, question="家里的小猫昨天跑出门了，能找回来吗？")
        pre, final = [c.kwargs["prompt"] for c in reloaded.context.llm_generate.await_args_list]
        assert "宠物走失：寻找走失的宠物" in pre
        assert "意图：宠物走失" in final and "# 六爻分类参考：综合" in final
        assert "参考文档 custom_" not in final
        saved = next(c for c in plugin._test_store[CASES_KEY]["cases"] if c["id"] != "legacy")
        assert (saved["intent"], saved["intent_label"]) == (key, "宠物走失")
        assert "综合研读笔记" in await reloaded.lookup_liuyao_reference_tool(event, "宠物走失")

        old_store = copy.deepcopy(plugin._test_store)
        reloaded.put_kv_data = AsyncMock(side_effect=OSError)
        with pytest.raises(OSError):
            await reloaded._admin_create_direction(payload={"label": "其他新类"}, actor="admin")
        assert "其他新类" not in reloaded.readings.direction_labels().values()
        assert plugin._test_store == old_store

    asyncio.run(run())


def test_legacy_cases_filter_by_category_before_paging_and_can_be_corrected():
    plugin = _make_enabled_plugin()
    cases = [
        {
            "id": f"case-{n}",
            "serial": n,
            "intent_label": "射覆",
            "analysis": "原断",
            "question": "找钥匙",
            "cast": {},
            "feedback": [],
        }
        for n in range(1, 26)
    ]
    cases.extend(
        [{"id": "unknown", "serial": 26}, {"id": "other", "serial": 27, "intent": "career"}]
    )
    plugin._test_store[CASES_KEY] = copy.deepcopy(cases)

    async def run():
        page = await plugin._page_cases(
            query={"intent": "shefu", "page": "2", "state": "pending", "search": "钥匙"}
        )
        assert (page["total"], page["all_count"], page["pages"]) == (25, 27, 2)
        assert [row["serial"] for row in page["items"]] == [5, 4, 3, 2, 1]
        assert {row["intent_label"] for row in page["items"]} == {"射覆"}
        unclassified = await plugin._page_cases(query={"intent": "uncategorized"})
        assert [(r["id"], r["intent_label"]) for r in unclassified["items"]] == [
            ("unknown", "未分类")
        ]
        assert plugin._test_store[CASES_KEY] == cases
        detail = await plugin._page_case("case-1", query={})
        change = {"action": "classify", "revision": detail["revision"]}
        with pytest.raises(ManagementError):
            await plugin._admin_change_case("case-1", {**change, "intent": "unknown"}, "admin")
        await plugin._admin_change_case("case-1", {**change, "intent": "lost_item"}, "admin")
        with pytest.raises(ManagementError) as stale:
            await plugin._admin_change_case("case-1", {**change, "intent": "career"}, "other")
        assert stale.value.status == 409
        after = (await plugin._page_case("case-1", query={}))["case"]
        assert after["intent_correction"]["original_label"] == "射覆"
        assert (after["intent"], after["intent_label"]) == ("lost_item", "寻物")
        for field in ("serial", "analysis", "question", "cast", "feedback"):
            assert after[field] == cases[0][field]
        assert (await plugin._page_cases(query={"intent": "shefu"}))["total"] == 24
        assert (await plugin._page_cases(query={"intent": "lost_item"}))["total"] == 1

    asyncio.run(run())


def test_manually_corrected_case_keeps_its_direction_on_numbered_and_recent_rereads():
    plugin, event = prepare()

    async def run():
        await plugin._manual_command_reply(event, "789678 要找的那件是什么？", analyze=False)
        record = plugin._test_store[CASES_KEY]["cases"][0]
        detail = await plugin._page_case(record["id"], query={})
        await plugin._admin_change_case(
            record["id"],
            {
                "action": "classify",
                "revision": detail["revision"],
                "intent": "lost_item",
            },
            "admin",
        )
        for index in range(2):
            event.message_obj.message_id = str(index + 2)
            plugin.context.llm_generate = replies("射覆")
            if index == 0:
                await plugin._manual_command_reply(event, f"#{record['serial']}", analyze=True)
            else:
                await plugin.reuse_liuyao_tool(event, question="再看一看大致方向")
            assert "意图：寻物" in plugin.context.llm_generate.await_args.kwargs["prompt"]
            assert plugin._render_and_send_chart.await_args.kwargs["intent"] == "lost_item"
            assert plugin._test_store[CASES_KEY]["cases"][0]["intent"] == "lost_item"

    asyncio.run(run())


def test_first_analysis_finishing_after_an_admin_edit_preserves_the_correction():
    plugin, event = prepare()

    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def generate(**kwargs):
            if "只输出合法 JSON" in kwargs["prompt"]:
                return SimpleNamespace(
                    completion_text='{"reject":false,"valid_question":true,"intent":"射覆"}'
                )
            entered.set()
            await finish.wait()
            return SimpleNamespace(completion_text=ANALYSIS)

        plugin.context.llm_generate = generate
        task = asyncio.create_task(plugin._dispatch_liuyao(event, "过去见到的东西是什么？"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            record = plugin._test_store[CASES_KEY]["cases"][0]
            detail = await plugin._page_case(record["id"], query={})
            await plugin._admin_change_case(
                record["id"],
                {"revision": detail["revision"], "action": "classify", "intent": "lost_item"},
                "admin",
            )
        finally:
            finish.set()
            await task
        saved = plugin._test_store[CASES_KEY]["cases"][0]
        assert (saved["intent"], saved["intent_label"], saved["analysis"]) == (
            "lost_item",
            "寻物",
            ANALYSIS,
        )

    asyncio.run(run())
