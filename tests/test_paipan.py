"""Calendar boundaries, classical examples and original-chart preservation."""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from itertools import product

import pytest
from test_permissions import _Event, _make_enabled_plugin

import cast_policy
from divination import CastResult
from paipan import BEIJING, build_chart, calendar_facts, chart_for, chart_text, six_spirits

AT = datetime(2026, 10, 2, 12, tzinfo=BEIJING)


def test_known_calendar_and_same_instant_in_different_timezones():
    result = calendar_facts(AT)
    assert result == calendar_facts(AT.astimezone(timezone.utc))
    assert (result["year"], result["month"], result["day"]) == ("丙午", "丁酉", "己酉")
    assert (result["hour"], result["xun"], result["xun_kong"]) == ("庚午", "甲辰", "寅卯")


def test_precise_solar_term_month_and_year_boundaries():
    # Fixed fixtures from lunar_python 1.4.8's solar term table, documented in PAIPAN.md.
    lichun = datetime(2026, 2, 4, 4, 2, 8, tzinfo=BEIJING)
    before = calendar_facts(lichun - timedelta(seconds=1))
    after = calendar_facts(lichun)
    assert (before["year"], before["month"]) == ("乙巳", "己丑")
    assert (after["year"], after["month"]) == ("丙午", "庚寅")
    jingzhe = datetime(2026, 3, 5, 21, 59, tzinfo=BEIJING)
    assert calendar_facts(jingzhe - timedelta(seconds=1))["month"] == "庚寅"
    assert calendar_facts(jingzhe)["month"] == "辛卯"


def test_midnight_day_rollover_and_late_zi_hour_share_declared_day():
    late = calendar_facts(AT.replace(hour=23, minute=59, second=59))
    early = calendar_facts((AT + timedelta(days=1)).replace(hour=0))
    assert late["day"] == "己酉" and late["hour"] == "甲子"
    assert early["day"] == "庚戌" and early["hour"] == "丙子"
    assert late["xun_kong"] == early["xun_kong"] == "寅卯"
    # 己亥 -> 庚子 does not change the 甲午旬; 癸卯 -> 甲辰 starts a new 旬.
    edge = AT + timedelta(days=54)
    assert calendar_facts(edge)["day"] == "癸卯"
    assert calendar_facts(edge)["xun_kong"] == "辰巳"
    assert calendar_facts(edge + timedelta(days=1))["xun_kong"] == "寅卯"


@pytest.mark.parametrize(
    "stem,first",
    list(
        zip(
            "甲乙丙丁戊己庚辛壬癸",
            ("青龙", "青龙", "朱雀", "朱雀", "勾陈", "螣蛇", "白虎", "白虎", "玄武", "玄武"),
            strict=True,
        )
    ),
)
def test_six_spirits_by_day_stem(stem, first):
    spirits = six_spirits(stem)
    assert spirits[0] == first
    assert len(set(spirits)) == 6
    assert six_spirits("甲") == ("青龙", "朱雀", "勾陈", "螣蛇", "白虎", "玄武")


@pytest.mark.parametrize(
    "values,shi,ying",
    [
        ((7, 7, 7, 7, 7, 7), 6, 3),  # 乾
        ((8, 7, 7, 7, 7, 7), 1, 4),  # 姤
        ((8, 8, 7, 7, 7, 7), 2, 5),  # 遁
        ((8, 8, 8, 7, 7, 7), 3, 6),  # 否
        ((8, 8, 8, 8, 7, 7), 4, 1),  # 观
        ((8, 8, 8, 8, 8, 7), 5, 2),  # 剥
        ((8, 8, 8, 7, 8, 7), 4, 1),  # 晋（游魂）
        ((7, 7, 7, 7, 8, 7), 3, 6),  # 大有（归魂）
    ],
)
def test_classical_qian_palace_shi_ying_examples(values, shi, ying):
    chart = build_chart(CastResult(values, cast_at=AT))
    assert (chart["shi"], chart["ying"]) == (shi, ying)
    assert chart["primary"][shi - 1]["role"] == "世"
    assert chart["primary"][ying - 1]["role"] == "应"


def test_hidden_relatives_match_dun_example_and_do_not_use_changed_palace():
    chart = build_chart(CastResult((6, 8, 7, 7, 7, 7), cast_at=AT))
    assert chart["primary"][0]["hidden"]["label"] == "子孙子水"
    assert chart["primary"][1]["hidden"]["label"] == "妻财寅木"
    assert all(line["hidden"] is None for line in chart["primary"][2:])
    assert chart["changed"][0]["relative"] == "妻财"  # 离初爻卯木，仍按乾宫金克木。


def test_all_hexagrams_have_two_roles_and_only_missing_hidden_relatives():
    for values in product((7, 8), repeat=6):
        chart = build_chart(CastResult(values))
        roles = [line["role"] for line in chart["primary"]]
        assert roles.count("世") == roles.count("应") == 1
        present = {line["relative"] for line in chart["primary"]}
        assert all(
            line["hidden"] is None or line["hidden"]["relative"] not in present
            for line in chart["primary"]
        )
        if len(present) == 5:
            assert all(line["hidden"] is None for line in chart["primary"])


