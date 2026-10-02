"""Build the bundled title font from the documented local Noto source."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA256 = "d627b53dbcde61e07de1498d2623a8b287f78585ffbc90cc0618d0caaa2ed6b0"
FIXED_TEXT = (
    "六爻排盘问卦辞与动爻静卦为天泽火雷风水山地年月日时柱"
    "乾兑离震巽坎艮坤甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥→ ·0123456789"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != SOURCE_SHA256:
        parser.error("源字体 SHA-256 不符合 assets/fonts/README.md 记录")
    corpus = json.loads((ROOT / "data/zhouyi.json").read_text(encoding="utf-8"))
    font = TTFont(args.source)
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.name_legacy = True
    subsetter = subset.Subsetter(options=options)
    subsetter.populate(text=json.dumps(corpus["hexagrams"], ensure_ascii=False) + FIXED_TEXT)
    subsetter.subset(font)
    names = {
        1: "Liuyao Editorial Serif",
        16: "Liuyao Editorial Serif",
        3: "LiuyaoEditorialSerif-SemiBold-1",
        4: "Liuyao Editorial Serif SemiBold",
        6: "LiuyaoEditorialSerif-SemiBold",
    }
    for record in font["name"].names:
        if record.nameID in names:
            record.string = names[record.nameID].encode(record.getEncoding())
    cff = font["CFF "].cff
    cff.fontNames = ["LiuyaoEditorialSerif-SemiBold"]
    cff.topDictIndex[0].FamilyName = "Liuyao Editorial Serif"
    cff.topDictIndex[0].FullName = "Liuyao Editorial Serif SemiBold"
    font.save(ROOT / "assets/fonts/LiuyaoEditorialSerif.otf")


if __name__ == "__main__":
    main()
