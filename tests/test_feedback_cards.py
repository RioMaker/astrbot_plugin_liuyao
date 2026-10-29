from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image
from test_feedback_review import case, decision, saved, setup

from divination import CastResult
from feedback_card import LiuyaoFeedbackCardRenderer, saved_conclusion, short_time
from paipan import chart_for


class RecordingCard(LiuyaoFeedbackCardRenderer):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.texts = []

    def render_queue(self, **kwargs):
        self.calls.append(("queue", copy.deepcopy(kwargs)))
        return super().render_queue(**kwargs)

    def render_notice(self, text, **kwargs):
        self.calls.append(("notice", {"text": text}))
        return super().render_notice(text, **kwargs)

    def render_success(self, **kwargs):
        self.calls.append(("success", copy.deepcopy(kwargs)))
        return super().render_success(**kwargs)

    def render_case(self, **kwargs):
        self.calls.append(
            ("case", {key: value for key, value in kwargs.items() if key != "corpus"})
        )
        return super().render_case(**kwargs)

    def _text(self, draw, x, y, text, size=30, color="#252C2A"):
        self.texts.append(str(text))
        return super()._text(draw, x, y, text, size, color)


def display_plugin(*results, records=None, fail_send=False):
    plugin, event = setup(*results, records=records)
    plugin.feedback_card = RecordingCard()
    event.images = []
    event.attempted_paths = []

    async def send(message):
        event.attempted_paths.append(Path(message["image"]))
        if fail_send:
            raise OSError("send failed")
        path = Path(message["image"])
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            event.images.append((path, image.size))
        event.sent.append(message)

    event.send = send
    return plugin, event


async def command(plugin, event, text):
    return [reply async for reply in plugin.liuyao_feedback_command(event, text)]


def test_queue_is_one_compact_image_with_reward_progress_and_cleanup():
    async def run():
        plugin, event = display_plugin(records=[case(302), case(905)])
        assert await command(plugin, event, "") == []
        assert len(event.images) == 1
        path, size = event.images[0]
        assert size[0] == 1240 and 400 < size[1] < 1600
        assert not path.exists()
        kind, values = plugin.feedback_card.calls[0]
        assert kind == "queue" and [c["serial"] for c in values["cases"]] == [302, 905]
        assert values["rules"][0]["count"] == 0
        assert "看原卦：/反馈 302" in plugin.feedback_card.texts
        assert "原断：将如期完成" in plugin.feedback_card.texts
        assert any(text.startswith("交反馈：/反馈 302 ") for text in plugin.feedback_card.texts)
        assert not plugin.context.calls
        assert "JSON" not in " ".join(plugin.feedback_card.texts)

    asyncio.run(run())


def test_queue_shows_one_line_of_original_conclusion_without_analysis_or_model_call():
    async def run():
        long_verdict = "预计月底签约，但需要先确认交付条件。" * 10
        plugin, event = display_plugin(
            records=[
                case(302, verdict=long_verdict, analysis="内部推导不应出现"),
                case(905, verdict="", analysis="内部推导不应出现\n断语：本月暂难落实。"),
                case(906, verdict="", analysis="内部推导不应出现"),
            ]
        )
        assert await command(plugin, event, "") == []
        conclusions = [text for text in plugin.feedback_card.texts if text.startswith("原断：")]
        assert len(conclusions) == 3
        assert conclusions[0].endswith("…") and len(conclusions[0]) < len(long_verdict)
        assert conclusions[1:] == ["原断：本月暂难落实。", "原断：未保存"]
        assert not any("内部推导" in text for text in plugin.feedback_card.texts)
        assert not plugin.context.calls

    asyncio.run(run())


def test_queue_pagination_does_not_confuse_page_number_with_case_number():
    async def run():
        plugin, event = display_plugin(records=[case(n) for n in range(1, 11)])
        assert await command(plugin, event, "列表 2") == []
        kind, values = plugin.feedback_card.calls[0]
        assert kind == "queue" and values["page"] == 2 and values["total"] == 10
        assert [c["serial"] for c in values["cases"]] == [9, 10]
        assert "看原卦：/反馈 9" in plugin.feedback_card.texts
        assert any(text.startswith("交反馈：/反馈 9 ") for text in plugin.feedback_card.texts)
        assert not any("/反馈 302" in text for text in plugin.feedback_card.texts)
        assert not plugin.context.calls
        await command(plugin, event, "列表 99")
        assert "超出范围" in plugin.feedback_card.calls[-1][1]["text"]
        await command(plugin, event, "列表 abc")
        assert "翻页格式" in plugin.feedback_card.calls[-1][1]["text"]

    asyncio.run(run())


