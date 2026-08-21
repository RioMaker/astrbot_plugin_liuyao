"""Pure six-line divination logic; independent from AstrBot for easy testing."""

from __future__ import annotations

from dataclasses import dataclass
import re
import secrets
from typing import Callable, Iterable, Sequence


LINE_NAMES = {
    6: "老阴（动）",
    7: "少阳",
    8: "少阴",
    9: "老阳（动）",
}

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

# Rows are lower trigrams, columns are upper trigrams.  The order follows the
# quick-reference table in the received (King Wen) sequence of the Zhouyi.
TRIGRAM_ORDER = ("乾", "兑", "离", "震", "巽", "坎", "艮", "坤")
HEXAGRAM_MATRIX = (
    (1, 43, 14, 34, 9, 5, 26, 11),
    (10, 58, 38, 54, 61, 60, 41, 19),
    (13, 49, 30, 55, 37, 63, 22, 36),
    (25, 17, 21, 51, 42, 3, 27, 24),
    (44, 28, 50, 32, 57, 48, 18, 46),
    (6, 47, 64, 40, 59, 29, 4, 7),
    (33, 31, 56, 62, 53, 39, 52, 15),
    (12, 45, 35, 16, 20, 8, 23, 2),
)

INTENT_ALIASES = {
    "综合": "general",
    "总体": "general",
    "general": "general",
    "事业": "career",
    "工作": "career",
    "career": "career",
    "感情": "relationship",
    "姻缘": "relationship",
    "relationship": "relationship",
    "财富": "wealth",
    "财运": "wealth",
    "钱财": "wealth",
    "wealth": "wealth",
    "学业": "study",
    "考试": "study",
    "study": "study",
    "健康": "health",
    "health": "health",
    "家庭": "family",
    "家宅": "family",
    "family": "family",
    "出行": "travel",
    "迁移": "travel",
    "travel": "travel",
}


class ManualCastError(ValueError):
    """Raised when a manual six-line cast cannot be parsed."""


@dataclass(frozen=True)
class CastResult:
    """A cast represented by six values ordered from the bottom line upward."""

    lines: tuple[int, int, int, int, int, int]

    def __post_init__(self) -> None:
        if len(self.lines) != 6 or any(value not in LINE_NAMES for value in self.lines):
            raise ValueError("六爻必须由自下而上的 6 个 6/7/8/9 组成")

    @property
    def primary_bits(self) -> tuple[int, ...]:
        return tuple(1 if value in (7, 9) else 0 for value in self.lines)

    @property
    def changed_bits(self) -> tuple[int, ...]:
        return tuple(
            (1 - bit) if value in (6, 9) else bit
            for bit, value in zip(self.primary_bits, self.lines, strict=True)
        )

    @property
    def moving_lines(self) -> tuple[int, ...]:
        return tuple(index for index, value in enumerate(self.lines, start=1) if value in (6, 9))

    @property
    def primary_number(self) -> int:
        return bits_to_hexagram_number(self.primary_bits)

    @property
    def changed_number(self) -> int:
        return bits_to_hexagram_number(self.changed_bits)


def cast_instant(randbelow: Callable[[int], int] = secrets.randbelow) -> CastResult:
    """Cast six three-coin lines with the traditional 1:3:3:1 probabilities."""

    values: list[int] = []
    for _ in range(6):
        roll = randbelow(8)
        if roll == 0:
            values.append(6)
        elif roll <= 3:
            values.append(7)
        elif roll <= 6:
            values.append(8)
        else:
            values.append(9)
    return CastResult(tuple(values))  # type: ignore[arg-type]


def parse_manual_cast(raw: str) -> CastResult:
    """Parse six bottom-up lines as 6/7/8/9 or six groups of three coins.

    Coin notation uses 正/字/阳/H as value 3 and 反/花/阴/T as value 2.
    Therefore each three-coin group naturally sums to 6, 7, 8, or 9.
    """

    text = (raw or "").strip()
    if not text:
        raise ManualCastError("没有提供手摇结果")

    compact = re.fullmatch(r"[6789]{6}", text)
    if compact:
        return CastResult(tuple(int(char) for char in text))  # type: ignore[arg-type]

    numeric_parts = [part for part in re.split(r"[\s,，、/|;；]+", text) if part]
    if len(numeric_parts) == 6 and all(part in {"6", "7", "8", "9"} for part in numeric_parts):
        return CastResult(tuple(int(part) for part in numeric_parts))  # type: ignore[arg-type]

    coin_parts = [part for part in re.split(r"[\s,，、/|;；]+", text) if part]
    if len(coin_parts) != 6:
        raise ManualCastError("请提供恰好六爻，并按初爻到上爻（自下而上）排列")

    values = tuple(_parse_three_coins(part) for part in coin_parts)
    return CastResult(values)  # type: ignore[arg-type]


