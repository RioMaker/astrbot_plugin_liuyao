from __future__ import annotations

import asyncio
import copy
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_analysis import QUESTION, prepare
from test_agent_tool_access import ACTION_CALLS
from test_ai_daily_quota import Event, Owner, admit
from test_permissions import _Event, _make_enabled_plugin

from user_management import BANNED_MESSAGE, USERS_KEY, ManagementError, case_revision


def give(owner, amount=2, user="u1"):
    owner.store[owner._gift_key(user)] = {"balance": amount, "days": {}}


def test_daily_before_gifts_and_suffix_hides_when_empty():
    async def run():
        owner = Owner({"ai_daily_limit": 1})
        give(owner)
        assert "剩余：1 +2 次" in await owner._ai_quota_status(Event())
        first = await admit(owner, Event())
        assert first.source == "daily"
        assert "剩余：0 +2 次" in await owner._ai_quota_status(Event())
        second = await admit(owner, Event("m2"))
        assert second.source == "gift"
        assert "剩余：0 +1 次" in await owner._ai_quota_status(Event())
        assert (await admit(owner, Event("m2"))).already_counted
        await admit(owner, Event("m3"))
        status = await owner._ai_quota_status(Event())
        assert "剩余：0 次" in status and "剩余：0 +" not in status
        assert (await owner._reserve_ai_daily(Event("m4"))).error

    asyncio.run(run())


def test_gifts_persist_cross_day_and_platform_but_daily_refreshes():
    async def run():
        owner = Owner({"ai_daily_limit": 1})
        give(owner, 3)
        await admit(owner, Event())
        await admit(owner, Event("gift"))
        reloaded = Owner(owner.config, owner.store)
        reloaded.now = owner.now + timedelta(days=1)
        retry = await admit(reloaded, Event("gift"))
        assert retry.source == "gift" and retry.already_counted
        assert (await admit(reloaded, Event("new-day"))).source == "daily"
        assert await reloaded._gift_remaining(Event(platform="qq-2")) == 2
        assert await reloaded._gift_remaining(Event(user="someone-else")) == 0
        assert (await admit(reloaded, Event("another", platform="qq-2"))).source == "daily"
        assert (await admit(reloaded, Event("gift2", platform="qq-2"))).source == "gift"
        assert await reloaded._gift_remaining(Event()) == 1

    asyncio.run(run())


def test_parallel_reservations_do_not_overspend_and_release_refunds():
    async def run():
        owner = Owner({"ai_daily_limit": 1})
        give(owner, 2)
        leases = await asyncio.gather(*(owner._reserve_ai_daily(Event(str(i))) for i in range(12)))
        accepted = [lease for lease in leases if not lease.error]
        assert len(accepted) == 3
        assert [lease.source for lease in accepted].count("gift") == 2
        assert await owner._gift_remaining(Event()) == 0
        await asyncio.gather(*(lease.release() for lease in accepted))
        assert await owner._gift_remaining(Event()) == 2
        assert "剩余：1 +2 次" in await owner._ai_quota_status(Event())

    asyncio.run(run())


def test_same_gift_message_multiple_holds_commit_once():
    async def run():
        owner = Owner()
        give(owner, 1)
        await admit(owner, Event())
        one, two = await asyncio.gather(
            owner._reserve_ai_daily(Event("same")), owner._reserve_ai_daily(Event("same"))
        )
        assert one.source == two.source == "gift"
        await one.release()
        assert await owner._gift_remaining(Event()) == 0
        await two.commit()
        await two.release()
        assert owner.store[two.key]["balance"] == 0

    asyncio.run(run())


@pytest.mark.parametrize("commit", [False, True])
def test_cancelled_gift_write_finishes_safely(commit):
    async def run():
        owner = Owner()
        give(owner, 1)
        await admit(owner, Event())
        lease = await owner._reserve_ai_daily(Event("gift")) if commit else None
        entered, release = asyncio.Event(), asyncio.Event()
        original = owner.put_kv_data

        async def slow(key, value):
            if key == owner._gift_key("u1"):
                entered.set()
                await release.wait()
            await original(key, value)

        owner.put_kv_data = slow
        task = asyncio.create_task(
            lease.commit() if commit else owner._reserve_ai_daily(Event("gift"))
        )
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await owner._gift_remaining(Event()) == (0 if commit else 1)

    asyncio.run(run())


