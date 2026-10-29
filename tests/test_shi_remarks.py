from __future__ import annotations

import json
import sys
from itertools import product
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import shi_remarks  # noqa: E402
from divination import CastResult  # noqa: E402
from paipan import chart_for  # noqa: E402


def test_presets_cover_all_hexagrams_and_positions_with_distinct_short_text():
    payload = json.loads(shi_remarks.REMARKS_PATH.read_text(encoding="utf-8"))
    assert "非古籍原文" in payload["origin"]
    assert {row["number"] for row in payload["hexagrams"]} == set(range(1, 65))
    texts = []
    for row in payload["hexagrams"]:
        assert len(row["remarks"]) == 6
        for position, expected in enumerate(row["remarks"], 1):
            actual = shi_remarks.get_shi_remark(row["number"], position)
            assert actual == expected
            assert 8 <= len(actual) <= 24
            assert actual != shi_remarks.FALLBACK_REMARK
            texts.append(actual)
    assert len(texts) == len(set(texts)) == 384


def test_all_actual_palace_positions_have_a_remark_and_use_primary_hexagram():
    seen = set()
    for bits in product((0, 1), repeat=6):
        # All moving lines make the changed hexagram different from the primary.
        cast = CastResult(tuple(9 if bit else 6 for bit in bits))
        chart = chart_for(cast)
        assert cast.primary_number != cast.changed_number
        remark = shi_remarks.get_shi_remark(cast.primary_number, chart["shi"])
        assert remark != shi_remarks.FALLBACK_REMARK
        assert remark != shi_remarks.get_shi_remark(cast.changed_number, chart["shi"])
        seen.add(cast.primary_number)
    assert seen == set(range(1, 65))


@pytest.mark.parametrize(
    ("number", "position"),
    [(0, 1), (65, 1), (1, 0), (1, 7), (True, 1), (1, False), (None, 1), (1, "1")],
)
def test_invalid_coordinates_fall_back_without_breaking_chart(number, position):
    assert shi_remarks.get_shi_remark(number, position) == shi_remarks.FALLBACK_REMARK


@pytest.mark.parametrize("contents", [None, "{", "{}", '{"schema_version":1,"hexagrams":[]}'])
def test_missing_or_damaged_presets_do_not_break_chart(tmp_path, monkeypatch, contents):
    path = tmp_path / "remarks.json"
    if contents is not None:
        path.write_text(contents, encoding="utf-8")
    shi_remarks._load_remarks.cache_clear()
    monkeypatch.setattr(shi_remarks, "REMARKS_PATH", path)
    try:
        assert shi_remarks.get_shi_remark(1, 6) == shi_remarks.FALLBACK_REMARK
    finally:
        shi_remarks._load_remarks.cache_clear()


def test_loaded_presets_are_reused_without_reading_file_again(monkeypatch):
    shi_remarks._load_remarks.cache_clear()
    first = shi_remarks.get_shi_remark(56, 1)

    def fail_read(*args, **kwargs):
        raise AssertionError("already-loaded presets should not be read again")

    monkeypatch.setattr(Path, "read_text", fail_read)
    try:
        assert shi_remarks.get_shi_remark(56, 1) == first
        assert shi_remarks.get_shi_remark(1, 6) != shi_remarks.FALLBACK_REMARK
    finally:
        shi_remarks._load_remarks.cache_clear()
