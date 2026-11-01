"""Persistent, provider-reported token accounting, independent of case retention."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
from datetime import date, datetime, timedelta
from time import perf_counter
from uuid import uuid4

from astrbot.api import logger

if __package__:
    from .ai_daily_quota import BEIJING, _wait_persistent_task
    from .user_management import ManagementError, require_text
else:
    from ai_daily_quota import BEIJING, _wait_persistent_task
    from user_management import ManagementError, require_text


TOKEN_USAGE_KEY = "liuyao_token_usage_v1"
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_tokens", "total_tokens")


def _get(value, key):
    return value.get(key) if isinstance(value, dict) else getattr(value, key, None)


def _number(value):
    return value if type(value) is int and value >= 0 else None


def _first(value, *keys):
    return next((n for key in keys if (n := _number(_get(value, key))) is not None), None)


def extract_usage(response) -> dict | None:
    """Prefer AstrBot TokenUsage; never estimate missing tokens from text length."""
    raw = _get(response, "raw_completion")
    for usage in (
        _get(response, "usage"),
        _get(raw, "usage"),
        _get(raw, "usage_metadata"),
    ):
        if usage is None:
            continue
        other = _first(usage, "input_other")
        if other is not None:
            cached = _first(usage, "input_cached")
            incoming = other + (cached or 0)
            outgoing = _first(usage, "output")
            total = incoming + outgoing if outgoing is not None else None
        else:
            incoming = _first(usage, "prompt_tokens", "input_tokens", "prompt_token_count")
            outgoing = _first(usage, "completion_tokens", "output_tokens", "candidates_token_count")
            total = _first(usage, "total_tokens", "total_token_count")
            cached = _first(
                usage, "input_cached", "cache_read_input_tokens", "cached_content_token_count"
            )
            for key in ("prompt_tokens_details", "input_tokens_details"):
                if cached is None:
                    cached = _first(_get(usage, key), "cached_tokens")
            # Anthropic's input_tokens excludes cache reads and cache creation.
            if incoming is not None and (
                _get(usage, "cache_read_input_tokens") is not None
                or _get(usage, "cache_creation_input_tokens") is not None
            ):
                incoming += (cached or 0) + (_first(usage, "cache_creation_input_tokens") or 0)
            # Gemini's candidate count excludes thinking tokens; total already includes them.
            if outgoing is not None and _get(usage, "candidates_token_count") is not None:
                outgoing += _first(usage, "thoughts_token_count") or 0
            if total is None and incoming is not None and outgoing is not None:
                total = incoming + outgoing
        if total is not None or incoming is not None or outgoing is not None:
            return dict(zip(TOKEN_FIELDS, (incoming, outgoing, cached, total), strict=True))
    return None


def cast_usage_key(*, sender, group, cast_at, lines) -> str:
    try:
        stamp = datetime.fromisoformat(str(cast_at))
        stamp = stamp.replace(tzinfo=BEIJING) if stamp.tzinfo is None else stamp.astimezone(BEIJING)
        stamp = stamp.isoformat()
    except (ValueError, TypeError):
        stamp = str(cast_at)
    identity = json.dumps([str(sender), str(group), stamp, list(lines)], ensure_ascii=False)
    return "cast-" + hashlib.sha256(identity.encode()).hexdigest()[:32]


def case_usage_key(case) -> str:
    return cast_usage_key(
        sender=case.get("caster_id", ""),
        group=case.get("group_id", ""),
        cast_at=case.get("created_at", ""),
        lines=case.get("cast", {}).get("lines", []),
    )


def empty_totals() -> dict:
    return {
        **dict.fromkeys(TOKEN_FIELDS, 0),
        "calls": 0,
        "reported_calls": 0,
        "unknown_calls": 0,
        "input_unknown_calls": 0,
        "output_unknown_calls": 0,
    }


def add_call(totals, call):
    usage = call.get("usage") or {}
    totals["calls"] += 1
    known = usage.get("total_tokens") is not None
    totals["reported_calls" if known else "unknown_calls"] += 1
    for field in TOKEN_FIELDS:
        totals[field] += usage.get(field) or 0
    totals["input_unknown_calls"] += usage.get("input_tokens") is None
    totals["output_unknown_calls"] += usage.get("output_tokens") is None


def summarize(runs) -> dict:
    result = empty_totals()
    for run in runs:
        for call in run["calls"]:
            add_call(result, call)
    result["casts"] = len({run["cast_key"] for run in runs if run.get("cast_key")})
    result["tracked"] = bool(runs)
    result["historical"] = bool(runs) and not any(
        run.get("kind") in {"cast", "debug"} for run in runs
    )
    return result


def period_start(day: date, view: str) -> date:
    if view == "weekly":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1) if view == "monthly" else day


def filter_runs(runs, *, start="", end="", group="", sender=""):
    result = []
    for run in runs:
        if (group and run["group_id"] != group) or (sender and run["caster_id"] != sender):
            continue
        calls = [call for call in run["calls"] if in_range(call["at"], start, end)]
        if calls or (not run["calls"] and in_range(run["created_at"], start, end)):
            result.append({**run, "calls": calls})
    return result


def in_range(timestamp, start, end):
    day = timestamp[:10]
    return (not start or start <= day) and (not end or day <= end)


def overview(runs, now):
    day = now.astimezone(BEIJING).date()
    return {
        "all": summarize(runs),
        **{
            label: summarize(
                filter_runs(runs, start=period_start(day, view).isoformat(), end=day.isoformat())
            )
            for label, view in (("today", "daily"), ("week", "weekly"), ("month", "monthly"))
        },
    }


def usage_rows(runs, view):
    groups = {}
    for run in runs:
        if view in {"daily", "weekly", "monthly"}:
            for call in run["calls"]:
                key = period_start(date.fromisoformat(call["at"][:10]), view).isoformat()
                groups.setdefault(key, []).append({**run, "calls": [call]})
            if not run["calls"]:
                key = period_start(date.fromisoformat(run["created_at"][:10]), view).isoformat()
                groups.setdefault(key, []).append(run)
        else:
            key = run["group_id"] if view == "groups" else run["caster_id"]
            if view == "cases":
                key = run.get("cast_key") or run["id"]
            groups.setdefault(key, []).append(run)
    rows = []
    for key, members in groups.items():
        first = min(members, key=lambda run: run["created_at"])
        latest = max(members, key=lambda run: run["created_at"])
        archived = next((run for run in members if run.get("case_id")), {})
        rows.append(
            {
                "id": key,
                "caster_id": first["caster_id"],
                "caster_name": latest["caster_name"],
                "group_id": first["group_id"],
                "question": first["question"],
                "created_at": first.get("cast_at") or first["created_at"],
                "last_at": max(
                    [c["at"] for r in members for c in r["calls"]] or [latest["created_at"]]
                ),
                "case_id": archived.get("case_id", ""),
                "serial": archived.get("serial", 0),
                "kind": first["kind"],
                "usage": summarize(members),
            }
        )
    if view in {"daily", "weekly", "monthly"}:
        rows.sort(key=lambda row: row["id"], reverse=True)
    elif view == "cases":
        rows.sort(key=lambda row: row["last_at"], reverse=True)
    else:
        rows.sort(key=lambda row: (row["usage"]["total_tokens"], row["last_at"]), reverse=True)
    return rows


class TokenUsageMixin:
    async def _load_token_ledger(self):
        raw = await self.get_kv_data(TOKEN_USAGE_KEY, {})
        if not isinstance(raw, dict) or not isinstance(raw.get("runs", {}), dict):
            raise ValueError("invalid token ledger")
        return copy.deepcopy(raw)

    async def _token_write(self, change):
        async def write():
            lock = getattr(self, "_token_usage_lock", None)
            if lock is None:
                lock = self._token_usage_lock = asyncio.Lock()
            async with lock:
                ledger = await self._load_token_ledger()
                ledger.setdefault("version", 1)
                ledger.setdefault("started_at", self._ai_daily_now().isoformat())
                ledger.setdefault("runs", {})
                change(ledger["runs"])
                await self.put_kv_data(TOKEN_USAGE_KEY, ledger)

        _, cancelled = await _wait_persistent_task(asyncio.create_task(write()))
        if cancelled:
            raise asyncio.CancelledError

    def _new_token_run(self, event, *, kind, question="", cast=None, case=None):
        name, sender = self._caster_identity(event)
        run = {
            "id": "run-" + uuid4().hex,
            "kind": kind,
            "created_at": self._ai_daily_now().astimezone(BEIJING).isoformat(),
            "caster_id": str(sender),
            "caster_name": name[:100],
            "group_id": str(event.get_group_id() or ""),
            "question": question[:200],
            "cast_key": "",
            "cast_at": "",
            "calls": [],
        }
        if cast is not None and cast.cast_at is not None:
            self._set_token_cast(run, cast)
        if case is not None:
            run.update(
                cast_key=case_usage_key(case),
                case_id=case["id"],
                serial=case.get("serial", 0),
                cast_at=case["created_at"],
            )
        return run

    @staticmethod
    def _set_token_cast(run, cast):
        run["cast_at"] = cast.cast_at.isoformat()
        run["cast_key"] = cast_usage_key(
            sender=run["caster_id"],
            group=run["group_id"],
            cast_at=run["cast_at"],
            lines=cast.lines,
        )
        if run.get("kind") == "debug":
            run["cast_key"] = run["id"]

    async def _bind_token_cast(self, run, cast):
        self._set_token_cast(run, cast)

        def change(runs):
            saved = runs.setdefault(run["id"], copy.deepcopy(run))
            saved.update(cast_key=run["cast_key"], cast_at=run["cast_at"])

        await self._try_token_write(change)

    async def _link_token_case(self, case):
        key = case_usage_key(case)

        def change(runs):
            for run in runs.values():
                if run["cast_key"] == key:
                    run.update(case_id=case["id"], serial=case["serial"])

        await self._try_token_write(change)

    async def _try_token_write(self, change):
        try:
            await self._token_write(change)
        except Exception as exc:
            self._token_storage_warning = True
            logger.warning("liuyao：Token 用量保存失败：%s", type(exc).__name__)

    async def _token_llm(self, run, stage, *, trace=None, **kwargs):
        call = {
            "id": uuid4().hex,
            "stage": stage,
            "provider": str(kwargs.get("chat_provider_id") or ""),
            "at": self._ai_daily_now().astimezone(BEIJING).isoformat(),
            "status": "pending",
            "usage": None,
        }

        def start(runs):
            runs.setdefault(run["id"], copy.deepcopy(run))["calls"].append(copy.deepcopy(call))

        # Persist intent first: a timeout, cancellation or restart must not look like zero cost.
        await self._token_write(start)
        if trace is not None:
            trace.calls.append(call)
        started = perf_counter()
        try:
            response = await self.context.llm_generate(**kwargs)
        except BaseException as exc:
            call["status"] = "cancelled" if isinstance(exc, asyncio.CancelledError) else "error"
            raise
        else:
            call["usage"] = extract_usage(response)
            call["status"] = "error" if _get(response, "role") == "err" else "returned"
            return response
        finally:
            call["elapsed_ms"] = round((perf_counter() - started) * 1000, 2)

            def finish(runs):
                calls = runs[run["id"]]["calls"]
                for index, saved in enumerate(calls):
                    if saved["id"] == call["id"]:
                        calls[index] = copy.deepcopy(call)
                        break

            await self._try_token_write(finish)

    async def _token_runs(self):
        return list((await self._load_token_ledger()).get("runs", {}).values())

    async def _page_usage(self, *, query):
        search, page = self._page_options(query)
        view = query.get("view", "cases")
        if view not in {"cases", "groups", "users", "daily", "weekly", "monthly"}:
            raise ManagementError("未知统计维度")
        dates = {}
        for key in ("start", "end"):
            value = require_text(query.get(key, ""), "日期", 10, empty=True)
            if value:
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ManagementError("日期必须为有效的 YYYY-MM-DD") from exc
            dates[key] = value
        if dates["start"] and dates["end"] and dates["start"] > dates["end"]:
            raise ManagementError("开始日期不能晚于结束日期")
        group = require_text(query.get("group", ""), "群号", 128, empty=True)
        sender = require_text(query.get("sender", ""), "QQ 号", 128, empty=True)
        ledger = await self._load_token_ledger()
        runs = filter_runs(list(ledger.get("runs", {}).values()), group=group, sender=sender)
        selected = filter_runs(runs, **dates)
        rows = usage_rows(selected, view)
        if search:
            fields = {
                "cases": (
                    "id",
                    "serial",
                    "case_id",
                    "caster_id",
                    "caster_name",
                    "group_id",
                    "question",
                ),
                "users": ("id", "caster_name"),
            }.get(view, ("id",))
            rows = [row for row in rows if search in " ".join(str(row[k]) for k in fields).lower()]
        result = self._page_result(rows, page)
        result.update(
            summary=summarize(selected),
            overview=overview(runs, self._ai_daily_now()),
            started_at=ledger.get("started_at", ""),
            timezone="Asia/Shanghai",
            storage_warning=bool(getattr(self, "_token_storage_warning", False)),
        )
        return result

    async def _page_usage_detail(self, usage_id, *, query):
        usage_id = require_text(usage_id, "用量编号", 100)
        runs = [r for r in await self._token_runs() if (r.get("cast_key") or r["id"]) == usage_id]
        if not runs:
            raise ManagementError("尚无该卦用量记录", 404)
        calls = sorted((c for r in runs for c in r["calls"]), key=lambda c: c["at"], reverse=True)
        stages = {}
        for call in calls:
            stage = call["stage"]
            add_call(stages.setdefault(stage, empty_totals()), call)
        return {
            "record": usage_rows(runs, "cases")[0],
            "calls": calls[:100],
            "call_count": len(calls),
            "stages": stages,
        }
