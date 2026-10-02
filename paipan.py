"""Deterministic chart facts; never infer dates or verdicts from a question."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from .najia import TRIGRAM_BITS, relatives_for_bits
except ImportError:  # pragma: no cover
    from najia import TRIGRAM_BITS, relatives_for_bits

BEIJING = timezone(timedelta(hours=8), "北京时间")
CALENDAR_RULE = "北京时间 UTC+8；立春换年、节气交接时刻换月、零点换日"
STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
SPIRITS = ("青龙", "朱雀", "勾陈", "螣蛇", "白虎", "玄武")
SPIRIT_START = (0, 0, 1, 1, 2, 3, 4, 4, 5, 5)
SHI_BY_STAGE = {
    "本宫": 6,
    "一世": 1,
    "二世": 2,
    "三世": 3,
    "四世": 4,
    "五世": 5,
    "游魂": 4,
    "归魂": 3,
}
INNER_STEMS = dict(zip("乾兑离震巽坎艮坤", "甲丁己庚辛戊丙乙", strict=True))
OUTER_STEMS = {**INNER_STEMS, "乾": "壬", "坤": "癸"}


def beijing_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("起卦时间缺少时区，不能推定干支")
    return value.astimezone(BEIJING)


def six_spirits(day_stem: str) -> tuple[str, ...]:
    start = SPIRIT_START[STEMS.index(day_stem)]
    return tuple(SPIRITS[(start + index) % 6] for index in range(6))


def calendar_facts(cast_at: datetime | None) -> dict[str, Any]:
    if cast_at is None:
        return {"status": "未计算：缺少原始起卦时间", "rule": CALENDAR_RULE}
    try:
        local = beijing_time(cast_at)
        if not 1900 <= local.year <= 2100:
            raise ValueError("支持的起卦年份为 1900–2100")
        from lunar_python import Solar

        lunar = Solar.fromYmdHms(
            local.year,
            local.month,
            local.day,
            local.hour,
            local.minute,
            local.second,
        ).getLunar()
        day = lunar.getDayInGanZhiExact2()
        # Derive the hour stem from the SAME midnight-changing day as the day pillar.
        hour_branch = ((local.hour + 1) // 2) % 12
        hour_stem = (STEMS.index(day[0]) % 5 * 2 + hour_branch) % 10
        return {
            "status": "已计算",
            "rule": CALENDAR_RULE,
            "cast_at": local.isoformat(timespec="seconds"),
            "year": lunar.getYearInGanZhiExact(),
            "month": lunar.getMonthInGanZhiExact(),
            "day": day,
            "hour": STEMS[hour_stem] + BRANCHES[hour_branch],
            "month_branch": lunar.getMonthZhiExact(),
            "day_branch": day[1],
            "xun": lunar.getDayXunExact2(),
            "xun_kong": lunar.getDayXunKongExact2(),
        }
    except ImportError:
        return {
            "status": "未计算：请安装 requirements.txt 中的 lunar_python 依赖",
            "rule": CALENDAR_RULE,
        }
    except Exception as exc:
        return {"status": f"未计算：历法转换失败（{type(exc).__name__}）", "rule": CALENDAR_RULE}


def _line_facts(bits, reference_element=None):
    relatives = relatives_for_bits(bits, reference_element=reference_element)
    lower = next(name for name, pattern in TRIGRAM_BITS.items() if pattern == tuple(bits[:3]))
    upper = next(name for name, pattern in TRIGRAM_BITS.items() if pattern == tuple(bits[3:]))
    return relatives, [
        {
            "position": line.position,
            "stem": INNER_STEMS[lower] if line.position <= 3 else OUTER_STEMS[upper],
            "branch": line.branch,
            "element": line.element,
            "relative": line.relative,
            "label": line.label,
        }
        for line in relatives.lines
    ]


def build_chart(cast, cast_at: datetime | None = None) -> dict[str, Any]:
    calendar = calendar_facts(cast_at if cast_at is not None else cast.cast_at)
    relatives, primary = _line_facts(cast.primary_bits)
    _, changed = _line_facts(cast.changed_bits, relatives.palace_element)
    shi = SHI_BY_STAGE[relatives.palace_stage]
    ying = (shi + 2) % 6 + 1
    _, pure = _line_facts(TRIGRAM_BITS[relatives.palace] * 2)
    present = {line["relative"] for line in primary}
    spirits = six_spirits(calendar["day"][0]) if calendar["status"] == "已计算" else ("—",) * 6
    for index, line in enumerate(primary):
        line["role"] = "世" if index + 1 == shi else "应" if index + 1 == ying else ""
        line["spirit"] = spirits[index]
        # Missing relatives in the PRIMARY chart are exposed from its pure palace.
        # These are candidates, not automatic selection of a question's 用神.
        line["hidden"] = pure[index] if pure[index]["relative"] not in present else None
    return {
        "schema_version": 1,
        "calendar": calendar,
        "palace": relatives.palace,
        "palace_element": relatives.palace_element,
        "palace_stage": relatives.palace_stage,
        "shi": shi,
        "ying": ying,
        "primary": primary,
        "changed": changed,
    }


def chart_for(cast, cast_at: datetime | None = None) -> dict[str, Any]:
    if cast.chart is not None:
        return cast.chart
    return build_chart(cast, cast_at)


def chart_text(chart: dict[str, Any]) -> str:
    calendar = chart["calendar"]
    rows = ["排盘口径：" + calendar["rule"]]
    if calendar["status"] == "已计算":
        rows.extend(
            [
                "起卦时间：" + calendar["cast_at"],
                f"干支：{calendar['year']}年 {calendar['month']}月 {calendar['day']}日 "
                f"{calendar['hour']}时；月建：{calendar['month_branch']}；日辰：{calendar['day_branch']}",
                f"旬：{calendar['xun']}；旬空：{calendar['xun_kong']}",
            ]
        )
    else:
        rows.append("日月干支/月建/旬空/六神" + calendar["status"])
    rows.append(f"世应：世爻在{chart['shi']}爻，应爻在{chart['ying']}爻（本卦）")
    for line, changed in zip(chart["primary"], chart["changed"], strict=True):
        hidden = line["hidden"]
        hidden_text = (
            f"；伏神：{hidden['relative']}{hidden['stem']}{hidden['branch']}{hidden['element']}"
            if hidden
            else ""
        )
        rows.append(
            f"{line['position']}爻 {line['spirit']} {line['role'] or '—'} "
            f"{line['relative']}{line['stem']}{line['branch']}{line['element']}"
            f" → {changed['relative']}{changed['stem']}{changed['branch']}{changed['element']}"
            + hidden_text
        )
    rows.append(
        "伏神按本卦缺失六亲从本宫首卦列候选；变卦六亲沿用本卦宫五行。"
        "此处之卦逐爻列装卦信息，只有原动爻发生动变；静爻不作化爻。"
    )
    return "\n".join(rows)
