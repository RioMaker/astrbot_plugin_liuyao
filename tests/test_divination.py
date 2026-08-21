from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from corpus import ZhouyiCorpus  # noqa: E402
from divination import (  # noqa: E402
    CastResult,
    ManualCastError,
    bits_to_hexagram_number,
    cast_instant,
    parse_intent_and_question,
    parse_manual_cast,
    render_diagram,
    split_manual_payload,
)
from reading import ReadingService  # noqa: E402


def test_manual_numeric_and_coin_notation_are_bottom_up() -> None:
    assert parse_manual_cast("7 8 9 6 7 8").lines == (7, 8, 9, 6, 7, 8)
    assert parse_manual_cast("789678").lines == (7, 8, 9, 6, 7, 8)
    assert parse_manual_cast(
        "正反反 正正反 正正正 反反反 正反反 正正反"
    ).lines == (7, 8, 9, 6, 7, 8)


def test_manual_input_rejects_wrong_line_count_and_symbols() -> None:
    with pytest.raises(ManualCastError):
        parse_manual_cast("7 8 9")
    with pytest.raises(ManualCastError):
        parse_manual_cast("正反X 正正反 正正正 反反反 正反反 正正反")


def test_split_manual_payload_preserves_intent_and_question() -> None:
    cast, rest = split_manual_payload("7 8 9 6 7 8 事业 是否适合换工作")
    assert cast == "7 8 9 6 7 8"
    assert rest == "事业 是否适合换工作"
    assert parse_intent_and_question(rest) == ("career", "是否适合换工作")


def test_instant_cast_uses_three_coin_probability_buckets() -> None:
    rolls = iter([0, 1, 4, 7, 3, 6])
    result = cast_instant(lambda _: next(rolls))
    assert result.lines == (6, 7, 8, 9, 7, 8)
    assert result.moving_lines == (1, 4)


def test_known_hexagram_mapping_and_change() -> None:
    assert bits_to_hexagram_number((1, 1, 1, 1, 1, 1)) == 1
    assert bits_to_hexagram_number((0, 0, 0, 0, 0, 0)) == 2
    assert bits_to_hexagram_number((1, 0, 0, 0, 1, 0)) == 3

    all_moving_qian = CastResult((9, 9, 9, 9, 9, 9))
    assert all_moving_qian.primary_number == 1
    assert all_moving_qian.changed_number == 2


def test_diagram_is_rendered_top_down_with_moving_markers() -> None:
    diagram = render_diagram(CastResult((6, 7, 8, 9, 7, 8)))
    assert "本卦" in diagram
    assert " ○" in diagram
    assert " ×" in diagram
    assert diagram.splitlines()[1].startswith("6 ")


def test_corpus_is_complete_and_binary_mapping_is_consistent() -> None:
    corpus = ZhouyiCorpus(ROOT / "data" / "zhouyi.json")
    payload = json.loads((ROOT / "data" / "zhouyi.json").read_text(encoding="utf-8"))
    assert len(payload["hexagrams"]) == 64
    assert sum(len(row["lines"]) for row in payload["hexagrams"]) == 384
    for row in payload["hexagrams"]:
        bits = tuple(int(value) for value in row["binary_bottom_up"])
        assert bits_to_hexagram_number(bits) == row["number"]

    assert "潛龍" in corpus.get(1)["lines"][0]
    assert "履霜" in corpus.get(2)["lines"][0]


def test_reading_contains_primary_changed_and_agent_constraints() -> None:
    corpus = ZhouyiCorpus(ROOT / "data" / "zhouyi.json")
    service = ReadingService(corpus, ROOT / "data" / "intents.json")
    text = service.render(
        CastResult((9, 7, 7, 7, 7, 7)),
        intent="事业",
        question="是否推进项目",
        method="测试",
        for_agent=True,
    )
    assert "本卦" in text
    assert "之卦" in text
    assert "動爻" in text or "动爻" in text
    assert "不要杜撰古籍原句" in text
