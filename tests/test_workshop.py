from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_feedback_review import case, decision
from test_permissions import ROOT

from divination import CastResult
from feedback_review import REVIEW_GUARDRAIL
from main import PLUGIN_VERSION, LiuyaoPlugin
from prompts import INTERPRETATION_GUARDRAIL, INTERPRETATION_PROMPT, PRE_REVIEW_PROTOCOL
from scripts.build_workshop_tab import TARGET, render_bundle
from user_management import CASES_KEY, ManagementError

PROMPT = "command_interpretation_prompt"
GENERAL = "interpretation_general_rules"
ORIGINAL = "用合同作为判断依据。"
BETTER = "区分口头意向与已签合同，并核对验收节点。"


class Config(dict):
    saves = 0

    def save_config(self):
        self.saved = self.copy()
        self.saves += 1


def response(reply="先对照实际反馈。", changes=()):
    return SimpleNamespace(
        completion_text=json.dumps({"reply": reply, "changes": list(changes)}, ensure_ascii=False)
    )


def edit(section=GENERAL, before=ORIGINAL, after=BETTER):
    return {"section": section, "before": before, "after": after, "reason": "原断混淆了不同进展。"}


@pytest.fixture
def setup_workshop(tmp_path, monkeypatch):
    # Construct the actual Star to exercise config defaults and ReadingService wiring.
    base, event = prepare()
    monkeypatch.setattr("workshop.history_path", lambda: tmp_path / "workshop.db")
    cfg = Config({**base.config, GENERAL: "观察{intent_label}。\n" + ORIGINAL})
    ctx = base.context
    ctx.get_all_providers = lambda: [
        SimpleNamespace(meta=lambda: SimpleNamespace(id="m", model="M"))
    ]
    plugin = LiuyaoPlugin(ctx, cfg)
    plugin.get_kv_data, plugin.put_kv_data = base.get_kv_data, base.put_kv_data
    plugin._test_store = base._test_store
    plugin._render_and_send_chart = base._render_and_send_chart
    plugin._send_reply_payload = base._send_reply_payload
    plugin._send_caster_reply = base._send_caster_reply
    return plugin, event


def chat_payload(state, **kwargs):
    return {
        "message": "口头承诺应该怎样判断？",
        "provider": "m",
        "focus": GENERAL,
        "revision": state["revision"],
        "use_cases": False,
        "serials": [],
    } | kwargs


async def state(plugin, actor="alice"):
    return await plugin._page_workshop(query={}, actor=actor)


async def chat(plugin, model_reply, actor="alice", **kwargs):
    plugin.context.llm_generate = AsyncMock(return_value=model_reply)
    return await plugin._admin_workshop_chat(
        payload=chat_payload(await state(plugin, actor), **kwargs),
        actor=actor,
    )


async def decide(plugin, change, action, actor="alice", **extra):
    return await plugin._admin_workshop_decide(
        payload={"id": change["id"], "action": action, **extra},
        actor=actor,
    )


