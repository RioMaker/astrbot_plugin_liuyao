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
        draw_taiji_logo,
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
        draw_taiji_logo,
        find_cjk_font,
        temp_output_path,
    )
    from textcard import _wrap


QUEUE_PAGE_SIZE = 8
CONTENT_WIDTH = WIDTH - 2 * MARGIN
QUEUE_NUMBER_WIDTH = 134
QUEUE_NUMBER_SIZE = 36
QUEUE_TEXT_OFFSET = 174


def short_time(value: Any, *, now: datetime | None = None) -> str:
    try:
        timestamp = datetime.fromisoformat(str(value or ""))
    except (TypeError, ValueError):
        return "时间未记录"
    if timestamp.tzinfo is not None:
        timestamp = timestamp.astimezone(BEIJING)
    year = (now or datetime.now(BEIJING)).year
    return timestamp.strftime("%m-%d %H:%M" if timestamp.year == year else "%Y-%m-%d %H:%M")


def saved_conclusion(case: dict[str, Any], *, fallback: str = "该卦例未保存独立结论。") -> str:
    # Never fall back to the analysis body: old cases may have no separate verdict.
    text = str(case.get("verdict") or "").strip() or extract_verdict(case.get("analysis", ""))
    return text or fallback


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
        draw_taiji_logo(draw, MARGIN, 49)
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

    def render_success(self, *, case_number, outcome, score=None, output_path=None) -> Path:
        """Celebrate one persisted, accepted feedback, without implying extra rewards."""
        canvas, draw = self._canvas(690, "反馈成功", f"卦例 {case_number}")
        green = "#27704E"
        pale = "#E7EEDC"
        gold = "#D4B978"
        draw.rounded_rectangle((MARGIN, 140, WIDTH - MARGIN, 570), radius=24, fill=JADE)
        # A drawn check avoids missing or differently styled emoji glyphs.
        cx, cy, radius = 230, 307, 92
        draw.ellipse((cx - 109, cy - 109, cx + 109, cy + 109), outline="#476957", width=2)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=pale)
        draw.line(
            ((cx - 40, cy), (cx - 9, cy + 29), (cx + 45, cy - 30)),
            fill=green,
            width=15,
            joint="curve",
        )
        for px, py, length in ((139, 191, 8), (337, 228, 6), (325, 400, 8)):
            draw.line((px - length, py, px + length, py), fill=gold, width=3)
            draw.line((px, py - length, px, py + length), fill=gold, width=3)
        self._text(draw, 390, 195, "反馈通过", 60, PAPER)
        self._text(draw, 393, 279, f"{outcome} · 本例已闭环", 29, "#CAD9C8")
        draw.line((392, 338, WIDTH - MARGIN - 48, 338), fill="#476957", width=1)
        self._text(draw, 393, 380, "有效反馈", 35, PAPER)
        self._text(draw, 600, 355, "+1", 112, gold)
        if score is not None:
            self._text(draw, 395, 502, f"累计有效反馈 {score} 次", 27, "#CAD9C8")
        self._text(draw, MARGIN, 609, "一卦有始有终，多谢回来报信。", 29, JADE)
        draw.text(
            (WIDTH - MARGIN, 613),
            "/反馈 查看队列与奖励",
            font=self._font(25),
            fill=MUTED,
            anchor="rt",
        )
        return self._save(canvas, output_path)

    def render_queue(self, *, cases, total, page, pages, score, rules, output_path=None) -> Path:
        rows = []
        for case in cases:
            lines = self._lines(
                case.get("question") or "未记录问题",
                width=CONTENT_WIDTH - QUEUE_TEXT_OFFSET,
                limit=2,
            )
            rows.append((case, lines, 90 + len(lines) * 44))
        height = 248 + sum(row[2] for row in rows) + 156 + len(rules) * 72 + 170
        if not rows:
            height += 82
        canvas, draw = self._canvas(height, "待反馈", f"共 {total} 例 · {page}/{pages} 页")
        self._text(draw, MARGIN, 119, "查看卦例与原断，再填写实际进展", 25, MUTED)
        draw.rounded_rectangle((MARGIN, 166, WIDTH - MARGIN, 209), radius=8, fill=PANEL)
        draw.text(
            (MARGIN + QUEUE_NUMBER_WIDTH / 2, 176),
            "序号",
            font=self._font(24),
            fill=JADE,
            anchor="mt",
        )
        self._text(draw, MARGIN + QUEUE_TEXT_OFFSET, 176, "问题", 24, JADE)
        y = 230
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
            conclusion = self._lines(
                f"原断：{saved_conclusion(case, fallback='未保存')}",
                25,
                CONTENT_WIDTH - QUEUE_TEXT_OFFSET,
                1,
            )[0]
            self._text(draw, x, y + len(lines) * 44 + 2, conclusion, 25, JADE)
            cast = case.get("cast") or {}
            names = str(cast.get("primary_name") or "卦名未记录")
            if cast.get("changed_name") and cast.get("moving_lines"):
                names += " → " + str(cast["changed_name"])
            meta = short_time(case.get("created_at")) + " · " + names
            self._text(
                draw,
                x,
                y + len(lines) * 44 + 34,
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
            label = f"{rule['window_days']}天内 · 每日+{rule['daily_bonus']}次"
            detail = f"{count}/{required} · " + (
                f"还差 {required - count} 次" if count < required else "已达标"
            )
            self._text(draw, MARGIN + 26, rule_y, label, 26)
            bar_x = max(MARGIN + 276, MARGIN + 50 + draw.textlength(label, font=self._font(26)))
            bar_right = min(
                MARGIN + 656,
                WIDTH - MARGIN - 50 - draw.textlength(detail, font=self._font(26)),
            )
            bar_width = max(0, bar_right - bar_x)
            if bar_width:
                draw.rounded_rectangle(
                    (bar_x, rule_y + 7, bar_right, rule_y + 23), radius=8, fill=RULE
                )
                fill = round(bar_width * min(1, count / required))
                if fill:
                    draw.rounded_rectangle(
                        (bar_x, rule_y + 7, bar_x + fill, rule_y + 23), radius=8, fill=JADE
                    )
            draw.text(
                (WIDTH - MARGIN - 26, rule_y), detail, font=self._font(26), fill=JADE, anchor="rt"
            )
            rule_y += 72
        if not rules:
            self._text(draw, MARGIN + 26, rule_y - 3, "暂无加额规则", 24, MUTED)
        y += panel_height + 32
        example_number = str(rows[0][0]["serial"]) if rows else "编号"
        self._text(draw, MARGIN, y, f"看原卦：/反馈 {example_number}", 28, JADE)
        self._text(
            draw,
            MARGIN,
            y + 48,
            f"交反馈：/反馈 {example_number} 实际发生了……，与原断相比……",
            28,
            JADE,
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
