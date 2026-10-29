"""Render a compact AI interpretation as a "jade slip" (玉笺) card.

The card is deliberately restrained: ivory paper, one jade title, a vermilion
seal, the question quoted once, the interpretation as short paragraphs and the
verdict in a jade panel.  It shares fonts, palette and bamboo decoration with
the chart renderer so both images look like one family.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from .renderer import (
        INK,
        JADE,
        MUTED,
        PAPER,
        RULE,
        TITLE_FONT_PATH,
        ChartRenderError,
        draw_bamboo_background,
        draw_taiji_logo,
        find_cjk_font,
        temp_output_path,
    )
except ImportError:  # pragma: no cover - direct local execution
    from renderer import (
        INK,
        JADE,
        MUTED,
        PAPER,
        RULE,
        TITLE_FONT_PATH,
        ChartRenderError,
        draw_bamboo_background,
        draw_taiji_logo,
        find_cjk_font,
        temp_output_path,
    )


CARD_WIDTH = 1080
MARGIN = 76
CONTENT_RIGHT = CARD_WIDTH - MARGIN
CONTENT_WIDTH = CONTENT_RIGHT - MARGIN
TEXT_LEFT = MARGIN + 52
TEXT_WIDTH = CONTENT_RIGHT - 28 - TEXT_LEFT
VERDICT_LEFT = MARGIN + 116

PAPER_DEEP = "#EFEBDC"
JADE_PANEL = "#E9EEDF"
FRAME = "#D9D3C1"
SEAL = "#B34F40"

SIZES = {
    "title": 52,
    "seal": 36,
    "meta": 23,
    "label": 23,
    "question": 30,
    "body": 31,
    "verdict": 32,
    "footer": 19,
}

BODY_LINE = 60
PARAGRAPH_GAP = 24
_TOP_PAD = 62
_FOOTER_GAP = 40
_BOTTOM_PAD = 56
_CLOSING = "，。、；：？！）】》」』’”%,.;:?!)]}…"


class LiuyaoTextCardRenderer:
    """Draw one self-contained PNG from already-decided strings."""

    def __init__(self, *, font_path: str | Path | None = None):
        custom = Path(font_path).expanduser() if font_path else None
        if custom is not None and custom.is_file():
            self._body_path = custom
            self._heading_path = custom
        else:
            self._body_path = find_cjk_font(bold=False)
            self._heading_path = TITLE_FONT_PATH if TITLE_FONT_PATH.is_file() else self._body_path
        self._fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}

    def _font(self, size: int, *, heading: bool = False) -> ImageFont.FreeTypeFont:
        key = (size, heading)
        font = self._fonts.get(key)
        if font is None:
            path = self._heading_path if heading else self._body_path
            font = ImageFont.truetype(str(path), size=size)
            self._fonts[key] = font
        return font

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def render_help(self, text: str, *, output_path: str | Path | None = None) -> Path:
        """Group the shared command text by task, with a prominent starting point."""
        lines = _clean_block(text).splitlines()
        entries = [tuple(line.split("：", 1)) for line in lines[1:]]
        cast = next(row for row in entries if row[0].startswith("/六爻 <"))
        alias = next(row for row in entries if row[0].startswith("/起卦 "))
        feedback = [
            row for row in entries if row[0].startswith("/六爻反馈") and "列表" not in row[0]
        ]
        paging = next(row for row in entries if row[0].startswith("/六爻反馈 列表"))
        admin = next(row for row in entries if row[0].startswith("/六爻 开"))
        help_row = next(row for row in entries if row[0] == "/六爻")
        utilities = [row for row in entries if row[0] in {"/额度", "/ping"}]
        measure = ImageDraw.Draw(Image.new("RGB", (CARD_WIDTH, 1), PAPER))

        def wrap(value, size, width):
            return _wrap(measure, value, self._font(size), width)

        cast_description = wrap(cast[1], 25, CONTENT_WIDTH - 64)
        hero_height = 228 + len(cast_description) * 38
        alias_lines = wrap(f"{alias[1]} {alias[0]}", 22, CONTENT_WIDTH)
        feedback_y = 190 + hero_height + 28 + len(alias_lines) * 34 + 42
        feedback_left = MARGIN + 210
        feedback_width = CONTENT_RIGHT - feedback_left - 28
        feedback_rows = []
        for label, (command, description) in zip(
            ("查编号", "看原断", "交反馈"), feedback, strict=True
        ):
            commands = wrap(command, 29, feedback_width)
            descriptions = wrap(description, 24, feedback_width)
            height = 40 + len(commands) * 42 + len(descriptions) * 35
            feedback_rows.append((label, commands, descriptions, height))
        paging_lines = wrap(f"翻页  {paging[0]} · {paging[1]}", 22, CONTENT_WIDTH - 56)
        feedback_height = sum(row[3] for row in feedback_rows) + 128 + len(paging_lines) * 34
        utilities_y = feedback_y + 62 + feedback_height + 46
        utility_width = (CONTENT_WIDTH - 20) // 2
        utility_rows = [
            (command, wrap(description, 24, utility_width - 48))
            for command, description in utilities
        ]
        utility_height = 78 + max(len(row[1]) for row in utility_rows) * 35
        admin_lines = wrap(f"{admin[0]}  ·  {admin[1]}", 23, CONTENT_WIDTH - 160)
        admin_height = 36 + len(admin_lines) * 35
        footer_y = utilities_y + 62 + utility_height + 18 + admin_height + 36
        height = footer_y + 106
        canvas = Image.new("RGB", (CARD_WIDTH, height), PAPER)
        draw_bamboo_background(canvas, CARD_WIDTH)
        draw = ImageDraw.Draw(canvas)
        _draw_frame(draw, height)

        def write(x, y, value, size=25, color=INK):
            _text(draw, x, y, value, self._font(size), color)

        def write_lines(x, y, rows, size, leading, color=INK):
            for row in rows:
                write(x, y, row, size, color)
                y += leading
            return y

        def section(y, number, label, hint):
            write(MARGIN, y + 3, number, 25, SEAL)
            write(MARGIN + 53, y, label, 32, JADE)
            _text_right(draw, CONTENT_RIGHT, y + 8, hint, self._font(21), MUTED)

        draw_taiji_logo(draw, MARGIN, 63, width=64, height=64)
        write(MARGIN + 90, 63, lines[0], 46, JADE)
        write(MARGIN + 92, 126, "先起卦，再回看，有结果后回来反馈", 23, MUTED)

        # The primary action is deliberately stronger than the reference commands.
        _panel(draw, 190, hero_height, fill=JADE)
        write(MARGIN + 30, 216, "01  开始起卦", 26, "#DADDBA")
        _text_right(draw, CONTENT_RIGHT - 30, 220, "常用入口", self._font(21), "#DADDBA")
        write(MARGIN + 30, 263, cast[0], 40, PAPER)
        bottom = write_lines(MARGIN + 32, 324, cast_description, 25, 38, PAPER)
        draw.rounded_rectangle(
            (MARGIN + 24, bottom + 12, CONTENT_RIGHT - 24, bottom + 68),
            radius=9,
            fill="#365A4C",
        )
        write(MARGIN + 40, bottom + 27, "示例", 22, "#DADDBA")
        write(MARGIN + 116, bottom + 24, "/六爻 这个项目能按期完成吗", 28, PAPER)
        write_lines(MARGIN + 4, 190 + hero_height + 24, alias_lines, 22, 34, MUTED)

        section(feedback_y, "02", "回看与反馈", "先查编号，再提交结果")
        top = feedback_y + 62
        _panel(draw, top, feedback_height, fill=JADE_PANEL)
        y = top
        for index, (label, commands, descriptions, row_height) in enumerate(feedback_rows, 1):
            cx, cy = MARGIN + 40, y + 37
            if index < len(feedback_rows):
                draw.line((cx, cy + 17, cx, cy + row_height - 17), fill=RULE, width=2)
            draw.ellipse((cx - 17, cy - 17, cx + 17, cy + 17), fill=JADE)
            _text_at(draw, cx, cy, str(index), self._font(21), PAPER)
            write(MARGIN + 72, y + 23, label, 27, JADE)
            bottom = write_lines(feedback_left, y + 22, commands, 29, 42, JADE)
            write_lines(feedback_left, bottom + 2, descriptions, 24, 35)
            y += row_height
        draw.line((MARGIN + 28, y, CONTENT_RIGHT - 28, y), fill=RULE, width=1)
        y = write_lines(MARGIN + 28, y + 20, paging_lines, 22, 34, MUTED)
        write(MARGIN + 28, y + 16, "示例  /六爻反馈 302 项目已按期完成", 26, JADE)
        write(MARGIN + 28, y + 57, "302 为示例编号，请替换为你列表中的实际编号。", 22, MUTED)

        section(utilities_y, "03", "查询与管理", "按需使用")
        for index, (command, descriptions) in enumerate(utility_rows):
            left = MARGIN + index * (utility_width + 20)
            top = utilities_y + 62
            draw.rounded_rectangle(
                (left, top, left + utility_width, top + utility_height),
                radius=12,
                fill=PAPER_DEEP,
            )
            write(left + 24, top + 22, command, 32, JADE)
            label = "查额度" if command == "/额度" else "测模型"
            _text_right(draw, left + utility_width - 24, top + 30, label, self._font(22), MUTED)
            write_lines(left + 24, top + 73, descriptions, 24, 35)
        top = utilities_y + 62 + utility_height + 18
        _panel(draw, top, admin_height, fill=PAPER_DEEP)
        draw.rounded_rectangle(
            (MARGIN + 18, top + 18, MARGIN + 119, top + 53), radius=6, fill="#E8D9C7"
        )
        write(MARGIN + 34, top + 24, "管理员", 22, "#805A38")
        write_lines(MARGIN + 142, top + 18, admin_lines, 23, 35)

        _rule(draw, footer_y)
        write(MARGIN, footer_y + 22, f"{help_row[0]}  ·  {help_row[1]}", 21, MUTED)
        _text_right(draw, CONTENT_RIGHT, footer_y + 22, "查看帮助不消耗额度", self._font(21), MUTED)
        path = temp_output_path(output_path)
        try:
            canvas.save(path, "PNG", optimize=True)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise ChartRenderError(f"保存六爻帮助图失败：{exc}") from exc
        return path

    def render(
        self,
        *,
        question: str = "",
        subtitle: str = "",
        body: str = "",
        verdict: str = "",
        meta: str = "",
        headline: str = "六爻解卦",
        footer: str = "六爻 · 可可子",
        footer_note: str = "卦象为参考，不作定论",
        output_path: str | Path | None = None,
    ) -> Path:
        """Render the card and return the PNG path."""
        measure = ImageDraw.Draw(Image.new("RGB", (CARD_WIDTH, 32), PAPER))
        question_text = _clean(question)
        body_text = _clean_block(body)
        verdict_text = _clean(verdict)

        question_lines = (
            _wrap(measure, question_text, self._font(SIZES["question"]), TEXT_WIDTH)
            if question_text
            else []
        )
        question_height = (22 + 30 + 8 + len(question_lines) * 46 + 22) if question_lines else 0
        paragraphs = [line for line in body_text.split("\n") if _clean(line)]
        body_rows: list[tuple[list[str], int]] = []
        for paragraph in paragraphs:
            lines = _wrap(measure, paragraph, self._font(SIZES["body"]), TEXT_WIDTH - 64)
            body_rows.append((lines, len(lines)))
        body_height = sum(
            count * BODY_LINE + (PARAGRAPH_GAP if index else 0)
            for index, (_, count) in enumerate(body_rows)
        )
        verdict_lines = (
            _wrap(
                measure,
                verdict_text,
                self._font(SIZES["verdict"]),
                CONTENT_RIGHT - VERDICT_LEFT - 28,
            )
            if verdict_text
            else []
        )
        verdict_height = (28 + max(1, len(verdict_lines)) * 48 + 28) if verdict_lines else 0

        height = _TOP_PAD + 92 + 30
        height += question_height
        if question_lines:
            height += 34
        height += 12 + 30 if body_rows else 0
        height += body_height
        if verdict_lines:
            height += 26 + verdict_height
        height += 30 + 1 + 22 + 40 + _BOTTOM_PAD

        canvas = Image.new("RGB", (CARD_WIDTH, height), PAPER)
        draw_bamboo_background(canvas, CARD_WIDTH)
        draw = ImageDraw.Draw(canvas)
        _draw_frame(draw, height)

        self._draw_header(draw, headline=headline, subtitle=subtitle, meta=meta)

        y = _TOP_PAD + 92 + 30
        if question_lines:
            _panel(draw, y, question_height, fill=PAPER_DEEP)
            draw.rectangle((MARGIN + 24, y + 18, MARGIN + 27, y + question_height - 18), fill=JADE)
            _text(draw, TEXT_LEFT, y + 18, "所问", self._font(SIZES["label"]), JADE)
            text_y = y + 56
            for line in question_lines:
                _text(draw, TEXT_LEFT, text_y, line, self._font(SIZES["question"]), INK)
                text_y += 46
            y += question_height + 34
            _rule(draw, y - 17)

        if body_rows:
            y += 12
            for index, (lines, _) in enumerate(body_rows):
                if index:
                    y += PARAGRAPH_GAP
                first = True
                for line in lines:
                    offset = 64 if first else 0
                    _text(
                        draw,
                        MARGIN + 8 + offset,
                        y,
                        line,
                        self._font(SIZES["body"]),
                        INK,
                    )
                    first = False
                    y += BODY_LINE
            y += 30

        if verdict_lines:
            _panel(draw, y, verdict_height, fill=JADE_PANEL)
            center_y = y + verdict_height // 2
            radius = 25
            draw.ellipse(
                (MARGIN + 22, center_y - radius, MARGIN + 22 + radius * 2, center_y + radius),
                fill=SEAL,
            )
            _text_at(
                draw,
                MARGIN + 22 + radius,
                center_y,
                "断",
                # The bundled heading font is a subset; 断 lives in the body font.
                self._font(25),
                "#FFFFFF",
            )
            text_y = center_y - (len(verdict_lines) * 48) // 2
            for line in verdict_lines:
                _text(draw, VERDICT_LEFT, text_y, line, self._font(SIZES["verdict"]), JADE)
                text_y += 48
            y += verdict_height

        footer_y = height - _BOTTOM_PAD - 40
        _rule(draw, footer_y)
        _text(draw, MARGIN, footer_y + 16, footer, self._font(SIZES["footer"]), MUTED)
        _text_right(
            draw, CONTENT_RIGHT, footer_y + 16, footer_note, self._font(SIZES["footer"]), MUTED
        )

        path = temp_output_path(output_path)
        try:
            canvas.save(path, "PNG", optimize=True)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise ChartRenderError(f"保存六爻解卦笺失败：{exc}") from exc
        return path

    def _draw_header(
        self, draw: ImageDraw.ImageDraw, *, headline: str, subtitle: str, meta: str
    ) -> None:
        top = _TOP_PAD
        draw_taiji_logo(draw, MARGIN, top + 8, width=76, height=76)

        title_font = self._font(SIZES["title"], heading=True)
        _text(draw, MARGIN + 116, top + 2, headline, title_font, JADE)
        if _clean(subtitle):
            _text(draw, MARGIN + 118, top + 62, _clean(subtitle), self._font(SIZES["meta"]), MUTED)
        if _clean(meta):
            _text_right(
                draw, CONTENT_RIGHT, top + 6, _clean(meta), self._font(SIZES["meta"]), MUTED
            )


def _draw_frame(draw: ImageDraw.ImageDraw, height: int) -> None:
    draw.rectangle((30, 30, CARD_WIDTH - 30, height - 30), outline=FRAME, width=1)
    draw.rectangle((38, 38, CARD_WIDTH - 38, height - 38), outline=FRAME, width=1)


def _panel(draw: ImageDraw.ImageDraw, y: int, height: int, *, fill: str) -> None:
    draw.rounded_rectangle((MARGIN, y, CONTENT_RIGHT, y + height), radius=10, fill=fill)


def _rule(draw: ImageDraw.ImageDraw, y: int) -> None:
    draw.line((MARGIN, y, CONTENT_RIGHT, y), fill=RULE, width=1)
    center = CARD_WIDTH // 2
    draw.ellipse((center - 3, y - 3, center + 3, y + 3), fill=RULE)


def _text(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font, color: str) -> None:
    draw.text((x, y), text, font=font, fill=color, anchor="lt")


def _text_at(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font, color: str) -> None:
    draw.text((x, y), text, font=font, fill=color, anchor="mm")


def _text_right(draw: ImageDraw.ImageDraw, x: int, y: int, text: str, font, color: str) -> None:
    draw.text((x, y), text, font=font, fill=color, anchor="rt")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def _clean_block(value: Any) -> str:
    """Collapse spaces inside each line but keep line breaks as paragraph marks."""
    text = str(value or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line for line in (" ".join(raw.split()) for raw in text.split("\n")) if line)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    """Wrap CJK text, letting trailing punctuation hang instead of starting a line."""
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        width = draw.textlength(candidate, font=font)
        if not current or width <= max_width:
            current = candidate
            continue
        if char in _CLOSING and width <= max_width + font.size:
            current = candidate
            continue
        lines.append(current)
        current = char
    if current:
        lines.append(current)
    return lines or [""]