def test_review_only_selected_change_reaches_next_command_and_survives_reload(setup_workshop):
    plugin, event = setup_workshop

    async def run():
        previous = plugin.config.copy()
        saves = plugin.config.saves
        result = await chat(
            plugin,
            response(
                changes=[
                    edit(),
                    edit(PROMPT, "第一段", "先看结论"),
                ]
            ),
        )
        turn = result["turns"][-1]
        rule, text = turn["changes"]
        assert (rule["before"], rule["after"], rule["status"]) == (ORIGINAL, BETTER, "pending")
        assert result["sections"][GENERAL]["text"] == previous[GENERAL]
        assert plugin.config == previous and plugin.config.saves == saves
        assert (await state(plugin, "bob"))["turns"] == []
        with pytest.raises(ManagementError) as denied:
            await decide(plugin, rule, "approve", "bob")
        assert denied.value.status == 404
        with pytest.raises(ManagementError):
            await decide(plugin, rule, "approve", after="替换成任意内容")
        await decide(plugin, text, "dismiss")
        applied = await decide(plugin, rule, "approve")
        await decide(plugin, rule, "approve")
        assert plugin.config.saves == saves + 1
        assert applied["sections"][GENERAL]["text"] == previous[GENERAL].replace(ORIGINAL, BETTER)
        assert applied["sections"][PROMPT]["text"] == INTERPRETATION_PROMPT
        assert [c["status"] for c in applied["turns"][0]["changes"]] == ["approved", "dismissed"]
        plugin.context.llm_generate.assert_awaited_once()
        with pytest.raises(ManagementError):
            await decide(plugin, text, "approve")

        plugin.context.llm_generate = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    completion_text='{"reject":false,"valid_question":true,"intent":"事业"}'
                ),
                SimpleNamespace(completion_text=ANALYSIS),
            ]
        )
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        final_prompt = plugin.context.llm_generate.await_args_list[-1].kwargs["prompt"]
        assert BETTER in final_prompt and "观察事业。" in final_prompt
        assert ORIGINAL not in final_prompt and final_prompt.endswith(INTERPRETATION_GUARDRAIL)

        reloaded = LiuyaoPlugin(plugin.context, Config(plugin.config.saved))
        again = await state(reloaded)
        assert again["sections"][GENERAL]["text"] == applied["sections"][GENERAL]["text"]
        assert again["turns"] == applied["turns"]
        assert reloaded.readings.render(
            CastResult((7, 8, 7, 8, 7, 8)),
            intent="weather",
            question="",
            method="测试",
            for_agent=True,
        ).endswith(again["sections"]["interpretation_weather_rules"]["text"])

    asyncio.run(run())


def test_case_sources_followup_and_conversation_only(setup_workshop):
    plugin, _ = setup_workshop
    cases = [
        {
            "id": "old",
            "question": "能签约吗",
            "analysis": "能",
            "caster_id": "private-sender",
            "feedback": [{"text": "只口头答应", "reporter_id": "private-reporter"}],
        },
        {
            "id": "open",
            "serial": 4,
            "question": "另一个项目",
            "analysis": "待定",
            "feedback": [{"text": "无关文本", "valid": False}],
        },
    ]
    plugin._test_store[CASES_KEY] = cases
    original = copy.deepcopy(cases)

    async def run():
        first = await chat(plugin, response("标记未经验证的推测。"), use_cases=True)
        material = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
        assert [c["question"] for c in material["本轮卦例"]] == ["能签约吗"]
        example = first["turns"][-1]["cases"][0]
        assert example["serial"] == 5
        assert example["reading"] == "能" and example["feedback"] == ["只口头答应"]
        assert "private-" not in str(material)
        anchored = await chat(plugin, response(), use_cases=True, serials=[4])
        assert anchored["turns"][-1]["cases"][0]["feedback"] == []
        with pytest.raises(ManagementError, match="找不到卦例"):
            await chat(plugin, response(), use_cases=True, serials=[4, 999])
        plugin.context.llm_generate.assert_not_awaited()
        plugin.get_kv_data = AsyncMock(wraps=plugin.get_kv_data)
        result = await chat(plugin, response(), message="接着上一轮，列一个反例。")
        material = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
        assert material["本轮卦例"] == []
        assert material["先前对话"][0]["reply"] == "标记未经验证的推测。"
        assert len(result["turns"]) == 3
        assert plugin._test_store[CASES_KEY] == original
        assert all(call.args[0] != CASES_KEY for call in plugin.get_kv_data.await_args_list)
        runs = await plugin._token_runs()
        assert len(runs) == 3 and all(r["kind"] == "workshop" for r in runs)
        assert all(not r["cast_key"] and not r["question"] for r in runs)
        assert "口头承诺" not in str(runs)
        assert not (await plugin._page_usage(query={"view": "users"}))["items"]
        assert (await plugin._page_usage(query={}))["summary"]["calls"] == 3

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        edit("guardrail"),
        edit("reference_career"),
        edit("pre_review_protocol"),
        edit(before="不存在的原文"),
        edit(PROMPT, "{chart}", "没有排盘"),
        edit(after="{unknown}"),
        edit(after=ORIGINAL),
        edit(before="。"),
    ],
)
def test_invalid_model_changes_never_create_reviewable_edits(setup_workshop, change):
    plugin, _ = setup_workshop

    async def run():
        previous = plugin.config.copy()
        with pytest.raises(ManagementError) as error:
            await chat(plugin, response(changes=[change]))
        assert error.value.status == 502
        assert (await state(plugin))["turns"] == []
        assert plugin.config == previous

    asyncio.run(run())


