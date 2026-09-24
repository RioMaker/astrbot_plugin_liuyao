from __future__ import annotations

import asyncio
import copy
import inspect
import re
from pathlib import Path

import pytest

from test_permissions import _Event, _make_enabled_plugin
from reference_library import StudyReferenceLibrary


ROOT = Path(__file__).resolve().parents[1]
NOTES = ROOT / "docs" / "study"


def test_catalog_covers_all_topics_with_three_distinct_sourced_cases():
    plugin = _make_enabled_plugin()
    library = plugin.readings.study_references
    assert not library.errors
    assert set(library.documents) == {
        "index", "foundations", "sources", *plugin.readings.directions,
    }
    all_ids = []
    for key in plugin.readings.directions:
        document = library.documents[key]
        assert "2026-09-24" in document
        assert "## 学习笔记" in document
        assert "## 给 Agent 的研判要求" in document
        cases = re.split(r"(?m)^### ([A-Z]{2}-\d{3}) ", document)
        assert len(cases) == 7, key
        for case_id, content in zip(cases[1::2], cases[2::2]):
            all_ids.append(case_id)
            for field in (
                "出处", "原问与时日", "卦象/关键爻", "原断摘录",
                "书载结果", "整理者分析", "限制",
            ):
                assert f"- {field}" in content, (case_id, field)
            assert "https://" in content and "定位" in content
            assert any(book in content for book in ("增删卜易", "金楼子", "梅花易数"))
        assert f"topics/{key}.md" in library.documents["index"]
        assert len(library.lookup(key)) < 15_000
    assert len(all_ids) == len(set(all_ids)) == 30


def test_local_markdown_links_resolve():
    for path in NOTES.rglob("*.md"):
        document = path.read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)]+)\)", document):
            if target.startswith(("https://", "#")):
                continue
            resolved = (path.parent / target.split("#", 1)[0]).resolve()
            assert resolved.is_file(), (path, target)
            resolved.relative_to(ROOT)


@pytest.mark.parametrize("topic", [
    "general", "career", "relationship", "wealth", "study", "health",
    "family", "travel", "shefu", "weather",
])
def test_any_topic_and_all_aliases_return_full_notes_without_casting(topic):
    plugin = _make_enabled_plugin()
    event = _Event("member")
    original = copy.deepcopy(plugin._test_store)
    profile = plugin.readings.directions[topic]
    for alias in (topic, profile["label"], *profile["aliases"]):
        result = asyncio.run(plugin.lookup_liuyao_reference_tool(event, alias))
        assert plugin.readings.study_references.documents[topic] in result
        assert "古例与现代整理已分栏" in result
        assert result.count("### ") == 3
    assert not event.sent
    assert plugin._test_store == original
    assert not plugin._pending_agent_cases


def test_cross_topic_index_and_shared_documents_are_accessible():
    plugin = _make_enabled_plugin()
    assert "天气" in plugin.readings.reference_context("weather")
    assert "SF-003" in asyncio.run(plugin.lookup_liuyao_reference_tool(_Event("member"), "射覆"))
    for alias in ("", "目录", "索引", "index"):
        assert "30 例" in plugin.readings.study_references.lookup(alias)
    for alias in ("基础", "通则", "foundations"):
        assert "## 7. 当前插件能力" in plugin.readings.study_references.lookup(alias)
    for alias in ("来源", "sources"):
        assert "1441927" in plugin.readings.study_references.lookup(alias)
    assert "CA-001" in plugin.readings.study_references.lookup(" CAREER ")
    assert "Args:" in inspect.getdoc(plugin.lookup_liuyao_reference_tool)


def test_tool_respects_group_switch_and_private_chat_gate():
    plugin = _make_enabled_plugin()
    plugin._test_store["method_switches"]["10001"]["liuyao"] = False
    result = asyncio.run(plugin.lookup_liuyao_reference_tool(_Event("owner"), "天气"))
    assert "尚未开启" in result and "WX-001" not in result
    private = _Event("member", astrbot_admin=True)
    private.get_group_id = lambda: ""
    result = asyncio.run(plugin.lookup_liuyao_reference_tool(private, "基础"))
    assert "仅面向 QQ 群聊" in result


@pytest.mark.parametrize("topic", [
    "../sources", "../../main.py", "D:/secret.md", "topics/weather.md",
    "weather/../health", "http://example.com", "不存在", "a" * 81, None, [],
])
def test_untrusted_parameters_never_read_files_or_fall_back(topic):
    library = _make_enabled_plugin().readings.study_references
    result = library.lookup(topic)
    assert "不接受文件路径" in result
    assert "GE-001" not in result


@pytest.mark.parametrize(
    "payload", [b"", b"\xff", b"a" * 14_001, b"a" * 60_001],
    ids=["empty", "invalid-utf8", "too-many-characters", "too-many-bytes"],
)
def test_broken_topic_fails_soft_without_substitution(tmp_path, payload):
    (tmp_path / "topics").mkdir()
    (tmp_path / "topics" / "weather.md").write_bytes(payload)
    library = StudyReferenceLibrary(tmp_path, {"weather": {"label": "天气"}})
    assert "weather" in library.errors
    assert "weather 未加载" in library.lookup("天气")
    assert "index 未加载" in library.lookup()


def test_invalid_catalog_key_is_not_a_file_path(tmp_path):
    library = StudyReferenceLibrary(tmp_path, {"../../private": {}, "index": {}})
    assert "未知参考类型" in library.lookup("../../private")
    assert not library.documents


def test_reference_symlink_outside_bundle_is_rejected(tmp_path):
    directory = tmp_path / "notes"
    directory.mkdir()
    private = tmp_path / "outside.md"
    private.write_text("must not be returned", encoding="utf-8")
    try:
        (directory / "README.md").symlink_to(private)
    except OSError:
        pytest.skip("host does not allow symlink creation")
    library = StudyReferenceLibrary(directory, {})
    assert library.errors["index"] == "ValueError"
    assert "must not be returned" not in library.lookup()


def test_missing_notes_do_not_break_casting_or_private_archive_permissions(tmp_path):
    plugin = _make_enabled_plugin()
    plugin.readings.study_references = StudyReferenceLibrary(tmp_path, plugin.readings.directions)
    plugin.config["agent_generate_chart_comment"] = False
    result = asyncio.run(plugin.cast_liuyao_tool(_Event("member"), intent="weather"))
    assert "本卦：" in result and "lookup_liuyao_reference" in result
    assert "未加载" in asyncio.run(plugin.lookup_liuyao_reference_tool(_Event("member"), "天气"))
    assert "仅 AstrBot 管理员" in asyncio.run(plugin._case_browser(_Event("member"), ""))


def test_original_mistakes_and_system_boundaries_remain_visible():
    library = _make_enabled_plugin().readings.study_references
    assert "原断失误" in library.lookup("事业")
    travel = library.lookup("出行")
    assert "七月必到" in travel and "后于亥月方到" in travel
    assert "后验" in travel and "初不知应风阻" in travel
    assert "不能标为纳甲实证" in library.lookup("射覆")
    assert "未另单列" in library.lookup("财富")