def split_manual_payload(payload: str) -> tuple[str, str]:
    """Split a command tail into the six-line expression and remaining text."""

    text = (payload or "").strip()
    compact = re.match(r"^([6789]{6})(?:\s+(.*))?$", text)
    if compact:
        return compact.group(1), (compact.group(2) or "").strip()

    tokens = text.split()
    if len(tokens) >= 6:
        candidate = " ".join(tokens[:6])
        try:
            parse_manual_cast(candidate)
        except ManualCastError:
            pass
        else:
            return candidate, " ".join(tokens[6:]).strip()

    # Six numeric lines may be separated by punctuation without spaces.
    match = re.match(
        r"^\s*([6789](?:\s*[,，、/|;；]\s*[6789]){5})(?:\s+(.*))?$",
        text,
    )
    if match:
        return match.group(1), (match.group(2) or "").strip()
    raise ManualCastError("无法从指令中识别六爻；推荐格式：7 8 9 6 7 8")


def parse_intent_and_question(text: str) -> tuple[str, str]:
    """Return a normalized intent key and the optional question text."""

    parts = (text or "").strip().split(maxsplit=1)
    if not parts:
        return "general", ""
    intent = INTENT_ALIASES.get(parts[0].lower())
    if intent:
        return intent, parts[1].strip() if len(parts) == 2 else ""
    return "general", (text or "").strip()


def bits_to_hexagram_number(bits: Sequence[int]) -> int:
    if len(bits) != 6 or any(bit not in (0, 1) for bit in bits):
        raise ValueError("卦象必须是自下而上的六个阴阳位")
    lower = _trigram_name(bits[:3])
    upper = _trigram_name(bits[3:])
    return HEXAGRAM_MATRIX[TRIGRAM_ORDER.index(lower)][TRIGRAM_ORDER.index(upper)]


def render_diagram(cast: CastResult) -> str:
    """Render original and changed hexagrams side-by-side, top line first."""

    rows = ["本卦　　　　　之卦"]
    changed = cast.changed_bits
    for index in range(5, -1, -1):
        value = cast.lines[index]
        primary_line = "━━━━━━" if value in (7, 9) else "━━  ━━"
        marker = " ○" if value == 9 else " ×" if value == 6 else "  "
        changed_line = "━━━━━━" if changed[index] else "━━  ━━"
        rows.append(f"{index + 1} {primary_line}{marker}　{changed_line}")
    return "\n".join(rows)


def extract_subcommand_tail(message: str, subcommands: Iterable[str]) -> str:
    """Extract everything following the first matching subcommand token."""

    normalized = (message or "").strip().lstrip("/／").strip()
    tokens = normalized.split()
    lowered = {item.lower() for item in subcommands}
    for index, token in enumerate(tokens):
        if token.lower() in lowered:
            return " ".join(tokens[index + 1 :]).strip()
    return ""


def _parse_three_coins(group: str) -> int:
    normalized = group.strip().replace("阳", "正").replace("陰", "反").replace("阴", "反")
    normalized = normalized.replace("字", "正").replace("花", "反")
    normalized = normalized.upper().replace("H", "正").replace("T", "反")
    normalized = re.sub(r"[-+_.]", "", normalized)
    if len(normalized) != 3 or any(char not in {"正", "反"} for char in normalized):
        raise ManualCastError(f"无法识别三枚铜币结果：{group}")
    return sum(3 if char == "正" else 2 for char in normalized)


def _trigram_name(bits: Sequence[int]) -> str:
    bit_tuple = tuple(bits)
    for name, pattern in TRIGRAM_BITS.items():
        if bit_tuple == pattern:
            return name
    raise ValueError(f"无法识别三爻卦：{bit_tuple!r}")
