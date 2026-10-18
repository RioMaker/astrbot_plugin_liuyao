"""Persistent daily admission tests without a running AstrBot or model."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


def load_quota_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "ai_daily_quota.py"
    if not path.exists():  # Standalone staging verification.
        path = Path(__file__).with_name("ai_daily_quota.py")
    spec = importlib.util.spec_from_file_location("daily_quota_under_test", path)
    module = importlib.util.module_from_spec(spec)
    api = types.ModuleType("astrbot.api")
    api.logger = types.SimpleNamespace(error=lambda *_args: None)
    with patch.dict(sys.modules, {"astrbot.api": api, spec.name: module}):
        spec.loader.exec_module(module)
    return module


quota = load_quota_module()


class Event:
    def __init__(self, message="m1", *, user="u1", group="g1", platform="qq-1"):
        self.user, self.group, self.platform = user, group, platform
        self.message_obj = types.SimpleNamespace(message_id=message)
        self.unified_msg_origin = f"{platform}:GroupMessage:{group}"
        self.extra = {}

    def get_sender_id(self):
        return self.user

    def get_group_id(self):
        return self.group

    def get_platform_id(self):
        return self.platform

    def get_extra(self, key, default=None):
        return self.extra.get(key, default)

    def set_extra(self, key, value):
        self.extra[key] = value


class Owner(quota.DailyAIQuotaMixin):
    def __init__(self, config=None, store=None):
        self.config = config or {}
        self.store = {} if store is None else store
        self.now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        self.fail_read = False
        self.fail_write = False

    def _ai_daily_now(self):
        return self.now

    async def get_kv_data(self, key, default):
        if self.fail_read:
            raise OSError("read failed")
        return self.store.get(key, default)

    async def put_kv_data(self, key, value):
        if self.fail_write:
            raise OSError("write failed")
        self.store[key] = copy.deepcopy(value)


async def admit(owner, event):
    lease = await owner._reserve_ai_daily(event)
    assert not lease.error
    await lease.commit()
    return lease


def test_default_persists_across_reload_and_configuration_changes():
    async def scenario():
        owner = Owner()
        await admit(owner, Event())
        reloaded = Owner(store=owner.store)
        assert (await reloaded._reserve_ai_daily(Event("m2"))).error
        reloaded.config["ai_daily_limit"] = 2
        await admit(reloaded, Event("m2"))
        reloaded.config["ai_daily_limit"] = 1
        assert (await reloaded._reserve_ai_daily(Event("m3"))).error
        duplicate = await admit(reloaded, Event("m1"))
        assert duplicate.already_counted

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "scope", [{"group": "g2"}, {"user": "u2"}, {"platform": "qq-2"}, {"group": ""}]
)
def test_scope_isolation(scope):
    async def scenario():
        owner = Owner()
        await admit(owner, Event())
        await admit(owner, Event(**scope))
        assert (await owner._reserve_ai_daily(Event("m2"))).error

    asyncio.run(scenario())


@pytest.mark.parametrize("identifier", ["g1", "qq-1:GroupMessage:g1"])
def test_group_override_replaces_global_default(identifier):
    async def scenario():
        owner = Owner(
            {
                "ai_daily_limit": 1,
                "ai_daily_group_limits": [{"group_id": identifier, "limit": 2}],
            }
        )
        await admit(owner, Event("m1"))
        await admit(owner, Event("m2"))
        assert (await owner._reserve_ai_daily(Event("m3"))).error
        await admit(owner, Event("m1", group="g2"))
        assert (await owner._reserve_ai_daily(Event("m2", group="g2"))).error

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [None, -1, True, "oops", 1.5, 10001])
def test_invalid_global_limit_uses_default_one(value):
    async def scenario():
        owner = Owner({"ai_daily_limit": value})
        await admit(owner, Event())
        assert (await owner._reserve_ai_daily(Event("m2"))).error

    asyncio.run(scenario())


def test_zero_disables_limit_and_invalid_overrides_are_skipped():
    async def scenario():
        owner = Owner(
            {
                "ai_daily_group_limits": [
                    None,
                    {"group_id": "g1", "limit": -1},
                    {"group_id": "g1", "limit": 0},
                ]
            }
        )
        for number in range(4):
            await admit(owner, Event(str(number)))
        await admit(owner, Event("m1", group="g2"))
        assert (await owner._reserve_ai_daily(Event("m2", group="g2"))).error

    asyncio.run(scenario())


def test_midnight_uses_beijing_day_and_retries_keep_original_day():
    async def scenario():
        owner = Owner()
        owner.now = datetime(2026, 10, 17, 15, 59, 59, tzinfo=timezone.utc)
        first = await admit(owner, Event())
        assert first.day == "2026-10-17"
        owner.now = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
        duplicate = await admit(owner, Event())
        assert duplicate.already_counted and duplicate.day == "2026-10-17"
        second = await admit(owner, Event("m2"))
        assert second.day == "2026-10-18"
        assert (await owner._reserve_ai_daily(Event("m3"))).error

    asyncio.run(scenario())


def test_pending_work_can_finish_across_midnight():
    async def scenario():
        owner = Owner()
        pending = await owner._reserve_ai_daily(Event())
        owner.now = datetime(2026, 10, 17, 16, tzinfo=timezone.utc)
        await admit(owner, Event("m2"))
        await pending.commit()
        assert (await owner._reserve_ai_daily(Event())).already_counted
        assert (await owner._reserve_ai_daily(Event("m3"))).error

    asyncio.run(scenario())


def test_old_days_pruned_without_losing_current_quota():
    async def scenario():
        owner = Owner()
        old = await admit(owner, Event())
        owner.now = datetime(2026, 10, 20, tzinfo=timezone.utc)
        await admit(owner, Event("m2"))
        assert set(owner.store[old.key]["days"]) == {"2026-10-20"}

    asyncio.run(scenario())


def test_message_dedup_and_no_id_event_identity():
    async def scenario():
        owner = Owner()
        await admit(owner, Event())
        assert (await admit(owner, Event())).already_counted
        assert (await owner._reserve_ai_daily(Event("other"))).error
        owner = Owner()
        event = Event(None)
        await admit(owner, event)
        assert (await admit(owner, event)).already_counted
        assert (await owner._reserve_ai_daily(Event(None))).error

    asyncio.run(scenario())


def test_parallel_requests_cannot_exceed_limit():
    async def scenario():
        owner = Owner()
        leases = await asyncio.gather(*(owner._reserve_ai_daily(Event(str(i))) for i in range(12)))
        admitted = [lease for lease in leases if not lease.error]
        assert len(admitted) == 1
        await admitted[0].commit()
        assert (await owner._reserve_ai_daily(Event("next"))).error

    asyncio.run(scenario())


def test_same_message_parallel_holds_release_and_commit_once():
    async def scenario():
        owner = Owner()
        first, second = await asyncio.gather(
            owner._reserve_ai_daily(Event()), owner._reserve_ai_daily(Event())
        )
        assert not first.error and not second.error
        await first.release()
        assert (await owner._reserve_ai_daily(Event("next"))).error
        await second.commit()
        await second.release()  # A completed AI request is not refunded.
        entry = owner.store[first.key]["days"][first.day][first.token]
        assert entry == {"used": True, "holds": []}

    asyncio.run(scenario())


def test_failed_preparation_releases_capacity():
    async def scenario():
        owner = Owner()
        lease = await owner._reserve_ai_daily(Event())
        await lease.release()
        await lease.release()
        await admit(owner, Event("next"))

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["read", "write", "corrupt"])
def test_storage_problems_fail_closed(failure):
    async def scenario():
        owner = Owner()
        if failure == "read":
            owner.fail_read = True
        elif failure == "write":
            owner.fail_write = True
        else:
            key, _ = owner._daily_identity(Event())
            owner.store[key] = {"days": {"2026-10-17": {"bad": {"used": False}}}}
        lease = await owner._reserve_ai_daily(Event())
        assert lease.error == quota.STORAGE_ERROR

    asyncio.run(scenario())


def test_commit_failure_keeps_persistent_pending_reservation():
    async def scenario():
        owner = Owner()
        lease = await owner._reserve_ai_daily(Event())
        before = copy.deepcopy(owner.store)
        owner.fail_write = True
        with pytest.raises(quota.AIQuotaStorageError):
            await lease.commit()
        assert owner.store == before
        owner.fail_write = False
        await lease.release()
        await admit(owner, Event("next"))

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_count", [1, 2, 3])
def test_reservation_cancelled_during_write_releases_capacity(cancel_count):
    async def scenario():
        owner = Owner()
        started, resume = asyncio.Event(), asyncio.Event()
        original = owner.put_kv_data

        async def slow_write(key, value):
            await original(key, value)
            started.set()
            await resume.wait()

        owner.put_kv_data = slow_write
        task = asyncio.create_task(owner._reserve_ai_daily(Event()))
        await started.wait()
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        owner.put_kv_data = original
        await admit(owner, Event("next"))

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_count", [1, 2, 3])
def test_commit_cancelled_during_write_finishes_and_cannot_refund(cancel_count):
    async def scenario():
        owner = Owner()
        lease = await owner._reserve_ai_daily(Event())
        started, resume = asyncio.Event(), asyncio.Event()
        original = owner.put_kv_data

        async def slow_write(key, value):
            await original(key, value)
            started.set()
            await resume.wait()

        owner.put_kv_data = slow_write
        task = asyncio.create_task(lease.commit())
        await started.wait()
        for _ in range(cancel_count):
            task.cancel()
            await asyncio.sleep(0)
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await lease.release()
        owner.put_kv_data = original
        assert (await owner._reserve_ai_daily(Event("next"))).error

    asyncio.run(scenario())
