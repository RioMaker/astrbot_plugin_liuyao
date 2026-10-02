"""Render an ivory-and-jade report with semantic five-element text colors."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from .corpus import ZhouyiCorpus
    from .divination import CastResult
    from .paipan import beijing_time, chart_for
except ImportError:  # pragma: no cover - direct local execution
    from corpus import ZhouyiCorpus
    from divination import CastResult
    from paipan import beijing_time, chart_for

WIDTH = 1240
MARGIN = 60
PAPER = "#F6F3EA"
INK = "#252C2A"
MUTED = "#737971"
ACCENT = "#B34F40"
JADE = "#234A3B"
PANEL = "#EAEEDF"
RULE = "#D5DCCB"
ELEMENT_COLORS = {
    "木": "#317450",
    "火": "#B54D3C",
    "土": "#986039",
    "金": "#80712E",
    "水": "#3F6691",
}
LINE_LABELS = {1: "初爻", 2: "二爻", 3: "三爻", 4: "四爻", 5: "五爻", 6: "上爻"}
TRIGRAM_IMAGES = {
    "乾": "天",
    "兑": "泽",
    "离": "火",
    "震": "雷",
    "巽": "风",
    "坎": "水",
    "艮": "山",
    "坤": "地",
}
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
BUNDLED_FONT_PATH = FONT_DIR / "NotoSansCJKsc-Regular.otf"
TITLE_FONT_PATH = FONT_DIR / "LiuyaoEditorialSerif.otf"


class ChartRenderError(RuntimeError):
    """Raised when a chart cannot be rendered."""


class LiuyaoImageRenderer:
    """Draw a self-contained report; no network access or five-element legend."""

    def __init__(self, corpus: ZhouyiCorpus, *, font_path: str | Path | None = None):
        self.corpus = corpus
        custom = Path(font_path).expanduser() if font_path else None
        if custom is not None and custom.is_file():
            self._font_regular = self._font_bold = custom
            self._font_heading = custom
        else:
            self._font_regular = self._find_font(bold=False)
            self._font_bold = self._find_font(bold=True)
            self._font_heading = TITLE_FONT_PATH if TITLE_FONT_PATH.is_file() else self._font_bold

    def _heading(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self._font_heading), size=size)

    @staticmethod
    def _full_name(row: dict[str, Any]) -> str:
        upper, lower = row["upper_trigram"], row["lower_trigram"]
        upper = upper.translate(str.maketrans("兌離", "兑离"))
        lower = lower.translate(str.maketrans("兌離", "兑离"))
        if upper == lower:
            return f"{upper}为{TRIGRAM_IMAGES[upper]}"
        return TRIGRAM_IMAGES[upper] + TRIGRAM_IMAGES[lower] + row["name"]

    @staticmethod
    def _text(draw, x, y, text, font, color=INK):
        # Top anchors make measured line heights independent of font ascenders.
        draw.text((x, y), text, font=font, fill=color, anchor="lt")

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
        cast_at: datetime | None,
        agent_name: str = "AI助手",
        ai_comment: str = "",
        comment_title: str = "AI短评",
        output_path: str | Path | None = None,
    ) -> Path:
        primary = self.corpus.get(cast.primary_number)
        changed = self.corpus.get(cast.changed_number)
        chart = chart_for(cast, cast_at)
        calendar = chart["calendar"]
        if calendar.get("cast_at"):
            cast_at = datetime.fromisoformat(calendar["cast_at"])
        if cast_at is not None and cast_at.tzinfo is not None:
            cast_at = beijing_time(cast_at)
        f = {
            "title": self._heading(76),
            "name": self._heading(51),
            "section": self._heading(35),
            "pillar": self._heading(40),
            "question": self._font(36),
            "body": self._font(28),
            "data": self._font(27),
            "small": self._font(23),
            "tiny": self._font(21),
        }
        measure = ImageDraw.Draw(Image.new("RGB", (WIDTH, 32), PAPER))

        def wrap(text, font, width):
            return self._wrap(measure, text, font, width)

        question_lines = wrap(self._clean(question) or "未填写具体问题", f["question"], 992)
        meta_lines = wrap(
            f"{self._clean(intent_label) or '综合'} · {self._clean(method)}", f["small"], 992
        )
        identity_lines = wrap(
            f"{self._clean(caster_name) or '群友'} / QQ {self._clean(caster_id) or '未知'}"
            f" / 群 {self._clean(group_id) or '未知'}",
            f["small"],
            992,
        )
        question_height = max(
            108, len(question_lines) * 50 + (len(meta_lines) + len(identity_lines)) * 34 + 28
        )
        date_lines = (
            []
            if calendar["status"] == "已计算"
            else wrap("日月干支 / 旬空 / 六神" + calendar["status"], f["small"], 1064)
        )
        calendar_height = 162 if not date_lines else 56 + len(date_lines) * 34
        details = [
            (label, wrap(value, f["body"], 876))
            for label, value in self._detail_rows(primary, changed, cast)
        ]
        details_height = 76 + sum(max(1, len(lines)) * 40 + 24 for _, lines in details)
        comment_lines = wrap(
            f"{self._clean(agent_name) or 'AI助手'}："
            + (
                str(ai_comment or "").replace("\x00", "").strip()
                or "卦象已成，当察动静、辨时位而取其宜。"
            ),
            f["body"],
            1064,
        )
        comment_heading = wrap(self._clean(comment_title) or "排盘提示", f["body"], 1064)
        comment_height = 48 + len(comment_heading) * 40 + len(comment_lines) * 40
        # Each section owns its measured height. Long questions/射覆分析 grow the canvas.
        height = (
            308 + question_height + calendar_height + 606 + details_height + comment_height + 94
        )
        canvas = Image.new("RGB", (WIDTH, height), PAPER)
        draw = ImageDraw.Draw(canvas)
        self._draw_header(draw, primary, changed, cast, chart, cast_at, f)
        y = 308
        draw.rounded_rectangle(
            (MARGIN, y, MARGIN + 78, y + question_height - 22), radius=6, fill=JADE
        )
        self._text(draw, MARGIN + 19, y + 20, "问", f["section"], PAPER)
        self._text(draw, MARGIN + 19, y + 66, "卦", f["section"], PAPER)
        text_y = y + 2
        for line in question_lines:
            self._text(draw, 166, text_y, line, f["question"], JADE)
            text_y += 50
        for line in meta_lines:
            self._text(draw, 166, text_y + 5, line, f["small"])
            text_y += 34
        for line in identity_lines:
            self._text(draw, 166, text_y + 5, line, f["small"], MUTED)
            text_y += 34
        y += question_height
        self._draw_calendar(draw, y, calendar_height, calendar, date_lines, f)
        y += calendar_height
        self._draw_chart(draw, y, cast, chart, f)
        y += 606
        self._section_heading(draw, y, "卦辞与动爻", f)
        text_y = y + 76
        for label, lines in details:
            row_height = len(lines) * 40 + 24
            if label.startswith("动爻") or label == "全爻皆变":
                draw.rectangle((MARGIN, text_y - 10, 286, text_y + row_height - 12), fill="#F5E8DF")
            self._text(
                draw,
                MARGIN + 16,
                text_y,
                label,
                f["small"],
                ACCENT if label.startswith("动爻") else JADE,
            )
            for index, line in enumerate(lines):
                self._text(draw, 304, text_y + index * 40, line, f["body"])
            draw.line(
                (MARGIN, text_y + row_height - 12, WIDTH - MARGIN, text_y + row_height - 12),
                fill=RULE,
                width=1,
            )
            text_y += row_height
        y += details_height
        draw.rounded_rectangle(
            (MARGIN, y, WIDTH - MARGIN, y + comment_height),
            radius=8,
            fill=PANEL,
            outline=RULE,
            width=1,
        )
        text_y = y + 24
        for line in comment_heading:
            self._text(draw, MARGIN + 28, text_y, line, f["body"], JADE)
            text_y += 40
        for line in comment_lines:
            self._text(draw, MARGIN + 28, text_y, line, f["body"])
            text_y += 40
        y += comment_height + 30
        footer = "北京时间 · 立春换年 · 节气换月 · 零点换日"
        x = (WIDTH - draw.textlength(footer, font=f["tiny"])) / 2
        self._text(draw, x, y, footer, f["tiny"], MUTED)
        path = self._output_path(output_path)
        try:
            canvas.save(path, "PNG", optimize=True)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise ChartRenderError(f"保存六爻信息图失败：{exc}") from exc
        return path

    def _draw_header(self, draw, primary, changed, cast, chart, cast_at, f):
        self._text(draw, MARGIN, 45, "六爻排盘", f["title"], JADE)
        stamp = cast_at.strftime("%Y-%m-%d %H:%M:%S") if cast_at else "原起卦时间未知"
        zone = "北京时间 UTC+8" if cast_at and cast_at.tzinfo else "原时间未注明时区"
        for y, text in ((54, stamp), (92, zone)):
            self._text(
                draw,
                WIDTH - MARGIN - draw.textlength(text, font=f["small"]),
                y,
                text,
                f["small"],
                MUTED,
            )
        title = self._full_name(primary)
        title += " → " + self._full_name(changed) if cast.moving_lines else " · 静卦"
        font = f["name"]
        while draw.textlength(title, font=font) > WIDTH - MARGIN * 2:
            font = self._heading(font.size - 1)
        self._text(draw, MARGIN, 160, title, font)
        self._text(
            draw,
            MARGIN,
            241,
            f"本卦 {primary['number']:02d} · {chart['palace']}宫{chart['palace_element']}"
            f" · {chart['palace_stage']}",
            f["body"],
            JADE,
        )
        subtitle = (
            f"之卦 {changed['number']:02d} · 六亲从本卦"
            if cast.moving_lines
            else "无动爻 · 以本卦为主"
        )
        self._text(
            draw,
            WIDTH - MARGIN - draw.textlength(subtitle, font=f["small"]),
            245,
            subtitle,
            f["small"],
            MUTED,
        )
        draw.line((MARGIN, 284, WIDTH - MARGIN, 284), fill="#9CAF9D", width=2)

    def _draw_calendar(self, draw, y, height, calendar, missing_lines, f):
        draw.rounded_rectangle(
            (MARGIN, y, WIDTH - MARGIN, y + height - 48), radius=8, fill=PANEL, outline=RULE
        )
        if missing_lines:
            for index, line in enumerate(missing_lines):
                self._text(draw, MARGIN + 28, y + 18 + index * 34, line, f["small"], MUTED)
            return
        span = (WIDTH - 2 * MARGIN) / 4
        for index, (key, label) in enumerate(
            (("year", "年"), ("month", "月"), ("day", "日"), ("hour", "时"))
        ):
            center = MARGIN + span * (index + 0.5)
            self._text(draw, center - 23, y + 14, label + "柱", f["small"], MUTED)
            text = calendar[key] + label
            self._text(
                draw,
                center - draw.textlength(text, font=f["pillar"]) / 2,
                y + 51,
                text,
                f["pillar"],
            )
            if index:
                draw.line((MARGIN + span * index, y + 24, MARGIN + span * index, y + 90), fill=RULE)
        note = (
            f"月建 {calendar['month_branch']} · 日辰 {calendar['day_branch']} · "
            f"{calendar['xun']}旬 · 旬空 {calendar['xun_kong']}"
        )
        self._text(
            draw, (WIDTH - draw.textlength(note, font=f["small"])) / 2, y + 130, note, f["small"]
        )

    def _draw_chart(self, draw, y, cast, chart, f):
        edges = (MARGIN, 154, 264, 420, 756, 840, WIDTH - MARGIN)
        draw.rounded_rectangle((MARGIN, y, WIDTH - MARGIN, y + 60), radius=7, fill=PANEL)
        for index, label in enumerate(("爻位", "六神", "伏神", "本卦", "世应", "之卦")):
            center = (edges[index] + edges[index + 1]) / 2
            self._text(
                draw,
                center - draw.textlength(label, font=f["data"]) / 2,
                y + 17,
                label,
                f["data"],
                JADE,
            )
        for index, position in enumerate(range(6, 0, -1)):
            top = y + 60 + index * 76
            base = top + 24
            line = chart["primary"][position - 1]
            changed = chart["changed"][position - 1]
            moving = position in cast.moving_lines
            if moving:
                draw.rectangle((MARGIN, top, WIDTH - MARGIN, top + 76), fill="#F6EAE3")
                draw.rectangle((MARGIN, top, MARGIN + 4, top + 76), fill=ACCENT)
            self._text(draw, 78, base, LINE_LABELS[position], f["data"])
            self._text(draw, 177, base, line["spirit"], f["data"])
            hidden = line["hidden"]
            self._text(
                draw,
                278 if hidden else 327,
                base,
                hidden["label"] if hidden else "—",
                f["data"],
                ELEMENT_COLORS.get(hidden["element"], INK) if hidden else MUTED,
            )
            self._draw_line(draw, 438, base + 6, cast.primary_bits[position - 1])
            self._text(
                draw,
                533,
                base,
                self._line_label(line),
                f["data"],
                ELEMENT_COLORS.get(line["element"], INK),
            )
            if moving:
                self._text(
                    draw,
                    727,
                    base - 1,
                    "○" if cast.lines[position - 1] == 9 else "×",
                    f["data"],
                    ACCENT,
                )
            if line["role"]:
                draw.rounded_rectangle((780, base - 6, 816, base + 35), radius=4, fill=JADE)
                self._text(draw, 784, base - 1, line["role"], f["data"], PAPER)
            else:
                self._text(draw, 785, base, "—", f["data"], MUTED)
            self._draw_line(draw, 860, base + 6, cast.changed_bits[position - 1])
            self._text(
                draw,
                958,
                base,
                self._line_label(changed),
                f["data"],
                ELEMENT_COLORS.get(changed["element"], INK),
            )
            draw.line((MARGIN, top + 76, WIDTH - MARGIN, top + 76), fill=RULE)
        for edge in edges:
            draw.line((edge, y + 7, edge, y + 516), fill=RULE)
        numbers = "一二三四五六"
        active = (
            "、".join(LINE_LABELS[p] for p in cast.moving_lines) + "发动"
            if cast.moving_lines
            else "无动爻"
        )
        note = f"世{numbers[chart['shi'] - 1]}应{numbers[chart['ying'] - 1]} · {active}"
        self._text(draw, MARGIN + 8, y + 538, note, f["small"])
        self._text(draw, MARGIN + 8, y + 575, "伏神按缺失六亲列候选", f["tiny"], MUTED)
        values = "六爻值（初→上）：" + " ".join(map(str, cast.lines))
        self._text(
            draw,
            WIDTH - MARGIN - draw.textlength(values, font=f["tiny"]),
            y + 575,
            values,
            f["tiny"],
            MUTED,
        )

    @staticmethod
    def _line_label(line):
        return f"{line['relative']}{line['stem']}{line['branch']}{line['element']}"

    @staticmethod
    def _draw_line(draw, x, y, bit):
        if bit:
            draw.rectangle((x, y, x + 78, y + 16), fill=INK)
        else:
            draw.rectangle((x, y, x + 31, y + 16), fill=INK)
            draw.rectangle((x + 47, y, x + 78, y + 16), fill=INK)

    def _section_heading(self, draw, y, title, f):
        draw.rectangle((MARGIN, y + 15, MARGIN + 5, y + 47), fill=JADE)
        self._text(draw, MARGIN + 24, y + 12, title, f["section"], JADE)
        end = MARGIN + 48 + draw.textlength(title, font=f["section"])
        draw.line((end, y + 34, WIDTH - MARGIN, y + 34), fill="#9CAF9D")

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
