from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from corpus import ZhouyiCorpus  # noqa: E402
from divination import CastResult  # noqa: E402
from renderer import LiuyaoImageRenderer  # noqa: E402


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