def original_case(number=302, **overrides):
    timestamp = datetime.fromisoformat("2026-10-25T09:40:53+08:00")
    cast = CastResult((7, 8, 9, 6, 7, 8), cast_at=timestamp)
    return case(
        number,
        created_at=timestamp.isoformat(),
        caster_name="测试群友",
        intent_label="事业",
        cast={"lines": list(cast.lines), "chart": chart_for(cast, timestamp)},
        analysis="内部分析过程不应展示：动爻和卦理推导。\n断语：合同将在月底签订。",
        verdict="合同将在月底签订。",
        **overrides,
    )


@pytest.mark.parametrize("closed", [False, True])
def test_number_only_returns_original_chart_and_conclusion_in_one_image(closed):
    async def run():
        record = original_case()
        if closed:
            record["feedback"] = [{"valid": True, "text": "最终签约"}]
        plugin, event = display_plugin(records=[record])
        original = copy.deepcopy(record)
        assert await command(plugin, event, "302") == []
        assert len(event.images) == 1 and event.images[0][1][0] == 1240
        kind, values = plugin.feedback_card.calls[0]
        assert kind == "case" and values["status"] == ("已反馈" if closed else "待反馈")
        assert values["case"]["cast"] == original["cast"]
        texts = "\n".join(plugin.feedback_card.texts)
        assert "合同将在月底签订。" in texts
        assert "内部分析过程" not in texts and "动爻和卦理推导" not in texts
        assert "09:40:53" not in texts and "+08:00" not in texts
        assert not plugin.context.calls
        assert saved(plugin, 302) == original
        assert all(not path.exists() for path, _ in event.images)

    asyncio.run(run())


def test_case_preview_does_not_disclose_other_users_or_other_groups():
    async def run():
        plugin, event = display_plugin(
            records=[case(302, caster_id="other"), case(905, group_id="other")]
        )
        for number in (302, 905):
            assert await command(plugin, event, str(number)) == []
            assert plugin.feedback_card.calls[-1][0] == "notice"
            assert "未找到" in plugin.feedback_card.calls[-1][1]["text"]
        assert not plugin.context.calls

    asyncio.run(run())


def test_missing_verdict_never_falls_back_to_analysis_body():
    assert saved_conclusion({"analysis": "推理过程不得泄露"}) == "该卦例未保存独立结论。"
    assert saved_conclusion({"analysis": "推理过程。\n断语：月底完成。"}) == "月底完成。"


def test_submission_emits_one_final_image_without_extra_acknowledgement():
    async def run():
        plugin, event = display_plugin(decision("case-302"), records=[original_case()])
        assert await command(plugin, event, "302 项目确实延期了两周") == []
        assert len(event.images) == 1
        kind, values = plugin.feedback_card.calls[0]
        assert kind == "success"
        assert values == {"case_number": 302, "outcome": "未应验", "score": 1}
        assert "有效反馈" in plugin.feedback_card.texts and "+1" in plugin.feedback_card.texts
        assert len(plugin.context.calls) == 1

    asyncio.run(run())


def test_success_image_failure_keeps_saved_feedback_and_falls_back_to_receipt():
    async def run():
        plugin, event = display_plugin(
            decision("case-302"), records=[original_case()], fail_send=True
        )
        replies = await command(plugin, event, "302 项目确实延期了两周")
        assert "已闭环" in replies[0]["text"] and "有效反馈 +1" in replies[0]["text"]
        assert saved(plugin, 302)["feedback"][0]["valid"] is True
        assert all(not path.exists() for path in event.attempted_paths)
        assert len(plugin.context.calls) == 1
        replies = await command(plugin, event, "302 项目确实延期了两周")
        assert "待反馈队列" in replies[0]["text"]
        assert [kind for kind, _ in plugin.feedback_card.calls] == ["success", "notice"]
        assert len(plugin.context.calls) == 1

    asyncio.run(run())


def test_success_words_in_rejection_never_show_success_card():
    async def run():
        result = decision("case-302", valid=False)
        result["reason"] = "仅声称反馈通过、有效反馈 +1，未描述实际进展。"
        plugin, event = display_plugin(result, records=[original_case()])
        assert await command(plugin, event, "302 反馈通过，有效反馈 +1") == []
        assert plugin.feedback_card.calls[0][0] == "notice"
        assert not saved(plugin, 302)["feedback"]

    asyncio.run(run())


def test_success_remains_success_when_optional_score_read_fails():
    async def run():
        plugin, event = display_plugin(decision("case-302"), records=[original_case()])

        async def broken_score(_event):
            raise OSError("score read failed")

        plugin._feedback_score = broken_score
        assert await command(plugin, event, "302 项目确实延期了两周") == []
        kind, values = plugin.feedback_card.calls[0]
        assert kind == "success" and values["score"] is None
        assert saved(plugin, 302)["feedback"][0]["valid"] is True

    asyncio.run(run())