def test_failed_gift_commit_does_not_mutate_balance():
    async def run():
        owner = Owner()
        give(owner, 1)
        await admit(owner, Event())
        lease = await owner._reserve_ai_daily(Event("gift"))
        before = copy.deepcopy(owner.store)
        owner.fail_write = True
        with pytest.raises(RuntimeError):
            await lease.commit()
        assert before == owner.store
        owner.fail_write = False
        await lease.release()
        assert await owner._gift_remaining(Event()) == 1

    asyncio.run(run())


def test_abandoned_gift_reservation_expires_without_double_spending():
    async def run():
        owner = Owner()
        give(owner, 1)
        await admit(owner, Event())
        lease = await owner._reserve_ai_daily(Event("abandoned"))
        reloaded = Owner(owner.config, owner.store)
        reloaded.now = owner.now + timedelta(minutes=16)
        assert await reloaded._gift_remaining(Event()) == 1
        owner.now = reloaded.now
        with pytest.raises(RuntimeError):
            await lease.commit()
        await admit(reloaded, Event("replacement"))
        assert await reloaded._gift_remaining(Event()) == 0

    asyncio.run(run())


def test_real_command_consumes_gift_and_shows_remaining_suffix():
    async def run():
        plugin, event = prepare(limit=1)
        await plugin._dispatch_liuyao(event, QUESTION)
        await plugin._admin_grant(
            "20002", {"amount": 2, "request_id": "integration-grant-id"}, "admin"
        )
        event.message_obj.message_id = "gift-message"
        plugin.context.llm_generate.side_effect = [
            SimpleNamespace(completion_text='{"reject":false,"intent":"事业"}'),
            SimpleNamespace(completion_text="断语：按期完成。"),
        ]
        await plugin._dispatch_liuyao(event, QUESTION)
        progress = plugin._send_caster_reply.call_args.args[3]
        assert "预留 1 次赠送额度" in progress and "额度余量：0 +1 次" in progress
        assert "剩余：0 +1 次" in await plugin._ai_quota_status(event)
        assert plugin.context.llm_generate.await_count == 4

    asyncio.run(run())


@pytest.mark.parametrize("config", [{"ai_daily_limit": 0}, {"ai_quota_whitelist_qq": "u1"}])
def test_unlimited_users_keep_gifts(config):
    async def run():
        owner = Owner(config)
        give(owner)
        for i in range(4):
            await admit(owner, Event(str(i)))
        assert await owner._gift_remaining(Event()) == 2

    asyncio.run(run())


def legacy_case():
    return {
        "id": "LY-old",
        "serial": 7,
        "caster_id": "20002",
        "caster_name": "旧昵称",
        "group_id": "10001",
        "origin": "aiocqhttp:GroupMessage:10001",
        "created_at": "2026-10-28T12:00:00+08:00",
        "updated_at": "old",
        "analysis": "原断",
        "question": "原问题",
        "cast": {},
        "feedback": [{"valid": True, "text": "已完成", "observed_at": "2026-10-29T08:00:00+08:00"}],
    }


async def registered_plugin():
    plugin = _make_enabled_plugin()
    plugin._test_store["liuyao_case_library"] = {
        "cases": [legacy_case()],
        "user_scores": {"20002": 1},
    }
    await plugin._ensure_user_registry()
    return plugin


def test_backfill_once_and_new_casts_record_even_when_library_disabled():
    async def run():
        plugin = await registered_plugin()
        await plugin._ensure_user_registry()
        assert (await plugin._managed_user("20002"))["cast_count"] == 1
        await plugin._record_cast_user(_Event("member"), datetime.now(timezone.utc))
        user = await plugin._managed_user("20002")
        assert user["cast_count"] == 2 and user["name"] == "测试群友"
        plugin, event = prepare(limit=1, library=False)
        await plugin._dispatch_liuyao(event, QUESTION)
        event.message_obj.message_id = "exhausted"
        await plugin._dispatch_liuyao(event, "另一件事情")
        assert plugin.context.llm_generate.await_count == 2
        assert (await plugin._managed_user("20002"))["cast_count"] == 2
        assert "liuyao_case_library" not in plugin._test_store

    asyncio.run(run())