def test_missing_naive_and_unsupported_dates_never_use_today():
    for value in (None, datetime(2026, 10, 2), datetime(1800, 1, 1, tzinfo=BEIJING)):
        facts = calendar_facts(value)
        assert facts["status"].startswith("未计算")
        assert "day" not in facts
    chart = build_chart(CastResult((7,) * 6))
    assert "世爻在6爻" in chart_text(chart)
    assert "未计算" in chart_text(chart)


def test_dependency_failure_preserves_static_chart(monkeypatch):
    monkeypatch.setitem(sys.modules, "lunar_python", None)
    chart = build_chart(CastResult((8, 8, 7, 7, 7, 7), cast_at=AT))
    assert "依赖" in chart["calendar"]["status"]
    assert chart["shi"] == 2 and chart["primary"][0]["hidden"]["label"] == "子孙子水"
    assert all(line["spirit"] == "—" for line in chart["primary"])


def test_admission_reuse_and_case_archive_keep_identical_snapshot(monkeypatch):
    plugin = _make_enabled_plugin()
    event = _Event("member")
    monkeypatch.setattr(cast_policy.time, "time", lambda: AT.timestamp())

    async def check():
        cast, error = await plugin._prepare_cast(
            event,
            CastResult((8, 8, 7, 7, 7, 7)),
            question="这个项目能否按期签约",
            intent="career",
            method="手动铜币",
        )
        assert not error and cast.cast_at == AT
        snapshot = json.loads(json.dumps(cast.chart))
        last = plugin._test_store["cast_policy:20002"]["last"]
        assert last["chart"] == snapshot
        await plugin._open_agent_case(
            event,
            cast,
            intent="career",
            question=last["question"],
            method=last["method"],
            cast_at=cast.cast_at,
        )
        case = plugin._test_store["liuyao_case_library"]["cases"][0]
        assert case["cast"]["chart"] == snapshot
        monkeypatch.setattr(cast_policy.time, "time", lambda: AT.timestamp() + 86400 * 30)
        restored = _make_enabled_plugin()
        restored.get_kv_data = plugin.get_kv_data
        result = await restored._reuse_cast(event, for_agent=True)
        assert chart_text(snapshot) in result
        assert "己酉日" in result and "月建：酉" in result
        assert len(plugin._test_store["cast_policy:20002"]["timestamps"]) == 1
        # A stored snapshot wins even when a caller supplies a different date.
        assert chart_for(cast, AT + timedelta(days=40)) == snapshot
        # Old records without a snapshot use their ORIGINAL timestamp, without mutation.
        del last["chart"]
        assert "己酉日" in await restored._reuse_cast(event)
        assert "chart" not in last
        del last["timestamp"]
        unknown = await restored._reuse_cast(event)
        assert "未计算：缺少原始起卦时间" in unknown and "世爻在2爻" in unknown

    asyncio.run(check())


@pytest.mark.parametrize("created_at", [AT.isoformat(), "2026-10-02T12:00:00", ""])
def test_historical_redraw_uses_saved_snapshot_or_original_time(tmp_path, created_at):
    plugin = _make_enabled_plugin()
    event = _Event("member", astrbot_admin=True)
    cast = CastResult((8, 8, 7, 7, 7, 7), cast_at=AT)
    case = plugin._build_case_record(
        event,
        cast,
        intent="career",
        question="能否签约",
        method="手动",
        cast_at=AT,
        caster_name="群友",
        caster_id="20002",
    )
    case["serial"] = 1
    snapshot = case["cast"]["chart"]
    case["created_at"] = created_at
    seen = []

    class Renderer:
        def render(self, received, **kwargs):
            seen.append(chart_for(received, kwargs["cast_at"]))
            path = tmp_path / "history.png"
            path.write_bytes(b"image")
            return path

    plugin.renderer = Renderer()
    asyncio.run(plugin._case_detail(event, case))
    assert seen[-1] == snapshot  # Even if older metadata is missing/ambiguous.
    del case["cast"]["chart"]
    asyncio.run(plugin._case_detail(event, case))
    if created_at == AT.isoformat():
        assert seen[-1] == snapshot
    else:
        assert seen[-1]["calendar"]["status"].startswith("未计算")
        assert "day" not in seen[-1]["calendar"]
    assert len(event.sent) == 2


def test_renderer_shows_snapshot_pillars_and_original_beijing_time(tmp_path, monkeypatch):
    from dataclasses import replace

    from PIL import ImageDraw

    from renderer import LiuyaoImageRenderer

    plugin = _make_enabled_plugin()
    cast = CastResult((6, 8, 7, 7, 7, 7), cast_at=AT)
    cast = replace(cast, chart=build_chart(cast))
    drawn = []
    original = ImageDraw.ImageDraw.text

    def capture(self, xy, text, *args, **kwargs):
        drawn.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture)
    LiuyaoImageRenderer(plugin.corpus).render(
        cast,
        caster_name="群友",
        caster_id="20002",
        group_id="10001",
        intent_label="事业",
        question="能否签约",
        method="手动",
        cast_at=AT + timedelta(days=40),
        output_path=tmp_path / "chart.png",
    )
    text = "\n".join(drawn)
    assert "2026-10-02 12:00:00" in text and "己酉日" in text
    assert "旬空 寅卯" in text and "螣蛇" in text
    assert "世爻 · 伏：妻财寅木" in text and "应爻" in text
    assert "伏：子孙子水" in text
