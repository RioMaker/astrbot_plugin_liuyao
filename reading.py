"""Compose compact user replies and structured Agent context from a cast."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from .corpus import ZhouyiCorpus
    from .divination import CastResult, render_diagram
    from .najia import relatives_for_bits
except ImportError:  # pragma: no cover - direct local execution
    from corpus import ZhouyiCorpus
    from divination import CastResult, render_diagram
    from najia import relatives_for_bits


class ReadingService:
    def __init__(self, corpus: ZhouyiCorpus, intent_path: Path):
        self.corpus = corpus
        payload = json.loads(intent_path.read_text(encoding="utf-8"))
        directions = payload.get("directions")
        if not isinstance(directions, dict) or "general" not in directions:
            raise ValueError("意图方向数据缺少 directions/general")
        self.directions: dict[str, dict[str, Any]] = directions
        self.curated: dict[tuple[int, str], dict[str, Any]] = {}
        for item in payload.get("curated_readings", []):
            if isinstance(item, dict):
                self.curated[(int(item["hexagram"]), str(item["intent"]))] = item

    def normalize_intent(self, intent: str) -> str:
        value = (intent or "general").strip().lower()
        if value in self.directions:
            return value
        for key, profile in self.directions.items():
            aliases = [str(alias).lower() for alias in profile.get("aliases", [])]
            if value == str(profile.get("label", "")).lower() or value in aliases:
                return key
        return "general"

    def render(
        self,
        cast: CastResult,
        *,
        intent: str,
        question: str,
        method: str,
        for_agent: bool = False,
    ) -> str:
        intent_key = self.normalize_intent(intent)
        profile = self.directions[intent_key]
        primary = self.corpus.get(cast.primary_number)
        changed = self.corpus.get(cast.changed_number)
        moving = cast.moving_lines
        primary_relatives = relatives_for_bits(cast.primary_bits)
        changed_relatives = relatives_for_bits(
            cast.changed_bits,
            reference_element=primary_relatives.palace_element,
        )

        rows = [
            "六爻问卦｜纳甲排盘",
            f"方式：{method}",
            f"意图：{profile['label']}",
        ]
        if question:
            rows.append(f"所问：{question}")
        rows.extend(
            [
                f"本卦：{primary['symbol']} 第{primary['number']}卦 {primary['name']}（{primary['upper_trigram']}上{primary['lower_trigram']}下）",
                (
                    f"之卦：{changed['symbol']} 第{changed['number']}卦 {changed['name']}"
                    if moving
                    else "之卦：无动爻"
                ),
                render_diagram(cast),
                "六爻（初爻→上爻）：" + " ".join(str(value) for value in cast.lines),
                (
                    f"卦宫：{primary_relatives.palace}宫"
                    f"（{primary_relatives.palace_element}，"
                    f"{primary_relatives.palace_stage}）"
                ),
                "本卦六亲（初爻→上爻）："
                + " ".join(line.label for line in primary_relatives.lines),
                f"本卦卦辞：{primary['judgment']}",
            ]
        )

        if moving:
            rows.append(
                "动爻：" + "；".join(primary["lines"][position - 1] for position in moving)
            )
            if len(moving) == 6 and primary.get("extra_lines"):
                rows.append("全爻皆变：" + "；".join(primary["extra_lines"]))
            rows.append(
                "之卦六亲（沿用本卦宫五行，初爻→上爻）："
                + " ".join(line.label for line in changed_relatives.lines)
            )
            rows.append(f"之卦卦辞：{changed['judgment']}")
        else:
            rows.append("动爻：无；以本卦卦辞和整体卦象为主。")

        focus = "、".join(str(item) for item in profile.get("focus", []))
        if focus:
            rows.append(f"{profile['label']}关注：{focus}")

        curated = self.curated.get((primary["number"], intent_key))
        if curated:
            rows.append(
                f"候选签词（{curated['source']}）：{curated['quote']}"
            )
            rows.append(f"方向提示：{curated['guidance']}")

        if for_agent:
            rows.extend(
                [
                    "Agent解读约束：",
                    "1. 图中AI短评是现代提示，不得当作古籍原文引用。",
                    "2. 原文只可引用上列卦辞、动爻辞、之卦卦辞；不要杜撰古籍原句。",
                    "3. 无动爻重本卦；有动爻优先解释动爻，之卦只作为变化趋势。",
                    f"4. 围绕“{profile['label']}”关注点分析，给出切合所问的判断与建议。",
                    "5. 最终回复须以单独一行“断语：……”收尾，断语应明确、简练。",
                    "6. 只保留六爻解读，不附加与卦义无关的固定套话。",
                ]
            )

        return "\n".join(rows)



