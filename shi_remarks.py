"""Offline, AI-authored wit keyed by primary hexagram and actual 世爻 position.

These modern compositions borrow imagery from the bundled Zhouyi corpus; they
are not quotations from ancient authors. Positions run bottom to top (1 to 6).
The six entries per hexagram do not alter the chart's fixed 八宫 世爻 placement.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REMARKS_PATH = Path(__file__).resolve().parent / "data" / "shi_remarks.json"
FALLBACK_REMARK = "卦位未明，且把脚下站稳。"


@lru_cache(maxsize=1)
def _load_remarks() -> tuple[tuple[str, ...], ...]:
    """Load and validate the complete, immutable 64 by 6 preset table once."""
    payload = json.loads(REMARKS_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("世爻短评数据版本无效")
    rows = payload.get("hexagrams")
    if not isinstance(rows, list) or len(rows) != 64:
        raise ValueError("世爻短评必须包含 64 卦")
    table = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("世爻短评卦目无效")
        number = row.get("number")
        remarks = row.get("remarks")
        if type(number) is not int or not 1 <= number <= 64 or number in table:
            raise ValueError("世爻短评卦序缺失或重复")
        if not isinstance(remarks, list) or len(remarks) != 6:
            raise ValueError("每卦须有初爻至上爻六条短评")
        if any(
            not isinstance(text, str)
            or not 1 <= len(text) <= 24
            or text != text.strip()
            or "\n" in text
            or "\r" in text
            for text in remarks
        ):
            raise ValueError("世爻短评须为 24 字符以内的单行文字")
        table[number] = tuple(remarks)
    if set(table) != set(range(1, 65)):
        raise ValueError("世爻短评卦序不完整")
    return tuple(table[number] for number in range(1, 65))


def get_shi_remark(number: int, position: int) -> str:
    """Return a preset without a model call; damaged/missing data stays renderable."""
    if type(number) is not int or type(position) is not int:
        return FALLBACK_REMARK
    if not 1 <= number <= 64 or not 1 <= position <= 6:
        return FALLBACK_REMARK
    try:
        return _load_remarks()[number - 1][position - 1]
    except (OSError, UnicodeError, ValueError, TypeError):
        return FALLBACK_REMARK