def test_stale_concurrent_review_and_save_failure(setup_workshop, monkeypatch):
    plugin, _ = setup_workshop

    async def run():
        owner = await chat(plugin, response(changes=[edit()]))
        other = await chat(plugin, response(changes=[edit(after="另一项建议。")]), "bob")
        first, second = owner["turns"][0]["changes"][0], other["turns"][0]["changes"][0]
        previous = plugin.config.copy()
        with monkeypatch.context() as patch:

            def failed():
                raise OSError("disk")

            patch.setattr(plugin.config, "save_config", failed)
            with pytest.raises(ManagementError, match="保存失败"):
                await decide(plugin, first, "approve")
            assert plugin.config == previous
            assert (await state(plugin))["turns"][0]["changes"][0]["status"] == "pending"
        await decide(plugin, first, "approve")
        with pytest.raises(ManagementError) as error:
            await decide(plugin, second, "approve", "bob")
        assert error.value.status == 409
        assert (await state(plugin, "bob"))["turns"][0]["changes"][0]["status"] == "stale"
        assert BETTER in plugin.config[GENERAL]

        fresh = await chat(plugin, response(changes=[edit(before=BETTER, after="新建议。")]))
        later = fresh["turns"][-1]["changes"][0]
        await plugin._admin_workshop_reset(actor="alice", payload={"revision": fresh["revision"]})
        with pytest.raises(ManagementError) as missing:
            await decide(plugin, later, "approve")
        assert missing.value.status == 404
        assert (await state(plugin))["turns"] == [] and BETTER in plugin.config[GENERAL]

    asyncio.run(run())


def test_model_in_flight_uses_original_section_and_never_applies_changes(setup_workshop):
    plugin, _ = setup_workshop

    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()

        async def generate(**kwargs):
            entered.set()
            await finish.wait()
            return response(changes=[edit()])

        plugin.context.llm_generate = generate
        body = chat_payload(await state(plugin))
        task = asyncio.create_task(plugin._admin_workshop_chat(actor="alice", payload=body))
        await entered.wait()
        with pytest.raises(ManagementError, match="正在处理"):
            await plugin._admin_workshop_chat(actor="alice", payload=body)
        plugin.config[GENERAL] = "管理员在另一处保存的新规则。"
        finish.set()
        result = await task
        assert result["turns"][0]["changes"][0]["status"] == "stale"
        assert result["sections"][GENERAL]["text"] == plugin.config[GENERAL]

    asyncio.run(run())


@pytest.mark.parametrize(
    ("section", "original", "direction"),
    [
        (PROMPT, "第一段", "事业"),
        ("interpretation_weather_rules", "先核对地点", "天气"),
        ("interpretation_shefu_rules", "先逐项给出物品画像", "射覆"),
    ],
)
def test_each_approved_template_reaches_its_real_interpretation(
    setup_workshop, section, original, direction
):
    plugin, event = setup_workshop

    async def run():
        updated = "这里采用复盘后确认的判据"
        raw = edit(section, original, updated) | {"status": "approved"}
        draft = (await chat(plugin, response(changes=[raw])))["turns"][0]["changes"][0]
        assert draft["status"] == "pending"
        discussion = await chat(
            plugin,
            SimpleNamespace(completion_text="已收到你的意见。"),
            message="确认，直接写入吧。",
        )
        assert discussion["turns"][-1]["changes"] == [] and updated not in plugin.config[section]
        assert discussion["turns"][0]["changes"][0]["status"] == "pending"
        await decide(plugin, draft, "approve")
        plugin.context.llm_generate = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    completion_text=json.dumps(
                        {
                            "reject": False,
                            "valid_question": True,
                            "intent": direction,
                        }
                    )
                ),
                SimpleNamespace(completion_text=ANALYSIS),
            ]
        )
        await plugin.cast_liuyao_tool(event, question=QUESTION)
        prompt = plugin.context.llm_generate.await_args_list[-1].kwargs["prompt"]
        assert updated in prompt and QUESTION in prompt and "六爻问卦" in prompt
        assert "{chart}" not in prompt and prompt.endswith(INTERPRETATION_GUARDRAIL)

    asyncio.run(run())


