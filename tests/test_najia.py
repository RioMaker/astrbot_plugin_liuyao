from __future__ import annotations

from itertools import product
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from najia import palace_for_bits, relatives_for_bits  # noqa: E402


def test_qian_najia_and_six_relatives_match_classic_example() -> None:
    result = relatives_for_bits((1, 1, 1, 1, 1, 1))

    assert result.palace == "乾"
    assert result.palace_element == "金"
    assert result.palace_stage == "本宫"
    assert [line.label for line in result.lines] == [
        "子孙子水",
        "妻财寅木",
        "父母辰土",
        "官鬼午火",
        "兄弟申金",
        "父母戌土",
    ]


def test_eight_palaces_cover_all_64_hexagrams_once() -> None:
    counts = {name: 0 for name in "乾兑离震巽坎艮坤"}
    stages: set[tuple[str, str]] = set()

    for bits in product((0, 1), repeat=6):
        palace, stage = palace_for_bits(bits)
        counts[palace] += 1
        stages.add((palace, stage))

    assert set(counts.values()) == {8}
    assert len(stages) == 64


def test_changed_relatives_keep_primary_palace_element() -> None:
    changed = relatives_for_bits(
        (0, 1, 1, 1, 1, 1),
        reference_element="金",
    )

    assert changed.lines[0].label == "父母丑土"
    assert changed.lines[1].label == "子孙亥水"
    assert changed.lines[2].label == "兄弟酉金"
