"""Render a detailed six-line divination chart as a local PNG."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import tempfile
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from .corpus import ZhouyiCorpus
    from .divination import CastResult, LINE_NAMES
    from .najia import HexagramRelatives, relatives_for_bits
except ImportError:  # pragma: no cover - direct local execution
    from corpus import ZhouyiCorpus
    from divination import CastResult, LINE_NAMES
    from najia import HexagramRelatives, relatives_for_bits


WIDTH = 1240
MARGIN = 64
PAPER = "#F4E9D1"
INK = "#25231F"
MUTED = "#706657"
ACCENT = "#8D2F23"
JADE = "#294B3F"
GOLD = "#B88A3B"
PANEL = "#FBF5E7"
LINE_LABELS = {1: "初爻", 2: "二爻", 3: "三爻", 4: "四爻", 5: "五爻", 6: "上爻"}
BUNDLED_FONT_PATH = (
    Path(__file__).resolve().parent
    / "assets"
    / "fonts"
    / "NotoSansCJKsc-Regular.otf"
)


class ChartRenderError(RuntimeError):
    """Raised when a chart cannot be rendered."""


class LiuyaoImageRenderer:
    """Draw a readable, self-contained 六爻 chart without network access."""

    def __init__(
        self,
        corpus: ZhouyiCorpus,
        *,
        font_path: str | Path | None = None,
    ):
        self.corpus = corpus
        custom_font = Path(font_path).expanduser() if font_path else None
        if custom_font is not None and custom_font.is_file():
            self._font_regular = custom_font
            self._font_bold = custom_font
        else:
            self._font_regular = self._find_font(bold=False)
            self._font_bold = self._find_font(bold=True)

    def render(
        self,
        cast: CastResult,
        *,
        caster_name: str,
        caster_id: str,
        group_id: str,
        intent_label: str,
        question: str,
        method: str,
        cast_at: datetime,
        agent_name: str = "AI助手",
        ai_comment: str = "",
        comment_title: str = "AI短评",
        output_path: str | Path | None = None,
    ) -> Path:
        primary = self.corpus.get(cast.primary_number)
        changed = self.corpus.get(cast.changed_number)
        moving = cast.moving_lines
        primary_relatives = relatives_for_bits(cast.primary_bits)
        changed_relatives = relatives_for_bits(
            cast.changed_bits,
            reference_element=primary_relatives.palace_element,
        )

        fonts = {
            "title": self._font(54, bold=True),
            "subtitle": self._font(28),
            "section": self._font(31, bold=True),
            "body": self._font(25),
            "small": self._font(21),
            "line": self._font(23, bold=True),
            "symbol": self._font(58),
        }
        measure = ImageDraw.Draw(Image.new("RGB", (WIDTH, 32), PAPER))
        question_lines = self._wrap(
            measure,
            f"所问：{self._clean(question) or '未填写具体问题'}",
            fonts["body"],
            WIDTH - 2 * MARGIN - 48,
        )
        comment_text = (
            f"{self._clean(agent_name) or 'AI助手'}："
            f"{self._clean(ai_comment) or '卦象已成，当察动静、辨时位而取其宜。'}"
        )
        comment_lines = self._wrap(
            measure,
            comment_text,
            fonts["body"],
            WIDTH - 2 * MARGIN - 176,
        )
        detail_rows = self._detail_rows(primary, changed, cast)
        detail_lines: list[tuple[str, bool]] = []
        for label, value in detail_rows:
            wrapped = self._wrap(
                measure,
                f"{label}：{value}",
                fonts["body"],
                WIDTH - 2 * MARGIN - 48,
            )
            detail_lines.extend(
                (line, index == 0) for index, line in enumerate(wrapped)
            )

        meta_height = 196 + max(1, len(question_lines)) * 38
        comment_height = 96 + max(1, len(comment_lines)) * 38
        diagram_height = 540
        details_height = 112 + len(detail_lines) * 39
        canvas_height = (
            156
            + meta_height
            + comment_height
            + diagram_height
            + details_height
            + 100
        )
        canvas = Image.new("RGB", (WIDTH, canvas_height), PAPER)
        draw = ImageDraw.Draw(canvas)
        self._draw_background(draw, canvas_height)

        y = self._draw_header(draw, primary, changed, moving, cast_at, fonts)
        y = self._draw_meta(
            draw,
            y,
            meta_height,
            caster_name=caster_name,
            caster_id=caster_id,
            group_id=group_id,
            intent_label=intent_label,
            method=method,
            question_lines=question_lines,
            fonts=fonts,
        )
        y = self._draw_ai_comment(
            draw,
            y,
            comment_height,
            comment_lines,
            comment_title,
            fonts,
        )
        y = self._draw_diagram(
            draw,
            y,
            diagram_height,
            cast,
            primary,
            changed,
            primary_relatives,
            changed_relatives,
            fonts,
        )
        y = self._draw_details(
            draw,
            y,
            details_height,
            detail_lines,
            fonts,
        )
        draw.text(
            (MARGIN, y + 28),
            "六爻纳甲 · 以象明理 · 以变察时",
            font=fonts["small"],
            fill=MUTED,
        )
        draw.text(
            (WIDTH - MARGIN, y + 28),
            "六爻排盘",
            font=fonts["small"],
            fill=MUTED,
            anchor="ra",
        )

        path = self._output_path(output_path)
        try:
            canvas.save(path, "PNG", optimize=True)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise ChartRenderError(f"保存六爻信息图失败：{exc}") from exc
        return path

    @staticmethod
    def _draw_background(draw: ImageDraw.ImageDraw, height: int) -> None:
        draw.rectangle((0, 0, WIDTH, height), fill=PAPER)
        for y in range(0, height, 72):
            shade = "#E9D9B9" if (y // 72) % 2 == 0 else "#EFE1C6"
            draw.line((0, y, WIDTH, y), fill=shade, width=1)
        draw.rectangle((18, 18, WIDTH - 18, height - 18), outline=GOLD, width=2)

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        primary: dict[str, Any],
        changed: dict[str, Any],
        moving: tuple[int, ...],
        cast_at: datetime,
        fonts: dict[str, ImageFont.FreeTypeFont],
    ) -> int:
        draw.rounded_rectangle(
            (32, 24, WIDTH - 32, 144),
            radius=24,
            fill=JADE,
        )
        draw.text((MARGIN, 46), "六爻排盘", font=fonts["title"], fill="#FFF8E8")
        transition = (
            f"本卦 {primary['name']}  →  之卦 {changed['name']}"
            if moving
            else f"本卦 {primary['name']} · 静卦"
        )
        draw.text(
            (WIDTH - MARGIN, 53),
            transition,
            font=fonts["subtitle"],
            fill="#F1D59A",
            anchor="ra",
        )
        draw.text(
            (WIDTH - MARGIN, 98),
            cast_at.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            font=fonts["small"],
            fill="#D8DFD8",
            anchor="ra",
        )
        return 164

    def _draw_meta(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        height: int,
        *,
        caster_name: str,
        caster_id: str,
        group_id: str,
        intent_label: str,
        method: str,
        question_lines: list[str],
        fonts: dict[str, ImageFont.FreeTypeFont],
    ) -> int:
        bottom = y + height - 16
        draw.rounded_rectangle(
            (MARGIN, y, WIDTH - MARGIN, bottom),
            radius=20,
            fill=PANEL,
            outline="#D2BE98",
            width=2,
        )
        left = MARGIN + 24
        right = WIDTH // 2 + 12
        safe_name = (self._clean(caster_name) or "群友")[:20]
        safe_id = self._clean(caster_id) or "未知"
        safe_group = self._clean(group_id) or "未知"
        draw.text(
            (left, y + 24),
            f"起卦人：{safe_name}（QQ {safe_id}）",
            font=fonts["body"],
            fill=INK,
        )
        draw.text(
            (right, y + 24),
            f"群号：{safe_group}",
            font=fonts["body"],
            fill=INK,
        )
        draw.text(
            (left, y + 68),
            f"方向：{self._clean(intent_label) or '综合'}",
            font=fonts["body"],
            fill=INK,
        )
        draw.text(
            (right, y + 68),
            f"方式：{self._clean(method)}",
            font=fonts["body"],
            fill=INK,
        )
        question_y = y + 118
        for line in question_lines:
            draw.text((left, question_y), line, font=fonts["body"], fill=ACCENT)
            question_y += 38
        return y + height

    def _draw_ai_comment(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        height: int,
        comment_lines: list[str],
        comment_title: str,
        fonts: dict[str, ImageFont.FreeTypeFont],
    ) -> int:
        top = y + 4
        bottom = y + height - 16
        draw.rounded_rectangle(
            (MARGIN, top, WIDTH - MARGIN, bottom),
            radius=20,
            fill="#E7EFE8",
            outline="#A9BDAF",
            width=2,
        )
        draw.text(
            (MARGIN + 24, top + 22),
            self._clean(comment_title) or "排盘提示",
            font=fonts["section"],
            fill=JADE,
        )
        text_y = top + 70
        for line in comment_lines:
            draw.text(
                (MARGIN + 24, text_y),
                line,
                font=fonts["body"],
                fill=ACCENT,
            )
            text_y += 38
        return y + height
    def _draw_diagram(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        height: int,
        cast: CastResult,
        primary: dict[str, Any],
        changed: dict[str, Any],
        primary_relatives: HexagramRelatives,
        changed_relatives: HexagramRelatives,
        fonts: dict[str, ImageFont.FreeTypeFont],
    ) -> int:
        top = y + 4
        bottom = y + height - 16
        draw.rounded_rectangle(
            (MARGIN, top, WIDTH - MARGIN, bottom),
            radius=20,
            fill="#F8EFD9",
            outline="#D2BE98",
            width=2,
        )

        primary_title = (
            f"本卦 · 第{primary['number']}卦 {primary['name']}"
        )
        changed_title = (
            f"之卦 · 第{changed['number']}卦 {changed['name']}"
            if cast.moving_lines
            else "静卦 · 无动爻"
        )
        draw.text((176, top + 34), primary_title, font=fonts["subtitle"], fill=INK)
        draw.text((728, top + 34), changed_title, font=fonts["subtitle"], fill=INK)
        draw.text(
            (176, top + 74),
            (
                f"{primary['upper_trigram']}上{primary['lower_trigram']}下 · "
                f"{primary_relatives.palace}宫{primary_relatives.palace_element}"
                f"（{primary_relatives.palace_stage}）"
            ),
            font=fonts["small"],
            fill=MUTED,
        )
        changed_note = (
            (
                f"{changed['upper_trigram']}上{changed['lower_trigram']}下 · "
                f"六亲从本卦{primary_relatives.palace}宫"
                f"{primary_relatives.palace_element}"
            )
            if cast.moving_lines
            else "本卦不变 · 六亲沿用本卦"
        )
        draw.text((728, top + 74), changed_note, font=fonts["small"], fill=MUTED)

        first_y = top + 138
        for row, position in enumerate(range(6, 0, -1)):
            line_y = first_y + row * 58
            value = cast.lines[position - 1]
            is_moving = value in {6, 9}
            draw.text(
                (MARGIN + 24, line_y - 12),
                LINE_LABELS[position],
                font=fonts["line"],
                fill=ACCENT if is_moving else MUTED,
            )
            self._draw_line(
                draw,
                176,
                line_y,
                cast.primary_bits[position - 1],
                moving=is_moving,
            )
            draw.text(
                (430, line_y - 12),
                (
                    f"{value} {LINE_NAMES[value]} · "
                    f"{primary_relatives.lines[position - 1].label}"
                ),
                font=fonts["small"],
                fill=ACCENT if is_moving else INK,
            )
            self._draw_line(
                draw,
                728,
                line_y,
                cast.changed_bits[position - 1],
                moving=False,
            )
            draw.text(
                (980, line_y - 12),
                changed_relatives.lines[position - 1].label,
                font=fonts["small"],
                fill=ACCENT if is_moving else MUTED,
            )
            if is_moving:
                marker = "○" if value == 9 else "×"
                draw.text(
                    (688, line_y - 16),
                    marker,
                    font=fonts["subtitle"],
                    fill=ACCENT,
                    anchor="ma",
                )
        return y + height

    def _draw_details(
        self,
        draw: ImageDraw.ImageDraw,
        y: int,
        height: int,
        detail_lines: list[tuple[str, bool]],
        fonts: dict[str, ImageFont.FreeTypeFont],
    ) -> int:
        top = y + 4
        bottom = y + height - 16
        draw.rounded_rectangle(
            (MARGIN, top, WIDTH - MARGIN, bottom),
            radius=20,
            fill=PANEL,
            outline="#D2BE98",
            width=2,
        )
        draw.text(
            (MARGIN + 24, top + 22),
            "卦辞与动爻",
            font=fonts["section"],
            fill=JADE,
        )
        text_y = top + 76
        for line, starts_row in detail_lines:
            draw.text(
                (MARGIN + 24, text_y),
                line,
                font=fonts["body"],
                fill=ACCENT if starts_row and line.startswith("动爻") else INK,
            )
            text_y += 39
        return y + height

    @staticmethod
    def _draw_line(
        draw: ImageDraw.ImageDraw,
        x: int,
        y: int,
        bit: int,
        *,
        moving: bool,
    ) -> None:
        color = ACCENT if moving else INK
        if bit:
            draw.rounded_rectangle((x, y, x + 224, y + 18), radius=4, fill=color)
        else:
            draw.rounded_rectangle((x, y, x + 94, y + 18), radius=4, fill=color)
            draw.rounded_rectangle((x + 130, y, x + 224, y + 18), radius=4, fill=color)

    def _detail_rows(
        self,
        primary: dict[str, Any],
        changed: dict[str, Any],
        cast: CastResult,
    ) -> list[tuple[str, str]]:
        rows = [("本卦卦辞", str(primary["judgment"]))]
        for position in cast.moving_lines:
            rows.append(
                (
                    f"动爻·{LINE_LABELS[position]}",
                    str(primary["lines"][position - 1]),
                )
            )
        if len(cast.moving_lines) == 6 and primary.get("extra_lines"):
            rows.append(("全爻皆变", "；".join(primary["extra_lines"])))
        if cast.moving_lines:
            rows.append(("之卦卦辞", str(changed["judgment"])))
        else:
            rows.append(("取用提示", "无动爻，以本卦卦辞与整体卦象为主。"))
        return rows

    @staticmethod
    def _wrap(
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
    ) -> list[str]:
        lines: list[str] = []
        for paragraph in (text or "").splitlines() or [""]:
            current = ""
            for char in paragraph:
                candidate = current + char
                if current and draw.textlength(candidate, font=font) > max_width:
                    lines.append(current)
                    current = char
                else:
                    current = candidate
            lines.append(current)
        return lines or [""]

    def _font(self, size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
        path = self._font_bold if bold else self._font_regular
        return ImageFont.truetype(str(path), size=size)

    @staticmethod
    def _find_font(*, bold: bool) -> Path:
        regular = [
            BUNDLED_FONT_PATH,
            Path("C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("C:/Windows/Fonts/simsun.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
        ]
        bold_candidates = [
            BUNDLED_FONT_PATH,
            Path("C:/Windows/Fonts/msyhbd.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
            Path("/System/Library/Fonts/PingFang.ttc"),
        ]
        candidates = bold_candidates + regular if bold else regular
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        kind = "粗体" if bold else "常规"
        raise ChartRenderError(f"未找到可用于六爻信息图的中文{kind}字体")

    @staticmethod
    def _output_path(output_path: str | Path | None) -> Path:
        if output_path is not None:
            path = Path(output_path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return path
        fd, raw_path = tempfile.mkstemp(prefix="liuyao_chart_", suffix=".png")
        os.close(fd)
        return Path(raw_path).resolve()

    @staticmethod
    def _clean(value: str) -> str:
        return " ".join(str(value or "").replace("\x00", "").split())