def test_provider_failure_and_storage_failure_release_conversation(setup_workshop):
    plugin, _ = setup_workshop

    async def run():
        previous = plugin.config.copy()
        body = chat_payload(await state(plugin))
        plugin.context.llm_generate = AsyncMock(side_effect=asyncio.TimeoutError)
        with pytest.raises(ManagementError) as error:
            await plugin._admin_workshop_chat(actor="alice", payload=body)
        assert error.value.status == 504
        plugin.put_kv_data = AsyncMock(side_effect=OSError)
        plugin.context.llm_generate.reset_mock()
        with pytest.raises(ManagementError) as error:
            await plugin._admin_workshop_chat(actor="alice", payload=body)
        assert error.value.status == 503 and plugin.config == previous
        assert (await state(plugin))["turns"] == []
        plugin.context.llm_generate.assert_not_awaited()

    asyncio.run(run())


def test_approved_rules_reach_pre_review_classifier_feedback_and_discussion(setup_workshop):
    plugin, event = setup_workshop

    async def run():
        changes = [
            edit(
                "command_pre_review_prompt",
                "判断是否存在有效问题",
                "核对实际所问后判断是否存在有效问题",
            ),
            edit(
                "command_classification_rules",
                "按用户要解决的问题",
                "优先按寻物目的与未知物身份区分，再按用户要解决的问题",
            ),
            edit(
                "feedback_review_prompt",
                "判断反馈是否提供",
                "先检查实物是否找回，再判断反馈是否提供",
            ),
            edit("workshop_discussion_prompt", "先回答本轮问题", "先对照人工纠错再回答本轮问题"),
        ]
        draft = await chat(plugin, response(changes=changes))
        for change in draft["turns"][0]["changes"]:
            assert draft["versions"][change["section"]] == 1
            assert change["after"] not in plugin.config[change["section"]]
            applied = await decide(plugin, change, "approve")
            assert applied["versions"][change["section"]] == 2

        question = "昨天把耳机弄丢了，能找回来吗？"
        plugin.context.llm_generate = AsyncMock(
            side_effect=[
                SimpleNamespace(
                    completion_text='{"reject":false,"valid_question":true,"intent":"射覆"}'
                ),
                SimpleNamespace(completion_text=ANALYSIS),
            ]
        )
        await plugin._dispatch_liuyao(event, question)
        pre, reading = [
            call.kwargs["prompt"] for call in plugin.context.llm_generate.await_args_list
        ]
        assert changes[0]["after"] in pre and changes[1]["after"] in pre
        assert pre.endswith(PRE_REVIEW_PROTOCOL) and question in pre
        assert "寻物研判" in reading and "射覆专用解读要求" not in reading
        assert "规则校正" in plugin._render_and_send_chart.await_args.kwargs["intent_label_suffix"]
        assert plugin._test_store[CASES_KEY]["cases"][0]["intent"] == "lost_item"

        record = case()
        plugin._test_store[CASES_KEY]["cases"].append(record)
        plugin.context.llm_generate = AsyncMock(
            return_value=SimpleNamespace(completion_text=json.dumps(decision(), ensure_ascii=False))
        )
        await plugin._review_case_feedback(event, record, "已经找回了")
        system = plugin.context.llm_generate.await_args.kwargs["system_prompt"]
        assert changes[2]["after"] in system and system.endswith(REVIEW_GUARDRAIL)
        request = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
        assert request["intent_label"] == "事业"
        await chat(plugin, response())
        assert changes[3]["after"] in plugin.context.llm_generate.await_args.kwargs["system_prompt"]

    asyncio.run(run())


