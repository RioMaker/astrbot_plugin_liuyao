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
