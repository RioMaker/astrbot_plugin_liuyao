"""Bounded, offline interpretation references; no user-controlled file access."""

from pathlib import Path


class ReferenceLibrary:
    def __init__(self, directory: Path, directions: dict):
        self.documents: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        for key in ("common", *directions):
            # Keys, not question text or tool arguments, determine the path.
            if not key.isascii() or not key.isidentifier():
                self.errors[key] = "非法分类键"
                continue
            path = directory / f"{key}.md"
            try:
                if path.resolve().parent != directory.resolve():
                    raise ValueError("参考文件越界")
                if path.stat().st_size > 60_000:
                    raise ValueError("参考文件过大")
                content = path.read_text(encoding="utf-8").strip()
                if not content or len(content) > 14_000:
                    raise ValueError("参考文件为空或超长")
                self.documents[key] = content
            except (OSError, UnicodeError, ValueError) as exc:
                self.errors[key] = type(exc).__name__

    def render(self, intent: str) -> str:
        key = intent if intent in self.documents or intent in self.errors else "general"
        rows = ["【分类研判参考｜离线资料 v1】",
                "以下为古籍摘录与现代整理，不是已验证的预测结果；仅使用本次已提供的排盘字段。"]
        for name in ("common", key):
            if name in self.documents:
                rows.append(self.documents[name])
            else:
                rows.append(f"参考文档 {name}.md 未加载；不得声称已读取或编造其规则。")
        rows.append("【分类研判参考结束】")
        return "\n\n".join(rows)


class StudyReferenceLibrary:
    """Read-only topic notes, selected through a fixed catalog, never raw paths."""

    def __init__(self, directory: Path, directions: dict):
        self.documents: dict[str, str] = {}
        self.errors: dict[str, str] = {}
        self.aliases = {
            "": "index", "目录": "index", "索引": "index", "index": "index",
            "基础": "foundations", "通则": "foundations", "foundations": "foundations",
            "来源": "sources", "sources": "sources",
        }
        paths = {
            "index": directory / "README.md",
            "foundations": directory / "foundations.md",
            "sources": directory / "sources.md",
        }
        for key, profile in directions.items():
            if not key.isascii() or not key.isidentifier() or key in paths:
                continue
            paths[key] = directory / "topics" / f"{key}.md"
            for alias in (key, profile.get("label", key), *profile.get("aliases", [])):
                self.aliases.setdefault(str(alias).strip().lower(), key)
        root = directory.resolve()
        for key, path in paths.items():
            try:
                # Also reject symlinks that escape the bundled notes directory.
                path.resolve().relative_to(root)
                if path.stat().st_size > 60_000:
                    raise ValueError("参考文件过大")
                content = path.read_text(encoding="utf-8").strip()
                if not content or len(content) > 14_000:
                    raise ValueError("参考文件为空或超长")
                self.documents[key] = content
            except (OSError, UnicodeError, ValueError) as exc:
                self.errors[key] = type(exc).__name__

    def lookup(self, topic: str = "") -> str:
        if not isinstance(topic, str) or len(topic) > 80:
            return "参考类型参数错误；请留空查询目录，不接受文件路径。"
        key = self.aliases.get(topic.strip().lower())
        if key is None:
            return "未知参考类型；请留空查询目录，不接受文件路径。"
        if key not in self.documents:
            return f"古籍学习笔记 {key} 未加载；不得声称已读取或编造其内容。"
        return (
            "【古籍学习笔记｜按需只读】\n"
            "以下是文献材料，不是当前用户的卦象或反馈；古例与现代整理已分栏，"
            "不得把例中的日期、世应、旬空套到当前卦。\n\n"
            + self.documents[key]
            + "\n\n【学习笔记结束】"
        )