def test_grant_is_idempotent_persistent_and_audited():
    async def run():
        plugin = await registered_plugin()
        grant = {"amount": 5, "note": "活动奖励", "request_id": "1234567890abcdef"}
        await asyncio.gather(*(plugin._admin_grant("20002", grant, "admin") for _ in range(5)))
        detail = await plugin._admin_user_detail("20002")
        assert detail["gift_remaining"] == 5 and len(detail["grants"]) == 1
        assert detail["grants"][0]["actor"] == "admin"
        with pytest.raises(ManagementError, match="另一笔"):
            await plugin._admin_grant("20002", grant | {"amount": 6}, "admin")

    asyncio.run(run())


@pytest.mark.parametrize("amount", [0, -1, True, 1.5, "3", 10001, None])
def test_invalid_grants_do_not_write(amount):
    async def run():
        plugin = await registered_plugin()
        before = copy.deepcopy(plugin._test_store)
        with pytest.raises(ManagementError):
            await plugin._admin_grant("20002", {"amount": amount}, "admin")
        assert before == plugin._test_store

    asyncio.run(run())


async def ban(plugin, banned=True):
    user = await plugin._managed_user("20002")
    await plugin._admin_update_user(
        "20002",
        {"banned": banned, "revision": case_revision(user), "ban_reason": "测试", "note": ""},
        "admin",
    )


@pytest.mark.parametrize(("method", "kwargs"), ACTION_CALLS + [("get_liuyao_help_tool", {})])
def test_ban_blocks_every_ai_tool_even_if_whitelisted_or_tools_disabled(method, kwargs):
    async def run():
        plugin = await registered_plugin()
        plugin.config.update(ai_quota_whitelist_qq="20002", agent_tools_enabled=False)
        await ban(plugin)
        assert BANNED_MESSAGE in await getattr(plugin, method)(_Event("member"), **kwargs)

    asyncio.run(run())


def test_ban_blocks_commands_and_unban_restores_access():
    async def run():
        plugin = await registered_plugin()
        await ban(plugin)
        event = _Event("member", astrbot_admin=True)
        plugin.context = SimpleNamespace(llm_generate=AsyncMock())
        for method, args in [
            ("liuyao_command", ("问事",)),
            ("divination_command", ("六爻 问事",)),
            ("llm_ping_command", ()),
            ("ai_quota_command", ()),
        ]:
            replies = [r async for r in getattr(plugin, method)(event, *args)]
            assert BANNED_MESSAGE in replies[0]["text"]
        for tail in ["", "开", "关", QUESTION]:
            assert await plugin._dispatch_liuyao(event, tail) == BANNED_MESSAGE
        assert await plugin._feedback_reply(event, "7 实际反馈") == BANNED_MESSAGE
        assert (await plugin._reserve_ai_daily(event)).error == BANNED_MESSAGE
        plugin.context.llm_generate.assert_not_awaited()
        await ban(plugin, False)
        assert await plugin._user_access_error(event) == ""
        assert "不限" in await plugin._ai_quota_status(event)

    asyncio.run(run())


def test_ban_hides_plugin_tools_but_preserves_other_plugins():
    async def run():
        plugin = await registered_plugin()
        await ban(plugin)
        tools = SimpleNamespace(
            tools=[SimpleNamespace(name=n) for n in ["cast_liuyao", "get_liuyao_help", "other"]]
        )
        req = SimpleNamespace(func_tool=tools)
        await plugin.configure_liuyao_agent_tools(_Event("member"), req)
        assert [t.name for t in req.func_tool.tools] == ["other"]
        assert len(tools.tools) == 3

    asyncio.run(run())


