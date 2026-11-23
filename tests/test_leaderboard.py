from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from PIL import Image
from test_agent_analysis import QUESTION, prepare
from test_command_spacing import command_event, compact
from test_manual_commands import REVIEW
from test_permissions import _make_enabled_plugin
from test_token_usage import response
from test_user_management import legacy_case

from leaderboard import HISTORY_KEY, LeaderboardPageError, group_leaderboard
from renderer import LiuyaoImageRenderer
from token_usage import TOKEN_USAGE_KEY
from user_management import CASES_KEY, USERS_KEY, case_revision


async def board_for(plugin, group="10001", page=1):
    return group_leaderboard(
        group,
        await plugin._leaderboard_history(),
        (await plugin._load_user_registry())["users"],
        await plugin.get_kv_data(CASES_KEY, {}),
        await plugin._token_runs(),
        page,
    )


def totals(board):
    return [section.total for section in board.sections]


def test_new_charts_rereadings_model_failover_and_lookup_take_distinct_paths():
    async def run():
        plugin, event = prepare()
        await plugin._leaderboard_history()
        manual = "排盘 789678 时间=202611150830"
        await plugin._dispatch_liuyao(event, manual)
        await plugin._dispatch_liuyao(event, manual)  # Two real casts at the same manual time.
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        assert totals(await board_for(plugin)) == [3, 1, 0]

        plugin.config["command_interpretation_backup_provider_id"] = "backup"
        event.message_obj.message_id = "reread"
        plugin.context.llm_generate.side_effect = [response(REVIEW), TimeoutError(), response()]
        await plugin._dispatch_liuyao(event, f"解卦 #1 {QUESTION}")
        assert totals(await board_for(plugin)) == [3, 2, 0]

        event.message_obj.message_id = "failed"
        plugin.context.llm_generate.side_effect = [response(REVIEW), TimeoutError(), TimeoutError()]
        plugin._render_and_send_chart.return_value = "生成或发送失败"
        await plugin._dispatch_liuyao(event, QUESTION)
        assert totals(await board_for(plugin)) == [3, 3, 0]

        event.message_obj.message_id = "review-only"
        plugin._render_and_send_chart.return_value = "已发送到当前会话"
        plugin.context.llm_generate.side_effect = [
            response('{"reject":false,"valid_question":false,"intent":"综合"}')
        ]
        await plugin._dispatch_liuyao(event, QUESTION)
        plugin._send_feedback_image = AsyncMock(return_value=True)
        await plugin._dispatch_liuyao(event, "反馈 1")
        await plugin._dispatch_liuyao(event, "额度")
        assert totals(await board_for(plugin)) == [4, 3, 0]

    asyncio.run(run())


def test_feedback_is_global_for_local_users_and_each_case_scores_once():
    async def run():
        plugin = _make_enabled_plugin()
        case = legacy_case() | {"feedback": []}
        local = case | {"id": "case-local", "caster_name": "本群昵称"}
        away = case | {"id": "case-away", "group_id": "2", "caster_name": "外群名片"}
        outsider = away | {"id": "case-other", "caster_id": "33333", "caster_name": "别群群友"}
        plugin._test_store[CASES_KEY] = {"cases": [local, away, outsider]}
        await plugin._leaderboard_history()
        event = command_event("/六爻排行")

        async def feedback(case_id, valid):
            return await plugin._append_case_feedback(
                event,
                case_id=case_id,
                feedback="项目按期完成",
                outcome="应验",
                reviewed_valid=valid,
            )

        assert "反馈未通过" in await feedback("case-local", False)
        assert totals(await board_for(plugin)) == [1, 1, 0]
        assert "已闭环" in await feedback("case-local", True)
        assert "已反馈过" in await feedback("case-local", True)
        event.get_group_id = lambda: "2"
        assert "已闭环" in await feedback("case-away", True)
        event.get_sender_id = lambda: "33333"
        assert "已闭环" in await feedback("case-other", True)
        local_board = await board_for(plugin)
        assert totals(local_board) == [1, 1, 2]
        assert local_board.participants == 1
        assert "本群昵称" in local_board.as_text() and "外群" not in local_board.as_text()
        assert totals(await board_for(plugin, "2")) == [2, 2, 3]

    asyncio.run(run())


def test_new_cast_is_counted_only_after_the_event_accepts_its_png():
    async def run():
        plugin, event = prepare()
        del plugin._render_and_send_chart
        plugin.renderer = LiuyaoImageRenderer(plugin.corpus)
        delivered = []

        async def send(message):
            path = Path(message["image"])
            with Image.open(path) as png:
                png.verify()
            delivered.append(path)
            if len(delivered) == 1:
                raise OSError("QQ image delivery failed")

        event.send = send
        await plugin._dispatch_liuyao(event, "排盘 789678")
        assert totals(await board_for(plugin)) == [0, 0, 0]
        await plugin._dispatch_liuyao(event, "排盘 789678")
        assert totals(await board_for(plugin)) == [1, 0, 0]
        assert len(delivered) == 2 and not any(path.exists() for path in delivered)

    asyncio.run(run())


