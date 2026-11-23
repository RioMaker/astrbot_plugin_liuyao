from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_analysis import ANALYSIS, QUESTION, prepare
from test_permissions import ROOT

from divination import CastResult
from main import PLUGIN_VERSION, LiuyaoPlugin
from prompts import INTERPRETATION_GUARDRAIL, INTERPRETATION_PROMPT
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
def setup_workshop():
    # Construct the actual Star to exercise config defaults and ReadingService wiring.
    base, event = prepare()
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
        assert again["turns"] == []
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
        edit("command_pre_review_prompt"),
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


def test_workshop_discovery_and_config():
    locale = json.loads((ROOT / ".astrbot-plugin/i18n/zh-CN.json").read_text(encoding="utf-8"))
    assert locale["views"]["optimize"] == locale["pages"]["optimize"]
    assert (ROOT / "pages/optimize/index.html").is_file()
    assert f'version: "{PLUGIN_VERSION}"' in (ROOT / "metadata.yaml").read_text(encoding="utf-8")
