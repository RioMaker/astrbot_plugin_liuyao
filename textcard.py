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
