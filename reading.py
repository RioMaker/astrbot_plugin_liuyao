"""Compose compact user replies and structured Agent context from a cast."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from .corpus import ZhouyiCorpus
    from .divination import CastResult, render_diagram
    from .najia import relatives_for_bits
    from .paipan import chart_for, chart_text
    from .prompts import INTERPRETATION_SECTIONS
    from .reference_library import ReferenceLibrary, StudyReferenceLibrary
except ImportError:  # pragma: no cover - direct local execution
    from corpus import ZhouyiCorpus
    from divination import CastResult, render_diagram
    from najia import relatives_for_bits
    from paipan import chart_for, chart_text
    from prompts import INTERPRETATION_SECTIONS
    from reference_library import ReferenceLibrary, StudyReferenceLibrary


class ReadingService:
    def __init__(
        self,
        corpus: ZhouyiCorpus,
        intent_path: Path,
        *,
        prompt_template: Callable[[str, str], str] | None = None,
    ):
        self.corpus = corpus
        self.prompt_template = prompt_template
        payload = json.loads(intent_path.read_text(encoding="utf-8"))
        directions = payload.get("directions")
        if not isinstance(directions, dict) or "general" not in directions:
            raise ValueError("意图方向数据缺少 directions/general")
        self.builtin_directions = directions
        self.directions: dict[str, dict[str, Any]] = directions.copy()
        sources = {
            key: profile for key, profile in directions.items() if "reference" not in profile
        }
        self.references = ReferenceLibrary(intent_path.parent / "references", sources)
        self.study_references = StudyReferenceLibrary(
            intent_path.parent.parent / "docs" / "study",
            sources,
        )
        self.curated: dict[tuple[int, str], dict[str, Any]] = {}
        for item in payload.get("curated_readings", []):
            if isinstance(item, dict):
                self.curated[(int(item["hexagram"]), str(item["intent"]))] = item

    def set_custom_directions(self, custom: dict) -> None:
        self.directions = {**self.builtin_directions, **custom}

    def direction_labels(self) -> dict[str, str]:
        return {key: profile["label"] for key, profile in self.directions.items()}

    def classification_catalog(self) -> str:
        rows = []
        for profile in self.directions.values():
            scope = profile.get("description") or "、".join(profile.get("focus", []))
            rows.append(f"{profile['label']}：{scope}" if scope else profile["label"])
        return "\n".join(rows)

    def normalize_intent(self, intent: str, fallback: str = "general") -> str:
        value = (intent or "").strip().lower()
        if not value:
            return fallback
        if value in self.directions:
            return value
        for key, profile in self.directions.items():
            aliases = [str(alias).lower() for alias in profile.get("aliases", [])]
            if value == str(profile.get("label", "")).lower() or value in aliases:
                return key
        return fallback

    def case_direction(self, case: dict) -> tuple[str, str]:
        for field in ("intent", "intent_label"):
            key = self.normalize_intent(str(case.get(field) or ""), "")
            if key:
                return key, self.directions[key]["label"]
        return "uncategorized", str(case.get("intent_label") or case.get("intent") or "未分类")

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
                f"本卦：{primary['symbol']} 第{primary['number']}卦 {primary['name']}"
                f"（{primary['upper_trigram']}上{primary['lower_trigram']}下）",
                (
                    f"之卦：{changed['symbol']} 第{changed['number']}卦 {changed['name']}"
                    if moving
                    else "卦态：静卦（无动爻）"
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
            rows.append("动爻：" + "；".join(primary["lines"][position - 1] for position in moving))
            if len(moving) == 6 and primary.get("extra_lines"):
                rows.append("全爻皆变：" + "；".join(primary["extra_lines"]))
            rows.append(
                "之卦六亲（沿用本卦宫五行，初爻→上爻）："
                + " ".join(line.label for line in changed_relatives.lines)
            )
            rows.append(f"之卦卦辞：{changed['judgment']}")
        else:
            rows.append("动爻：无；结合本卦、六亲与所问分类研判，不虚构动变。")

        rows.append(chart_text(chart_for(cast), show_changed=bool(moving)))
        focus = "、".join(str(item) for item in profile.get("focus", []))
        if profile.get("description"):
            rows.append(f"{profile['label']}适用范围：{profile['description']}")
        if focus:
            rows.append(f"{profile['label']}关注：{focus}")

        curated = self.curated.get((primary["number"], intent_key))
        if curated:
            rows.append(f"候选签词（{curated['source']}）：{curated['quote']}")
            rows.append(f"方向提示：{curated['guidance']}")

        if for_agent:
            rows.append(self.reference_context(intent_key))
            key = f"interpretation_{intent_key}_rules"
            if key not in INTERPRETATION_SECTIONS:
                key = "interpretation_general_rules"
            _, rule = INTERPRETATION_SECTIONS[key]
            if self.prompt_template:
                rule = self.prompt_template(key, rule)
            rows.append(rule.replace("{intent_label}", profile["label"]))

        return "\n".join(rows)

    def reference_context(self, intent: str) -> str:
        key = self.normalize_intent(intent)
        return self.references.render(self.directions[key].get("reference", key)) + (
            "\n\n扩展研读：可调用 lookup_liuyao_reference(topic) 获取任意类型的"
            "完整学习笔记、三个古籍卦例与出处，不限当前方向；topic 留空返回目录，"
            "传“基础”读通则，传“来源”核对版本。古例不等于本群已验证反馈。"
        )