def test_corrected_case_and_custom_category_reach_all_workshop_views(setup_workshop):
    plugin, _ = setup_workshop

    async def run():
        record = case(intent="shefu", question="走失的猫能找回来吗？")
        plugin._test_store[CASES_KEY] = [record]
        category = await plugin._admin_create_direction(
            payload={"label": "宠物走失", "description": "寻找走失的宠物"}, actor="alice"
        )
        detail = await plugin._page_case(record["id"], query={})
        await plugin._admin_change_case(
            record["id"],
            {"revision": detail["revision"], "action": "classify", "intent": category["intent"]},
            "alice",
        )
        result = await plugin._page_workshop_cases(
            query={"intent": category["intent"], "feedback": "all"}, actor="alice"
        )
        assert [row["direction"] for row in result["items"]] == ["宠物走失"]
        result = await chat(
            plugin, response(), use_cases=True, serials=[1], focus="command_classification_rules"
        )
        snapshot = result["turns"][-1]["cases"][0]
        assert (
            snapshot["original_intent_label"] == "射覆" and snapshot["intent_label"] == "宠物走失"
        )
        material = json.loads(plugin.context.llm_generate.await_args.kwargs["prompt"])
        assert material["本轮卦例"][0] == snapshot
        assert "寻找走失的宠物" in material["可选分类及适用范围"]
        assert "alice" not in str(snapshot)

    asyncio.run(run())


def test_workshop_discovery_and_config():
    locale = json.loads((ROOT / ".astrbot-plugin/i18n/zh-CN.json").read_text(encoding="utf-8"))
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8").splitlines()
    manage_title = locale["views"]["manage"]["title"]
    assert locale["pages"]["manage"]["title"] == manage_title
    assert f"display_name: {manage_title}" in metadata
    assert locale["views"]["optimize"] == locale["pages"]["optimize"]
    assert (ROOT / "pages/optimize/index.html").is_file()
    assert f'version: "{PLUGIN_VERSION}"' in metadata


def test_management_tab_ships_the_current_workshop():
    assert TARGET.read_text(encoding="utf-8") == render_bundle()


def test_picker_filters_verified_text_outcome_direction_and_keyword_before_paging(setup_workshop):
    plugin, _ = setup_workshop
    cases = [
        {
            "id": str(n),
            "serial": n,
            "intent": "career",
            "question": "合同会签吗",
            "cast": {"primary_name": "乾", "changed_name": "泰"},
            "verdict": "月末有结果",
            "feedback": [{"outcome": "未应验", "text": "实际延期"}],
        }
        for n in range(1, 24)
    ]
    cases.extend(
        [
            {"id": "legacy", "intent": "weather", "feedback": [{"text": "晴天"}]},
            {"id": "invalid", "feedback": [{"text": "延期", "outcome": "未应验", "valid": False}]},
            {"id": "blank", "feedback": [{"text": " "}]},
        ]
    )
    plugin._test_store[CASES_KEY] = cases

    async def listing(**query):
        return await plugin._page_workshop_cases(actor="alice", query=query)

    async def run():
        default = await listing()
        assert default["total"] == 24 and default["items"][0]["serial"] == 24
        assert (await listing(feedback="unverified"))["total"] == 2
        assert (await listing(feedback="未分类", intent="weather", search="晴天"))["total"] == 1
        for word in ("合同", "乾", "泰", "月末", "延期"):
            matches = await listing(feedback="未应验", intent="career", search=word, page="2")
            assert matches["total"] == 23 and matches["pages"] == 2
            assert [row["serial"] for row in matches["items"]] == [3, 2, 1]
        assert (await listing(feedback="应验", search="合同"))["items"] == []
        assert (await listing(search="#21"))["items"][0]["direction"] == "事业"
        assert [row["serial"] for row in (await listing(search="＃003"))["items"]] == [3]
        for bad in ({"page": "0"}, {"feedback": "错"}, {"intent": "错"}):
            with pytest.raises(ManagementError):
                await listing(**bad)
        selected = await chat(plugin, response(), use_cases=True, serials=[23, 3, 25])
        material = selected["turns"][0]["cases"]
        assert [item["serial"] for item in material] == [23, 3, 25]
        assert material[0]["feedback"] == ["未应验 · 实际延期"] and material[2]["feedback"] == []

    asyncio.run(run())


