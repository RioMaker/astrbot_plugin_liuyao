from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_configuration_and_metadata_contract() -> None:
    config = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert config["default_enabled"]["default"] is False
    assert config["allow_operator_api_lookup"]["default"] is True
    assert config["agent_send_chart_image"]["default"] is True
    assert config["command_send_chart_image"]["default"] is True
    assert config["agent_generate_chart_comment"]["default"] is True
    assert config["agent_display_name"]["default"] == "AI助手"
    assert config["agent_comment_timeout_seconds"]["default"] == 45
    metadata = (ROOT / "metadata.yaml").read_text(encoding="utf-8")
    assert "name: astrbot_plugin_liuyao" in metadata
    assert "version: \"0.4.3\"" in metadata
    assert "aiocqhttp" in metadata
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "Pillow>=10.0.0" in requirements
    font = ROOT / "assets" / "fonts" / "NotoSansCJKsc-Regular.otf"
    font_license = ROOT / "assets" / "fonts" / "LICENSE.txt"
    assert font.stat().st_size > 10_000_000
    assert "SIL OPEN FONT LICENSE Version 1.1" in font_license.read_text(
        encoding="utf-8"
    )


def test_corpus_has_revision_attribution_and_special_lines() -> None:
    payload = json.loads((ROOT / "data" / "zhouyi.json").read_text(encoding="utf-8"))
    assert len(payload["source"]["revisions"]) == 64
    assert all(row["source_revision"] for row in payload["hexagrams"])
    assert all(all(line.strip() for line in row["lines"]) for row in payload["hexagrams"])
    assert len(payload["hexagrams"][0]["extra_lines"]) == 1
    assert len(payload["hexagrams"][1]["extra_lines"]) == 1
    assert all(not row["extra_lines"] for row in payload["hexagrams"][2:])


def test_intent_dataset_has_supported_directions_and_curated_examples() -> None:
    payload = json.loads((ROOT / "data" / "intents.json").read_text(encoding="utf-8"))
    assert set(payload["directions"]) == {
        "general",
        "career",
        "relationship",
        "wealth",
        "study",
        "health",
        "family",
        "travel",
    }
    assert len(payload["curated_readings"]) >= 8


def test_documented_sources_and_data_license_exist() -> None:
    sources = (ROOT / "docs" / "DATA_SOURCES.md").read_text(encoding="utf-8")
    notice = (ROOT / "data" / "LICENSE.md").read_text(encoding="utf-8")
    assert "维基文库《周易》" in sources
    assert "中国哲学书电子化计划" in sources
    assert "CC BY-SA 4.0" in notice