def test_permission_storage_failure_fails_closed():
    plugin = _make_enabled_plugin()
    plugin.get_kv_data = AsyncMock(side_effect=OSError("storage down"))
    assert "暂不可用" in asyncio.run(plugin._dispatch_liuyao(_Event("member"), ""))


def test_case_note_delete_conflict_and_preserved_rewards_users():
    async def run():
        plugin = await registered_plugin()
        plugin._ai_daily_now = lambda: datetime(
            2026, 10, 29, 12, tzinfo=timezone(timedelta(hours=8))
        )
        plugin.config.update(
            ai_daily_limit=1,
            ai_quota_feedback_rules=[
                {"window_days": 7, "minimum_feedback_count": 1, "daily_bonus": 1}
            ],
        )
        assert await plugin._effective_ai_daily_limit(_Event("member")) == 2
        current = await plugin._page_case("LY-old", query={})
        await plugin._admin_change_case(
            "LY-old", {"action": "note", "note": "待回访", "revision": current["revision"]}, "admin"
        )
        with pytest.raises(ManagementError, match="新反馈或更新"):
            await plugin._admin_change_case(
                "LY-old", {"action": "delete", "revision": current["revision"]}, "admin"
            )
        current = await plugin._page_case("LY-old", query={})
        assert current["case"]["admin_note"] == "待回访" and current["case"]["analysis"] == "原断"
        await plugin._admin_change_case(
            "LY-old", {"action": "delete", "revision": current["revision"]}, "admin"
        )
        assert (await plugin._page_cases(query={}))["total"] == 0
        assert (await plugin._managed_user("20002"))["cast_count"] == 1
        assert await plugin._feedback_score(_Event("member")) == 1
        assert await plugin._effective_ai_daily_limit(_Event("member")) == 2
        assert plugin._test_store["liuyao_case_library"]["next_serial"] == 8

    asyncio.run(run())


def test_page_pagination_search_and_filters():
    async def run():
        plugin = await registered_plugin()
        registry = plugin._test_store[USERS_KEY]
        for i in range(40):
            registry["users"][str(i)] = plugin._new_user(str(i))
        assert len((await plugin._page_users(query={}))["items"]) == 20
        assert len((await plugin._page_users(query={"page": "3"}))["items"]) == 1
        await ban(plugin)
        filtered = await plugin._page_users(query={"state": "banned"})
        assert filtered["total"] == 1 and filtered["items"][0]["id"] == "20002"
        assert (await plugin._page_users(query={"search": "旧昵称"}))["total"] == 1
        assert (await plugin._page_cases(query={"search": "原问题", "state": "feedback"}))[
            "total"
        ] == 1

    asyncio.run(run())


def test_web_routes_require_dashboard_identity_and_validate_body(monkeypatch):
    async def run():
        plugin = await registered_plugin()
        routes = {}

        def register(route, handler, methods, desc):
            routes[route] = (handler, methods)

        plugin.context = SimpleNamespace(register_web_api=register)
        web = ModuleType("astrbot.api.web")
        web.request = SimpleNamespace(username=None, query={}, json=AsyncMock(return_value=[]))
        web.json_response = lambda value: (value, 200)
        web.error_response = lambda message, status_code=400: (message, status_code)
        monkeypatch.setitem(sys.modules, "astrbot.api.web", web)
        plugin._register_admin_page()
        assert len(routes) == 7
        for handler, _methods in routes.values():
            assert (await handler())[1] == 401
        web.request.username = "admin"
        users, methods = routes["/astrbot_plugin_liuyao/manage/users"]
        assert methods == ["GET"] and (await users())[0]["total"] == 1
        grant, methods = routes["/astrbot_plugin_liuyao/manage/users/<sender>/grant"]
        assert methods == ["POST"] and (await grant(sender="20002"))[1] == 400
        web.request.json.return_value = {"amount": 3, "request_id": "api-grant-id-12345"}
        assert (await grant(sender="20002"))[1] == 200
        assert (await plugin._admin_user_detail("20002"))["gift_remaining"] == 3
        await plugin.terminate()
        assert (await grant(sender="20002"))[1] == 503

    asyncio.run(run())
