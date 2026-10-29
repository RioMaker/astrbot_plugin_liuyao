from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from corpus import ZhouyiCorpus  # noqa: E402
from divination import CastResult  # noqa: E402
from renderer import ELEMENT_COLORS, INK, SPIRIT_COLORS, LiuyaoImageRenderer  # noqa: E402


@pytest.mark.parametrize("saved_position", [None, 2])
def test_default_remark_uses_primary_hexagram_and_actual_chart_position(
    tmp_path, monkeypatch, saved_position
):
    from paipan import chart_for

    cast = CastResult((9, 7, 7, 7, 7, 7))
    chart = chart_for(cast)
    if saved_position is not None:
        chart["shi"] = saved_position
        cast = CastResult(cast.lines, chart=chart)
    requested = []
    texts = []

    def preset(number, position):
        requested.append((number, position))
        return "龙可飞天，莫顶破天。"

    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        texts.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr("renderer.get_shi_remark", preset)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    LiuyaoImageRenderer(ZhouyiCorpus(ROOT / "data/zhouyi.json")).render(
        cast,
        caster_name="测试",
        caster_id="20002",
        group_id="10001",
        intent_label="综合",
        question="测试主卦世位",
        method="手动铜币",
        cast_at=None,
        output_path=tmp_path / "remark.png",
    )
    assert cast.primary_number != cast.changed_number
    assert requested == [(cast.primary_number, chart["shi"])]
    assert "龙可飞天，莫顶破天。" in texts
    assert any(text.startswith("可可子の短评 · 乾卦 · 世居") for text in texts)
    assert not any("AI助手：" in text or "卦象已成" in text for text in texts)


def test_renderer_creates_detailed_png(tmp_path) -> None:
    corpus = ZhouyiCorpus(ROOT / "data" / "zhouyi.json")
    renderer = LiuyaoImageRenderer(corpus)
    cast = CastResult((7, 8, 9, 6, 7, 8))
    output = tmp_path / "chart.png"

    result = renderer.render(
        cast,
        caster_name="测试群友",
        caster_id="20002",
        group_id="10001",
        intent_label="事业",
        question="今年是否适合换工作，应该重点观察哪些信号？",
        method="即时天机（三枚铜币等概率模拟）",
        cast_at=datetime(
            2026,
            8,
            21,
            12,
            34,
            56,
            tzinfo=timezone(timedelta(hours=8)),
        ),
        output_path=output,
    )

    assert result == output.resolve()
    assert output.stat().st_size > 30_000
    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.width == 1240
        assert image.height > 1200


def test_renderer_uses_bundled_font_when_custom_path_is_missing(tmp_path) -> None:
    corpus = ZhouyiCorpus(ROOT / "data" / "zhouyi.json")
    renderer = LiuyaoImageRenderer(
        corpus,
        font_path=tmp_path / "missing-font.ttf",
    )

    expected = ROOT / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"
    assert renderer._font_regular == expected
    assert renderer._font_bold == expected


def test_renderer_detail_rows_include_each_moving_line() -> None:
    corpus = ZhouyiCorpus(ROOT / "data" / "zhouyi.json")
    renderer = LiuyaoImageRenderer(corpus)
    cast = CastResult((9, 8, 7, 6, 7, 8))
    primary = corpus.get(cast.primary_number)
    changed = corpus.get(cast.changed_number)

    rows = renderer._detail_rows(primary, changed, cast)

    labels = [label for label, _ in rows]
    assert "本卦卦辞" in labels
    assert "动爻·初爻" in labels
    assert "动爻·四爻" in labels
    assert "之卦卦辞" in labels


def test_element_colors_survive_moving_line_highlight_without_legend(tmp_path, monkeypatch):
    renderer = LiuyaoImageRenderer(ZhouyiCorpus(ROOT / "data/zhouyi.json"))
    drawn = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        drawn.append((text, kwargs.get("fill")))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    renderer.render(
        CastResult((6, 8, 7, 7, 7, 7)),
        caster_name="排盘验证",
        caster_id="20002",
        group_id="10001",
        intent_label="事业",
        question="这个项目能否按期签约？",
        method="手动铜币",
        cast_at=datetime(2026, 10, 2, 12, tzinfo=timezone(timedelta(hours=8))),
        output_path=tmp_path / "colors.png",
    )
    # The moving earth line retains its element color; hidden/changed lines do too.
    assert ("父母丙辰土", ELEMENT_COLORS["土"]) in drawn
    assert ("妻财寅木", ELEMENT_COLORS["木"]) in drawn
    assert ("子孙己亥水", ELEMENT_COLORS["水"]) in drawn
    # A pillar may contain two different elements; its date suffix stays neutral.
    assert ("庚", ELEMENT_COLORS["金"]) in drawn
    assert ("午", ELEMENT_COLORS["火"]) in drawn
    assert ("己", ELEMENT_COLORS["土"]) in drawn
    assert ("酉", ELEMENT_COLORS["金"]) in drawn
    assert ("年", INK) in drawn and ("时", INK) in drawn
    assert all((spirit, color) in drawn for spirit, color in SPIRIT_COLORS.items())
    assert not any(text in ELEMENT_COLORS for text, _ in drawn)


@pytest.mark.parametrize("known_time", [True, False])
def test_long_content_and_all_moving_lines_stay_inside_canvas(tmp_path, monkeypatch, known_time):
    renderer = LiuyaoImageRenderer(ZhouyiCorpus(ROOT / "data/zhouyi.json"))
    bounds = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        bounds.append(self.textbbox(xy, text, font=kwargs["font"], anchor=kwargs.get("anchor")))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    output = renderer.render(
        CastResult((9, 9, 9, 9, 9, 9)),
        caster_name="很长的用户昵称" * 15,
        caster_id="20002",
        group_id="10001",
        intent_label="射覆",
        question="盒子里的物品有什么形状和颜色？" * 25,
        method="手动铜币",
        cast_at=datetime(2026, 10, 2, 12, tzinfo=timezone(timedelta(hours=8)))
        if known_time
        else None,
        ai_comment=("第一段完整分析。" * 25 + "\n") * 5 + "最后一段。",
        comment_title="射覆分析",
        output_path=tmp_path / "long.png",
    )
    with Image.open(output) as image:
        assert image.height > 3000
        assert all(0 <= left <= right <= image.width for left, _, right, _ in bounds)
        assert all(0 <= top <= bottom <= image.height for _, top, _, bottom in bounds)
