"""Build the offline corpus from the public MediaWiki API.

Run only during development. The AstrBot plugin never needs network access.
The API is queried in two batches to avoid unnecessary load.
"""

from __future__ import annotations

from datetime import date
import html
from http.client import RemoteDisconnected
import json
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API = "https://zh.wikisource.org/w/api.php"
SOURCE_ROOT = "https://zh.wikisource.org/wiki/周易/"
USER_AGENT = "AstrBot-Liuyao-DataBuilder/0.1 (offline corpus build)"

NAMES = (
    "乾", "坤", "屯", "蒙", "需", "訟", "師", "比", "小畜", "履", "泰", "否", "同人", "大有", "謙", "豫",
    "隨", "蠱", "臨", "觀", "噬嗑", "賁", "剝", "復", "无妄", "大畜", "頤", "大過", "坎", "離", "咸", "恒",
    "遯", "大壯", "晉", "明夷", "家人", "睽", "蹇", "解", "損", "益", "夬", "姤", "萃", "升", "困", "井",
    "革", "鼎", "震", "艮", "漸", "歸妹", "豐", "旅", "巽", "兌", "渙", "節", "中孚", "小過", "既濟", "未濟",
)

TRIGRAM_ORDER = ("乾", "兌", "離", "震", "巽", "坎", "艮", "坤")
TRIGRAM_BITS = {
    "乾": (1, 1, 1), "兌": (1, 1, 0), "離": (1, 0, 1), "震": (1, 0, 0),
    "巽": (0, 1, 1), "坎": (0, 1, 0), "艮": (0, 0, 1), "坤": (0, 0, 0),
}
MATRIX = (
    (1, 43, 14, 34, 9, 5, 26, 11), (10, 58, 38, 54, 61, 60, 41, 19),
    (13, 49, 30, 55, 37, 63, 22, 36), (25, 17, 21, 51, 42, 3, 27, 24),
    (44, 28, 50, 32, 57, 48, 18, 46), (6, 47, 64, 40, 59, 29, 4, 7),
    (33, 31, 56, 62, 53, 39, 52, 15), (12, 45, 35, 16, 20, 8, 23, 2),
)


def fetch_pages() -> dict[str, dict]:
    collected: dict[str, dict] = {}
    for offset in range(0, len(NAMES), 32):
        names = NAMES[offset : offset + 32]
        query = urlencode(
            {
                "action": "query",
                "prop": "revisions",
                "titles": "|".join(f"周易/{name}" for name in names),
                "rvprop": "ids|content",
                "rvslots": "main",
                "format": "json",
                "formatversion": 2,
                "maxlag": 5,
            }
        )
        request = Request(f"{API}?{query}", headers={"User-Agent": USER_AGENT})
        payload = None
        for attempt in range(4):
            try:
                with urlopen(request, timeout=45) as response:
                    payload = json.load(response)
                break
            except (HTTPError, URLError, RemoteDisconnected) as exc:
                if isinstance(exc, HTTPError) and exc.code not in {429, 500, 502, 503}:
                    raise
                if attempt == 3:
                    raise
                time.sleep(4 * (attempt + 1))
        if not payload or "query" not in payload:
            raise RuntimeError(f"MediaWiki API response missing query: {payload!r}")
        for page in payload["query"]["pages"]:
            if "missing" in page or not page.get("revisions"):
                raise RuntimeError(f"missing Wikisource page: {page.get('title', '').encode('unicode_escape').decode('ascii')}")
            name = page["title"].split("/", 1)[1]
            revision = page["revisions"][0]
            collected[name] = {
                "wikitext": revision["slots"]["main"]["content"],
                "revid": revision["revid"],
            }
        time.sleep(1)
    if len(collected) != 64:
        raise RuntimeError(f"expected 64 pages, got {len(collected)}")
    return collected


def clean_markup(value: str) -> str:
    text = re.sub(r"-\{([^{}]+)\}-", r"\1", value)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\{\*\|([^{}]+)\}\}", r"（\1）", text)
    text = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", text)
    text = text.replace("'''", "").replace("''", "")
    return html.unescape(text).strip()


def parse_classic(number: int, name: str, wikitext: str) -> tuple[str, list[str], list[str]]:
    marker = wikitext.find("易經：")
    if marker < 0:
        raise ValueError(f"hexagram={number}: classic section not found")
    end = wikitext.find("彖曰：", marker)
    if end < 0:
        raise ValueError(f"hexagram={number}: tuan boundary not found")
    section = wikitext[marker:end]

    judgment_parts: list[str] = []
    line_texts: list[str] = []
    found_judgment = False
    for raw_line in section.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("*#"):
            line_texts.append(clean_markup(stripped[2:]))
            found_judgment = True
            continue
        if stripped.startswith("*") and not found_judgment:
            candidate = clean_markup(stripped.lstrip("*"))
            if "：" in candidate and not judgment_parts:
                candidate = candidate.split("：", 1)[1].strip()
            if candidate and "易經：" not in candidate:
                judgment_parts.append(candidate)

    expected = 7 if name in {"乾", "坤"} else 6
    judgment = "".join(judgment_parts)
    if not judgment or len(line_texts) != expected:
        raise ValueError(
            f"hexagram={number}: judgment={bool(judgment)} lines={len(line_texts)} expected={expected}"
        )
    return judgment, line_texts[:6], line_texts[6:]


def trigram_pair(number: int) -> tuple[str, str]:
    for lower_index, row in enumerate(MATRIX):
        for upper_index, candidate in enumerate(row):
            if candidate == number:
                return TRIGRAM_ORDER[lower_index], TRIGRAM_ORDER[upper_index]
    raise ValueError(f"missing matrix entry for {number}")


def build() -> dict:
    pages = fetch_pages()
    rows = []
    revisions = {}
    for number, name in enumerate(NAMES, start=1):
        page = pages[name]
        judgment, lines, extra_lines = parse_classic(number, name, page["wikitext"])
        lower, upper = trigram_pair(number)
        bits = TRIGRAM_BITS[lower] + TRIGRAM_BITS[upper]
        rows.append(
            {
                "number": number,
                "name": name,
                "symbol": chr(0x4DBF + number),
                "lower_trigram": lower,
                "upper_trigram": upper,
                "binary_bottom_up": "".join(str(bit) for bit in bits),
                "judgment": judgment,
                "lines": lines,
                "extra_lines": extra_lines,
                "source_url": f"{SOURCE_ROOT}{name}",
                "source_revision": page["revid"],
            }
        )
        revisions[name] = page["revid"]
    return {
        "schema_version": 1,
        "source": {
            "title": "《周易》卦辞、爻辞（维基文库逐卦页）",
            "root_url": "https://zh.wikisource.org/wiki/周易",
            "retrieved_on": date.today().isoformat(),
            "license_note": "古籍原文属于公版；页面编排按 CC BY-SA 4.0 署名维基文库。",
            "revisions": revisions,
        },
        "hexagrams": rows,
    }


if __name__ == "__main__":
    output = Path(__file__).resolve().parents[1] / "data" / "zhouyi.json"
    output.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output}")