def test_archive_preserves_all_rounds_failures_and_decisions_beyond_reset(setup_workshop):
    plugin, _ = setup_workshop

    async def history(actor="alice", **query):
        return await plugin._page_workshop_history(actor=actor, query=query)

    async def run():
        first = (await chat(plugin, response(changes=[edit()])))["turns"][0]
        for n in range(9):
            await chat(plugin, response("普通讨论"), message=f"讨论 {n}")
        assert len((await state(plugin))["turns"]) == 8
        with pytest.raises(ManagementError):
            await chat(plugin, response(changes=[edit(before="无此原文")]))
        plugin.context.llm_generate = AsyncMock(side_effect=asyncio.TimeoutError)
        with pytest.raises(ManagementError):
            await plugin._admin_workshop_chat(
                actor="alice", payload=chat_payload(await state(plugin))
            )
        await chat(plugin, response(), "bob")
        await plugin._admin_workshop_reset(
            actor="alice", payload={"revision": (await state(plugin))["revision"]}
        )
        latest, old = await history(), await history(page="2")
        assert latest["total"] == 12 and len(old["items"]) == 2 and latest["pages"] == 2
        assert latest["items"][0]["status"] == latest["items"][1]["status"] == "failed"
        assert "超时" in (await history(id=latest["items"][0]["id"]))["reply"]
        archived = await history(id=first["id"])
        assert archived["reply"] == first["reply"]
        assert (
            archived["changes"][0]["status"] == "archived" and archived["changes"][0]["decided_at"]
        )
        assert archived["versions"][GENERAL] == 1 and archived["actor"] == "alice"
        assert (await state(plugin))["turns"] == []
        assert (await history("bob"))["total"] == 1
        with pytest.raises(ManagementError) as denied:
            await history("bob", id=first["id"])
        assert denied.value.status == 404
        with pytest.raises(ManagementError):
            await decide(plugin, first["changes"][0], "approve")
        assert (await plugin._page_workshop_versions(query={"section": GENERAL}, actor="alice"))[
            "total"
        ] == 1

    asyncio.run(run())


def test_version_snapshots_match_live_config_and_link_to_original_evidence(setup_workshop):
    plugin, _ = setup_workshop
    plugin._test_store[CASES_KEY] = [
        {"id": "ref", "serial": 9, "question": "能签约吗", "feedback": [{"text": "只口头答应"}]}
    ]

    async def versions(**query):
        return await plugin._page_workshop_versions(
            actor="alice", query={"section": GENERAL} | query
        )

    async def run():
        original = plugin.config[GENERAL]
        turn = (await chat(plugin, response(changes=[edit()]), use_cases=True, serials=[9]))[
            "turns"
        ][0]
        plugin._test_store[CASES_KEY].clear()
        await decide(plugin, turn["changes"][0], "approve")
        await decide(plugin, turn["changes"][0], "approve")
        v1 = await versions(number="1")
        v2 = await versions(number="2")
        assert v1["source"] == "initial" and v1["text"] == v2["previous"] == original
        assert v2["number"] == 2 and v2["source"] == "workshop" and v2["actor"] == "alice"
        assert v2["turn_id"] == turn["id"] and v2["reason"] == edit()["reason"]
        assert v2["before"] == ORIGINAL and v2["after"] == BETTER
        assert v2["can_view_turn"] is True
        shared = await plugin._page_workshop_versions(
            actor="bob", query={"section": GENERAL, "number": 2}
        )
        assert shared["text"] == v2["text"] and shared["can_view_turn"] is False
        assert v2["text"] == plugin.config[GENERAL] == original.replace(ORIGINAL, BETTER)
        plugin.config[GENERAL] = "管理员手动更新后的整节原文。"
        external = await versions(number="3")
        assert external["source"] == "config" and external["actor"] is None
        assert external["previous"] == v2["text"] and external["text"] == plugin.config[GENERAL]
        assert "+管理员手动更新后的整节原文。" in external["diff"]
        assert [v["number"] for v in (await versions())["items"]] == [3, 2, 1]
        assert (await versions(number="1")) == v1 and (await versions(number="2")) == v2
        detail = await plugin._page_workshop_history(actor="alice", query={"id": turn["id"]})
        assert detail["changes"][0]["version"] == 2 and detail["cases"][0]["feedback"] == [
            "只口头答应"
        ]
        with pytest.raises(ManagementError):
            await versions(number="99")
        with pytest.raises(ManagementError):
            await versions(section="guardrail")

    asyncio.run(run())


