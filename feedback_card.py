"""Compact feedback cards drawn from stored facts, without model calls."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from .case_library import extract_verdict
    from .divination import CastResult
    from .paipan import BEIJING, chart_for
    from .renderer import (
        INK,
        JADE,
        MARGIN,
        MUTED,
        PANEL,
        PAPER,
        RULE,
        WIDTH,
        LiuyaoImageRenderer,
        draw_bamboo_background,
        find_cjk_font,
        temp_output_path,
    )
    from .textcard import _wrap
except ImportError:  # pragma: no cover
    from case_library import extract_verdict
    from divination import CastResult
    from paipan import BEIJING, chart_for
    from renderer import (
        INK,
        JADE,
        MARGIN,
        MUTED,
        PANEL,
        PAPER,
        RULE,
        WIDTH,
        LiuyaoImageRenderer,
        draw_bamboo_background,
        find_cjk_font,
        temp_output_path,
    )
    from textcard import _wrap


QUEUE_PAGE_SIZE = 8
CONTENT_WIDTH = WIDTH - 2 * MARGIN
QUEUE_NUMBER_WIDTH = 134
QUEUE_NUMBER_SIZE = 36
QUEUE_TEXT_OFFSET = 174
# 旅卦：上离下艮，按从上爻到初爻的绘图顺序排列。
TRAVEL_HEXAGRAM_TOP_DOWN = (1, 0, 1, 1, 0, 0)


def short_time(value: Any, *, now: datetime | None = None) -> str:
    try:
        timestamp = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return "时间未记录"
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(BEIJING)
    year = (now or datetime.now(BEIJING)).year
    return timestamp.strftime("%m-%d %H:%M" if timestamp.year == year else "%Y-%m-%d %H:%M")


def saved_conclusion(case: dict[str, Any]) -> str:
    # Never fall back to the analysis body: old cases may have no separate verdict.
    text = str(case.get("verdict") or "").strip() or extract_verdict(case.get("analysis", ""))
    return text or "该卦例未保存独立结论。"


def case_cast(case: dict[str, Any]) -> CastResult:
    data = case.get("cast") or {}
    chart = data.get("chart")
    calendar = chart.get("calendar", {}) if isinstance(chart, dict) else {}
    timestamp = calendar.get("cast_at") or case.get("created_at")
    try:
        original_time = datetime.fromisoformat(str(timestamp))
    except (ValueError, TypeError):
        original_time = None
    return CastResult(tuple(data["lines"]), cast_at=original_time, chart=chart)


class LiuyaoFeedbackCardRenderer:
    def __init__(self, *, font_path: str = ""):
        custom = Path(font_path).expanduser() if font_path else None
        self.font_path = custom if custom and custom.is_file() else find_cjk_font(bold=False)
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}

    def _font(self, size):
        if size not in self._fonts:
            self._fonts[size] = ImageFont.truetype(str(self.font_path), size=size)
        return self._fonts[size]

    def _lines(self, text, size=30, width=CONTENT_WIDTH, limit=0):
        draw = ImageDraw.Draw(Image.new("RGB", (WIDTH, 1)))
        lines = _wrap(draw, " ".join(str(text or "").split()), self._font(size), width)
        if limit and len(lines) > limit:
            lines = lines[:limit]
            lines[-1] = lines[-1][:-1] + "…"
        return lines

    def _text(self, draw, x, y, text, size=30, color=INK):
        draw.text((x, y), str(text), font=self._font(size), fill=color, anchor="lt")

    def _canvas(self, height, title, meta=""):
        canvas = Image.new("RGB", (WIDTH, height), PAPER)
        draw_bamboo_background(canvas, WIDTH)
        draw = ImageDraw.Draw(canvas)
        for index, yang in enumerate(TRAVEL_HEXAGRAM_TOP_DOWN):
            top = 49 + index * 9
            if yang:
                draw.rectangle((MARGIN, top, MARGIN + 52, top + 4), fill=JADE)
            else:
                draw.rectangle((MARGIN, top, MARGIN + 20, top + 4), fill=JADE)
                draw.rectangle((MARGIN + 32, top, MARGIN + 52, top + 4), fill=JADE)
        self._text(draw, MARGIN + 78, 49, title, 46, JADE)
        if meta:
            draw.text((WIDTH - MARGIN, 64), meta, font=self._font(24), fill=MUTED, anchor="rt")
        return canvas, draw

    @staticmethod
    def _save(canvas, output_path):
        path = temp_output_path(output_path)
        try:
            canvas.save(path, "PNG", optimize=True)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def render_notice(self, text: str, *, output_path=None) -> Path:
        blocks = [
            self._lines(row, width=CONTENT_WIDTH - 56) for row in text.splitlines() if row.strip()
        ]
        height = 194 + sum(len(lines) * 49 + 22 for lines in blocks)
        canvas, draw = self._canvas(height, "反馈")
        draw.rounded_rectangle((MARGIN, 130, WIDTH - MARGIN, height - 40), radius=14, fill=PANEL)
        y = 157
        for lines in blocks:
            for line in lines:
                self._text(draw, MARGIN + 28, y, line, 31)
                y += 49
            y += 22
        return self._save(canvas, output_path)

    def render_queue(self, *, cases, total, page, pages, score, rules, output_path=None) -> Path:
        rows = []
        for case in cases:
            lines = self._lines(
                case.get("question") or "未记录问题",
                width=CONTENT_WIDTH - QUEUE_TEXT_OFFSET,
                limit=2,
            )
            rows.append((case, lines, 60 + len(lines) * 44))
        height = 190 + sum(row[2] for row in rows) + 156 + len(rules) * 72 + 170
        if not rows:
            height += 82
        canvas, draw = self._canvas(height, "待反馈", f"共 {total} 例 · {page}/{pages} 页")
        self._text(draw, MARGIN, 119, "查看卦例与原断，再填写实际进展", 25, MUTED)
        y = 172
        for case, lines, row_height in rows:
            number_height = 52
            draw.rounded_rectangle(
                (MARGIN, y, MARGIN + QUEUE_NUMBER_WIDTH, y + number_height),
                radius=10,
                fill=JADE,
            )
            number = str(case["serial"])
            font = self._font(QUEUE_NUMBER_SIZE)
            left, top, right, bottom = font.getbbox(number)
            draw.text(
                (
                    MARGIN + (QUEUE_NUMBER_WIDTH - (right - left)) / 2 - left,
                    y + (number_height - (bottom - top)) / 2 - top,
                ),
                number,
                font=font,
                fill=PAPER,
            )
            x = MARGIN + QUEUE_TEXT_OFFSET
            for index, line in enumerate(lines):
                self._text(draw, x, y + index * 44, line, 31)
            cast = case.get("cast") or {}
            names = str(cast.get("primary_name") or "卦名未记录")
            if cast.get("changed_name") and cast.get("moving_lines"):
                names += " → " + str(cast["changed_name"])
            meta = short_time(case.get("created_at")) + " · " + names
            self._text(
                draw,
                x,
                y + len(lines) * 44 + 4,
                self._lines(meta, 23, CONTENT_WIDTH - QUEUE_TEXT_OFFSET, 1)[0],
                23,
                MUTED,
            )
            y += row_height
            draw.line((MARGIN, y - 14, WIDTH - MARGIN, y - 14), fill=RULE)
        if not rows:
            self._text(draw, MARGIN, y + 12, "暂无待反馈卦例", 32, MUTED)
            y += 82
        y += 12
        panel_height = 98 + len(rules) * 72 if rules else 142
        draw.rounded_rectangle((MARGIN, y, WIDTH - MARGIN, y + panel_height), radius=14, fill=PANEL)
        self._text(draw, MARGIN + 26, y + 25, "反馈奖励", 29, JADE)
        draw.text(
            (WIDTH - MARGIN - 26, y + 29),
            f"累计有效 {score} 次",
            font=self._font(25),
            fill=JADE,
            anchor="rt",
        )
        rule_y = y + 78
        for rule in rules:
            count, required = rule["count"], rule["minimum_feedback_count"]
            label = f"{rule['window_days']}天内反馈"
            self._text(draw, MARGIN + 26, rule_y, label, 26)
            bar_x, bar_width = MARGIN + 276, 380
            draw.rounded_rectangle(
                (bar_x, rule_y + 7, bar_x + bar_width, rule_y + 23), radius=8, fill=RULE
            )
            fill = round(bar_width * min(1, count / required))
            if fill:
                draw.rounded_rectangle(
                    (bar_x, rule_y + 7, bar_x + fill, rule_y + 23), radius=8, fill=JADE
                )
            detail = f"{count}/{required} · 每日 +{rule['daily_bonus']} 次"
            draw.text(
                (WIDTH - MARGIN - 26, rule_y), detail, font=self._font(26), fill=JADE, anchor="rt"
            )
            rule_y += 72
        if not rules:
            self._text(draw, MARGIN + 26, rule_y - 3, "暂无加额规则", 24, MUTED)
        y += panel_height + 32
        self._text(draw, MARGIN, y, "查看 /反馈 编号    提交 /反馈 编号 实际进展", 28, JADE)
        self._text(
            draw, MARGIN, y + 48, "未应验也可通过；每例通过计 1 次，未通过可补充。", 25, MUTED
        )
        if pages > 1:
            navigation = []
            if page > 1:
                navigation.append(f"上一页 /反馈 列表 {page - 1}")
            if page < pages:
                navigation.append(f"下一页 /反馈 列表 {page + 1}")
            self._text(draw, MARGIN, y + 94, "    ".join(navigation), 25, MUTED)
        return self._save(canvas, output_path)

    def render_case(self, *, case, corpus, conclusion, status, output_path=None) -> Path:
        cast = case_cast(case)
        chart = chart_for(cast, cast.cast_at)
        question = self._lines(case.get("question") or "未记录问题", 33, CONTENT_WIDTH - 48)
        verdict = self._lines(conclusion, 34, CONTENT_WIDTH - 56)
        question_height = len(question) * 48 + 42
        calendar = chart["calendar"]
        calendar_height = 176 if calendar.get("status") == "已计算" else 66
        conclusion_height = 94 + len(verdict) * 52
        height = 206 + question_height + calendar_height + 606 + conclusion_height + 178
        canvas, draw = self._canvas(height, f"卦例 {case['serial']}", status)
        calendar_time = calendar.get("cast_at") or case.get("created_at")
        self._text(
            draw,
            MARGIN,
            116,
            short_time(calendar_time) + " · " + str(case.get("intent_label") or "六爻"),
            25,
            MUTED,
        )
        y = 165
        draw.rounded_rectangle(
            (MARGIN, y, WIDTH - MARGIN, y + question_height), radius=12, fill=PANEL
        )
        for index, line in enumerate(question):
            self._text(draw, MARGIN + 24, y + 22 + index * 48, line, 33)
        y += question_height + 28
        primary, changed = corpus.get(cast.primary_number), corpus.get(cast.changed_number)
        name = primary["name"] + (" → " + changed["name"] if cast.moving_lines else " · 静卦")
        self._text(draw, MARGIN, y, name, 34, JADE)
        y += 58
        # Use exactly the stored chart snapshot, preserving its original six lines
        # and calendar facts. No new casting, interpretation or model request.
        chart_renderer = LiuyaoImageRenderer(corpus, font_path=self.font_path)
        fonts = {
            key: self._font(size)
            for key, size in (("small", 23), ("data", 27), ("tiny", 21), ("pillar", 40))
        }
        if calendar.get("status") == "已计算":
            chart_renderer._draw_calendar(draw, y, calendar_height, calendar, [], fonts)
        else:
            self._text(draw, MARGIN, y + 12, "未保存完整干支信息", 24, MUTED)
        y += calendar_height
        chart_renderer._draw_chart(draw, y, cast, chart, fonts)
        y += 624
        draw.rounded_rectangle(
            (MARGIN, y, WIDTH - MARGIN, y + conclusion_height), radius=14, fill=PANEL
        )
        self._text(draw, MARGIN + 28, y + 22, "原断结论", 28, JADE)
        for index, line in enumerate(verdict):
            self._text(draw, MARGIN + 28, y + 72 + index * 52, line, 34, JADE)
        y += conclusion_height + 32
        instruction = (
            f"/反馈 {case['serial']} 实际进展或结果"
            if status == "待反馈"
            else "/反馈 返回待反馈队列"
        )
        self._text(draw, MARGIN, y, instruction, 27, MUTED)
        return self._save(canvas, output_path)
