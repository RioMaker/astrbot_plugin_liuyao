"""Traditional Najia branches and six-relative relationships for 六爻."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


TRIGRAM_BITS = {
    "乾": (1, 1, 1),
    "兑": (1, 1, 0),
    "离": (1, 0, 1),
    "震": (1, 0, 0),
    "巽": (0, 1, 1),
    "坎": (0, 1, 0),
    "艮": (0, 0, 1),
    "坤": (0, 0, 0),
}

TRIGRAM_ELEMENTS = {
    "乾": "金",
    "兑": "金",
    "离": "火",
    "震": "木",
    "巽": "木",
    "坎": "水",
    "艮": "土",
    "坤": "土",
}

# Each tuple is ordered from the bottom line upward.
INNER_BRANCHES = {
    "乾": ("子", "寅", "辰"),
    "兑": ("巳", "卯", "丑"),
    "离": ("卯", "丑", "亥"),
    "震": ("子", "寅", "辰"),
    "巽": ("丑", "亥", "酉"),
    "坎": ("寅", "辰", "午"),
    "艮": ("辰", "午", "申"),
    "坤": ("未", "巳", "卯"),
}

OUTER_BRANCHES = {
    "乾": ("午", "申", "戌"),
    "兑": ("亥", "酉", "未"),
    "离": ("酉", "未", "巳"),
    "震": ("午", "申", "戌"),
    "巽": ("未", "巳", "卯"),
    "坎": ("申", "戌", "子"),
    "艮": ("戌", "子", "寅"),
    "坤": ("丑", "亥", "酉"),
}

BRANCH_ELEMENTS = {
    "子": "水",
    "亥": "水",
    "寅": "木",
    "卯": "木",
    "巳": "火",
    "午": "火",
    "申": "金",
    "酉": "金",
    "辰": "土",
    "戌": "土",
    "丑": "土",
    "未": "土",
}

GENERATES = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
CONTROLS = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

# 八宫 order: 本宫、一世、二世、三世、四世、五世、游魂、归魂.
PALACE_PATTERNS = (
    ("本宫", ()),
    ("一世", (1,)),
    ("二世", (1, 2)),
    ("三世", (1, 2, 3)),
    ("四世", (1, 2, 3, 4)),
    ("五世", (1, 2, 3, 4, 5)),
    ("游魂", (1, 2, 3, 5)),
    ("归魂", (5,)),
)


@dataclass(frozen=True)
class LineRelative:
    position: int
    branch: str
    element: str
    relative: str

    @property
    def label(self) -> str:
        return f"{self.relative}{self.branch}{self.element}"


@dataclass(frozen=True)
class HexagramRelatives:
    palace: str
    palace_element: str
    palace_stage: str
    lines: tuple[LineRelative, ...]


def palace_for_bits(bits: Sequence[int]) -> tuple[str, str]:
    """Return the 八宫 trigram and stage for six bottom-up yin/yang bits."""

    normalized = _normalize_bits(bits)
    for palace, trigram in TRIGRAM_BITS.items():
        pure = trigram + trigram
        for stage, changed_positions in PALACE_PATTERNS:
            candidate = list(pure)
            for position in changed_positions:
                candidate[position - 1] = 1 - candidate[position - 1]
            if tuple(candidate) == normalized:
                return palace, stage
    raise ValueError(f"无法判定卦宫：{normalized!r}")


def branches_for_bits(bits: Sequence[int]) -> tuple[str, ...]:
    """Install Najia earthly branches from the actual lower/upper trigrams."""

    normalized = _normalize_bits(bits)
    lower = _trigram_name(normalized[:3])
    upper = _trigram_name(normalized[3:])
    return INNER_BRANCHES[lower] + OUTER_BRANCHES[upper]


def relatives_for_bits(
    bits: Sequence[int],
    *,
    reference_element: str | None = None,
) -> HexagramRelatives:
    """Return six relatives; changed hexagrams may reuse the primary palace element."""

    normalized = _normalize_bits(bits)
    palace, stage = palace_for_bits(normalized)
    palace_element = TRIGRAM_ELEMENTS[palace]
    base_element = reference_element or palace_element
    if base_element not in GENERATES:
        raise ValueError(f"无效的卦宫五行：{base_element}")
    lines = tuple(
        LineRelative(
            position=index,
            branch=branch,
            element=BRANCH_ELEMENTS[branch],
            relative=_relative(BRANCH_ELEMENTS[branch], base_element),
        )
        for index, branch in enumerate(branches_for_bits(normalized), start=1)
    )
    return HexagramRelatives(
        palace=palace,
        palace_element=palace_element,
        palace_stage=stage,
        lines=lines,
    )


def _relative(line_element: str, self_element: str) -> str:
    if line_element == self_element:
        return "兄弟"
    if GENERATES[line_element] == self_element:
        return "父母"
    if GENERATES[self_element] == line_element:
        return "子孙"
    if CONTROLS[line_element] == self_element:
        return "官鬼"
    if CONTROLS[self_element] == line_element:
        return "妻财"
    raise ValueError(f"无法判定六亲：{line_element}/{self_element}")


def _normalize_bits(bits: Sequence[int]) -> tuple[int, ...]:
    normalized = tuple(int(bit) for bit in bits)
    if len(normalized) != 6 or any(bit not in {0, 1} for bit in normalized):
        raise ValueError("卦象必须是自下而上的六个阴阳位")
    return normalized


def _trigram_name(bits: Sequence[int]) -> str:
    normalized = tuple(bits)
    for name, pattern in TRIGRAM_BITS.items():
        if normalized == pattern:
            return name
    raise ValueError(f"无法识别三爻卦：{normalized!r}")
