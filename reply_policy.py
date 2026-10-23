"""Pure reply rules: length budget, verdict preservation and text/image choice."""

from __future__ import annotations

import re

IMAGE_MODES = ("关闭", "超长时", "始终")
DEFAULT_IMAGE_MODE = "超长时"
DEFAULT_MAX_CHARS = 280
DEFAULT_IMAGE_THRESHOLD = 200

_VERDICT_LINE = re.compile(r"(?m)^[ \t]*断语\s*[：:]\s*(?P<verdict>.+?)[ \t]*$")
_SENTENCE_END = "。！？；!?;\n"
_TRAILING_JUNK = "，、；：,;: \n"


def split_verdict(text: str) -> tuple[str, str]:
    """Return the reply without its final “断语：” line, plus that verdict."""
    body = str(text or "").strip()
    matches = list(_VERDICT_LINE.finditer(body))
    if not matches:
        return body, ""
    last = matches[-1]
    verdict = last.group("verdict").strip()
    if not verdict:
        return body, ""
    remainder = (body[: last.start()] + body[last.end() :]).strip()
    return remainder, verdict


def limit_reply(text: str, max_chars: int, *, trim: bool = True) -> str:
    """Cut an over-long reply by sentence, keeping the verifiable verdict line."""
    reply = str(text or "").strip()
    if not trim or max_chars <= 0 or len(reply) <= max_chars:
        return reply

    body, verdict = split_verdict(reply)
    if not verdict:
        return _clip(reply, max_chars)

    reserved = len(verdict) + len("断语：") + 1
    budget = max_chars - reserved
    if budget < 40:
        # The verdict alone nearly fills the limit; it is the part worth keeping.
        return f"断语：{verdict}"
    clipped = _clip(body, budget)
    return f"{clipped}\n断语：{verdict}" if clipped else f"断语：{verdict}"


def normalize_image_mode(value: object) -> str:
    """Accept the WebUI options plus a few forgiving spellings."""
    text = str(value or "").strip()
    if text in IMAGE_MODES:
        return text
    lowered = text.lower()
    if lowered in {"off", "none", "never", "关", "禁用"}:
        return "关闭"
    if lowered in {"always", "all", "一直", "一律"}:
        return "始终"
    if lowered in {"long", "auto", "超长", "超长时转图片"}:
        return "超长时"
    return DEFAULT_IMAGE_MODE


def should_render_card(mode: object, length: int, threshold: int) -> bool:
    """Decide whether this reply is delivered as a card image."""
    resolved = normalize_image_mode(mode)
    if resolved == "关闭" or length <= 0:
        return False
    if resolved == "始终":
        return True
    return length >= max(0, int(threshold))


def _clip(text: str, budget: int) -> str:
    text = str(text or "").strip()
    if len(text) <= budget:
        return text
    cut = 0
    for index in range(min(budget, len(text) - 1), 0, -1):
        if text[index - 1] in _SENTENCE_END:
            cut = index
            break
    if cut < budget * 0.6:
        return text[:budget].rstrip(_TRAILING_JUNK) + "…"
    return text[:cut].rstrip()