@pytest.mark.parametrize("saved", [True, False])
def test_restart_recovers_approval_intent_without_extra_version_or_save(setup_workshop, saved):
    plugin, _ = setup_workshop

    async def run():
        turn = (await chat(plugin, response(changes=[edit()])))["turns"][0]
        draft = turn["changes"][0]
        plugin._journal().decide(draft["id"], "alice", "applying")
        if saved:
            plugin.config[GENERAL] = plugin.config[GENERAL].replace(ORIGINAL, BETTER)
        reloaded = LiuyaoPlugin(plugin.context, Config(plugin.config))
        after = await state(reloaded)
        current = after["turns"][0]["changes"][0]
        assert current["status"] == ("approved" if saved else "pending")
        assert after["versions"][GENERAL] == (2 if saved else 1)
        saves = reloaded.config.saves
        applied = await decide(reloaded, draft, "approve")
        assert applied["versions"][GENERAL] == 2 and BETTER in reloaded.config[GENERAL]
        assert reloaded.config.saves - saves == (0 if saved else 1)

    asyncio.run(run())


def test_interrupted_model_call_survives_reload_as_a_readable_round(setup_workshop):
    plugin, _ = setup_workshop

    async def run():
        started = asyncio.Event()
        finish = asyncio.Event()

        async def generate(**kwargs):
            started.set()
            await finish.wait()
            return response(changes=[edit()])

        plugin.context.llm_generate = generate
        body = chat_payload(await state(plugin))
        pending = asyncio.create_task(plugin._admin_workshop_chat(actor="alice", payload=body))
        await started.wait()
        reloaded = LiuyaoPlugin(plugin.context, Config(plugin.config))
        assert not (await state(reloaded))["processing"]
        rows = await reloaded._page_workshop_history(actor="alice", query={})
        assert rows["total"] == 1 and rows["items"][0]["status"] == "interrupted"
        finish.set()
        with pytest.raises(ManagementError, match="已中断"):
            await pending
        assert not (await state(reloaded))["turns"]
        assert (await state(reloaded))["versions"][GENERAL] == 1

    asyncio.run(run())


def test_plugin_load_captures_config_changes_before_any_workshop_visits(setup_workshop):
    plugin, _ = setup_workshop

    async def run():
        await plugin.initialize()
        first = plugin.config[GENERAL]
        for text in ("第一次在配置页修改。", "第二次在配置页修改。"):
            reloaded = LiuyaoPlugin(plugin.context, Config(plugin.config | {GENERAL: text}))
            reloaded.get_kv_data = plugin.get_kv_data
            reloaded._ensure_user_registry = AsyncMock()
            await reloaded.initialize()
        versions = await reloaded._page_workshop_versions(actor="alice", query={"section": GENERAL})
        assert [row["source"] for row in versions["items"]] == ["config", "config", "initial"]
        assert reloaded._journal().version_detail(GENERAL, 1)["text"] == first
        assert reloaded._journal().history("alice", 1)["total"] == 0

    asyncio.run(run())
