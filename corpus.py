"""Load, validate, and search the plugin's offline Zhouyi corpus."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


TRIGRAM_IMAGES = {
    "乾": "天",
    "兌": "泽",
    "兑": "泽",
    "離": "火",
    "离": "火",
    "震": "雷",
    "巽": "风",
    "坎": "水",
    "艮": "山",
    "坤": "地",
}

NORMALIZE_TRANSLATION = str.maketrans(
    "訟師謙隨蠱臨觀賁剝復頤過離恆遯壯晉損漸歸豐兌渙節濟無為",
    "讼师谦随蛊临观贲剥复颐过离恒遁壮晋损渐归丰兑涣节济无为",
)


class CorpusError(RuntimeError):
    pass


class ZhouyiCorpus:
    def __init__(self, path: Path):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CorpusError(f"无法读取《周易》数据：{exc}") from exc

        rows = payload.get("hexagrams") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != 64:
            raise CorpusError("《周易》数据必须恰好包含 64 卦")

        self.metadata = payload.get("source", {})
        self._by_number: dict[int, dict[str, Any]] = {}
        self._aliases: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise CorpusError("卦数据项必须是对象")
            number = row.get("number")
            lines = row.get("lines")
            if not isinstance(number, int) or not 1 <= number <= 64:
                raise CorpusError(f"无效卦序：{number!r}")
            if not isinstance(lines, list) or len(lines) != 6:
                raise CorpusError(f"第 {number} 卦必须有六条爻辞")
            self._by_number[number] = row

        if set(self._by_number) != set(range(1, 65)):
            raise CorpusError("卦序不完整或有重复")

        for number, row in self._by_number.items():
            full_name = self._full_name(row)
            aliases = {
                str(number),
                f"第{number}卦",
                str(row["symbol"]),
                str(row["name"]),
                f"{row['name']}卦",
                full_name,
                f"{full_name}卦",
            }
            for alias in aliases:
                self._aliases[self._normalize_name(alias)] = number

    def get(self, number: int) -> dict[str, Any]:
        try:
            return self._by_number[int(number)]
        except (KeyError, TypeError, ValueError) as exc:
            raise CorpusError("卦序必须在 1 到 64 之间") from exc

    def resolve(self, name_or_number: str) -> dict[str, Any]:
        """Resolve 乾、乾为天、第1卦、1 or the Unicode hexagram symbol."""

        key = self._normalize_name(name_or_number)
        number = self._aliases.get(key)
        if number is None:
            raise CorpusError(f"无法识别卦名：{name_or_number}")
        return self.get(number)

    def lookup_text(self, number: int, line: int = 0) -> str:
        row = self.get(number)
        header = (
            f"{row['symbol']} 第{number}卦 {row['name']}"
            f"（{row['upper_trigram']}上{row['lower_trigram']}下）"
        )
        if line == 0:
            return f"{header}\n卦辞：{row['judgment']}"
        if not 1 <= line <= 6:
            raise CorpusError("爻位必须为 0（卦辞）或 1 到 6（初爻至上爻）")
        return f"{header}\n第{line}爻：{row['lines'][line - 1]}"

    @staticmethod
    def _normalize_name(value: str) -> str:
        text = re.sub(r"[\s·•._-]+", "", str(value or "").strip())
        return text.translate(NORMALIZE_TRANSLATION)

    @staticmethod
    def _full_name(row: dict[str, Any]) -> str:
        upper = str(row["upper_trigram"])
        lower = str(row["lower_trigram"])
        upper_image = TRIGRAM_IMAGES[upper]
        lower_image = TRIGRAM_IMAGES[lower]
        if upper.translate(NORMALIZE_TRANSLATION) == lower.translate(
            NORMALIZE_TRANSLATION
        ):
            return f"{row['name']}为{upper_image}"
        return f"{upper_image}{lower_image}{row['name']}"