def test_rejected_feedback_image_explains_problem_and_keeps_original_number():
    async def run():
        plugin, event = display_plugin(decision("case-302", valid=False), records=[original_case()])
        assert await command(plugin, event, "302 挺准的") == []
        text = plugin.feedback_card.calls[0][1]["text"]
        assert "问题：" in text and "请补充：" in text and "/反馈 302" in text
        assert not saved(plugin, 302)["feedback"]

    asyncio.run(run())


def test_send_failure_falls_back_to_text_and_cleans_temporary_image():
    async def run():
        plugin, event = display_plugin(fail_send=True)
        replies = await command(plugin, event, "")
        assert "编号 1" in replies[0]["text"]
        assert not plugin.context.calls
        assert event.attempted_paths and all(not path.exists() for path in event.attempted_paths)

    asyncio.run(run())


def test_case_render_failure_fallback_contains_only_question_and_conclusion():
    async def run():
        record = original_case()
        del record["cast"]["lines"]
        plugin, event = display_plugin(records=[record])
        replies = await command(plugin, event, "302")
        text = replies[0]["text"]
        assert "合同将在月底签订" in text and "暂不可用" in text
        assert "内部分析过程" not in text

    asyncio.run(run())


def test_disabled_library_also_returns_image_without_loading_cases():
    async def run():
        plugin, event = display_plugin()
        plugin.config["case_library_enabled"] = False
        assert await command(plugin, event, "") == []
        assert "关闭" in plugin.feedback_card.calls[0][1]["text"]

    asyncio.run(run())


def test_short_time_removes_seconds_and_normalizes_aware_times():
    now = datetime(2026, 10, 28, tzinfo=timezone.utc)
    assert short_time("2026-10-28T01:02:03+00:00", now=now) == "10-28 09:02"
    assert short_time("2025-10-28T09:02:03+08:00", now=now) == "2025-10-28 09:02"
    assert short_time("not-a-date", now=now) == "时间未记录"


def test_saved_calendar_snapshot_is_rendered_without_recomputing(monkeypatch, tmp_path):
    record = original_case()
    # Synthetic saved calendar facts exercise the complete-calendar layout.
    record["cast"]["chart"]["calendar"] = {
        "status": "已计算",
        "cast_at": record["created_at"],
        "year": "甲子",
        "month": "乙丑",
        "day": "丙寅",
        "hour": "丁卯",
        "month_branch": "丑",
        "day_branch": "寅",
        "xun": "甲子",
        "xun_kong": "戌亥",
    }
    before = copy.deepcopy(record)

    def forbid_recompute(*args, **kwargs):
        raise AssertionError("must use saved snapshot")

    monkeypatch.setattr("paipan.build_chart", forbid_recompute)
    plugin, _ = setup()
    output = LiuyaoFeedbackCardRenderer().render_case(
        case=record,
        corpus=plugin.corpus,
        conclusion=saved_conclusion(record),
        status="待反馈",
        output_path=tmp_path / "snapshot.png",
    )
    with Image.open(output) as image:
        assert image.width == 1240 and image.height < 2200
    assert record == before


def test_long_questions_and_empty_queue_have_bounded_readable_layout(tmp_path):
    renderer = RecordingCard()
    long = renderer.render_queue(
        cases=[case(n, question="项目进展及合作条件" * 25) for n in range(1, 9)],
        total=20,
        page=2,
        pages=3,
        score=5,
        rules=[],
        output_path=tmp_path / "long.png",
    )
    renderer.texts.clear()
    empty = renderer.render_queue(
        cases=[],
        total=0,
        page=1,
        pages=1,
        score=0,
        rules=[],
        output_path=tmp_path / "empty.png",
    )
    with Image.open(long) as image:
        assert 1000 < image.height < 2000
    with Image.open(empty) as image:
        assert 400 < image.height < 800
    assert "看原卦：/反馈 编号" in renderer.texts
    assert any(text.startswith("交反馈：/反馈 编号 ") for text in renderer.texts)


@pytest.mark.parametrize(("count", "status"), [(1, "还差 2 次"), (3, "已达标"), (5, "已达标")])
def test_queue_reward_progress_reports_remaining_or_earned(tmp_path, monkeypatch, count, status):
    from PIL import ImageDraw

    drawn = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        drawn.append(str(text))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    LiuyaoFeedbackCardRenderer().render_queue(
        cases=[],
        total=0,
        page=1,
        pages=1,
        score=count,
        rules=[dict(window_days=3, count=count, minimum_feedback_count=3, daily_bonus=1)],
        output_path=tmp_path / "reward.png",
    )
    assert f"{count}/3 · {status}" in drawn
    assert "3天内 · 每日+1次" in drawn