def test_migration_deduplicates_ledgers_and_preserves_legacy_counts_after_deletion():
    async def run():
        plugin = _make_enabled_plugin()
        old = legacy_case()
        modern = old | {"id": "tracked", "serial": 8, "feedback": []}
        failed = old | {"id": "unsent", "serial": 9, "analysis": "", "feedback": []}
        plugin._test_store[CASES_KEY] = {
            "cases": [old, modern, failed],
            "valid_feedback_events": {"20002": ["a", "b"]},
        }
        common = {"caster_id": "20002", "group_id": "10001", "caster_name": "本群昵称"}
        runs = {
            "new": common | {"kind": "cast", "case_id": "tracked", "cast_key": "case-tracked"},
            "reuse": common | {"kind": "reuse", "case_id": "tracked"},
            "failed-reread": common | {"kind": "reuse", "case_id": old["id"]},
            "nosend": common | {"kind": "cast", "case_id": "unsent", "chart_sent": False},
            "debug": common | {"kind": "debug", "chart_sent": True},
            "test": common | {"kind": "reading_test", "chart_sent": True},
            "review": common | {"kind": "feedback"},
        }
        for key, row in runs.items():
            row.update(id=key, calls=[{"stage": "interpretation", "status": "returned"}])
        runs["nosend"]["calls"][0]["status"] = "not_called"
        runs["failed-reread"]["calls"][0]["status"] = "error"
        plugin._test_store[TOKEN_USAGE_KEY] = {"runs": runs}

        first, second = await asyncio.gather(board_for(plugin), board_for(plugin))
        assert first == second and totals(first) == [2, 4, 2]
        assert plugin._test_store.get(HISTORY_KEY)
        for row in list(plugin._test_store[CASES_KEY]["cases"]):
            await plugin._admin_change_case(
                row["id"], {"action": "delete", "revision": case_revision(row)}, "admin"
            )
        reloaded = _make_enabled_plugin()
        reloaded._test_store.update(copy.deepcopy(plugin._test_store))
        assert totals(await board_for(reloaded)) == [2, 4, 2]
        # A new successful feedback is added to its legacy baseline.
        reloaded._test_store[CASES_KEY]["user_scores"] = {"20002": 1}
        assert totals(await board_for(reloaded)) == [2, 4, 3]

    asyncio.run(run())


def test_pagination_keeps_ties_group_names_and_all_three_independent_orders():
    users = {str(i): {"name": f"群友{i}", "groups": ["A"]} for i in range(1, 8)}
    users["9"] = {"name": "其他群", "groups": ["B"]}
    users["1"] = {"name": "其他群里的名片", "groups": ["A", "B"]}
    history = {"groups": {}, "feedback_offsets": {}}
    payload = {"user_scores": {sender: 1 for sender in users} | {"7": 8, "9": 100}}
    runs = [
        {
            "kind": "cast",
            "group_id": "A",
            "caster_id": "2",
            "caster_name": "本群名片",
            "chart_sent": True,
            "calls": [],
        },
    ]
    first = group_leaderboard("A", history, users, payload, runs)
    second = group_leaderboard("A", history, users, payload, runs, 2)
    assert first.participants == 7 and first.pages == 2
    assert totals(first) == totals(second) == [1, 0, 14]
    assert not second.sections[0].entries and not first.sections[1].entries
    assert first.sections[0].entries[0].name == "本群名片"
    entries = first.sections[2].entries + second.sections[2].entries
    assert [e.rank for e in entries] == [1, 2, 2, 2, 2, 2, 2]
    assert len({e.name for e in entries}) == 7 and entries[0].name == "群友7"
    assert "其他群" not in first.as_text() + second.as_text()
    with pytest.raises(LeaderboardPageError, match="共 2 页"):
        group_leaderboard("A", history, users, payload, runs, 3)


@pytest.mark.parametrize("alias", ["/六爻排行", "/六爻排行榜"])
def test_commands_send_real_png_even_when_empty_and_keep_queries_unranked(alias):
    async def run():
        plugin = _make_enabled_plugin()
        event = command_event(alias)
        delivered = []

        async def send(message):
            path = Path(message["image"])
            with Image.open(path) as png:
                assert png.format == "PNG" and png.width == 900
                png.verify()
            delivered.append(path)

        event.send = send
        assert await compact(plugin, event) == []
        assert event.stop_event.called and len(delivered) == 1 and not delivered[0].exists()
        assert totals(await board_for(plugin)) == [0, 0, 0]
        assert TOKEN_USAGE_KEY not in plugin._test_store
        assert USERS_KEY not in plugin._test_store
        event.send = AsyncMock(side_effect=OSError("delivery failed"))
        fallback = await compact(plugin, event)
        assert (
            "暂无记录" in fallback[0]["text"]
            and not Path(event.send.call_args.args[0]["image"]).exists()
        )
        assert "共 1 页" in await plugin._dispatch_liuyao(event, "排行榜 2")
        assert "用法" in await plugin._dispatch_liuyao(event, "排行 abc")
        event.get_group_id = lambda: ""
        assert "群聊" in await plugin._dispatch_liuyao(event, "排行")
        event.get_group_id = lambda: "disabled"
        assert "尚未开启" in await plugin._dispatch_liuyao(event, "排行榜")

    asyncio.run(run())
