"""Load and validate the plugin's offline Zhouyi corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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

    def get(self, number: int) -> dict[str, Any]:
        try:
            return self._by_number[int(number)]
        except (KeyError, TypeError, ValueError) as exc:
            raise CorpusError("卦序必须在 1 到 64 之间") from exc

    def lookup_text(self, number: int, line: int = 0) -> str:
        row = self.get(number)
        header = f"{row['symbol']} 第{number}卦 {row['name']}（{row['upper_trigram']}上{row['lower_trigram']}下）"
        if line == 0:
            return f"{header}\n卦辞：{row['judgment']}"
        if not 1 <= line <= 6:
            raise CorpusError("爻位必须为 0（卦辞）或 1 到 6（初爻至上爻）")
        return f"{header}\n第{line}爻：{row['lines'][line - 1]}"
