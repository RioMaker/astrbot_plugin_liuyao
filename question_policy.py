"""Local question checks; Agent tools also enforce the semantic rule."""

import re
import unicodedata


def normalize_question(question: str) -> str:
    text = unicodedata.normalize("NFKC", str(question or "")).casefold()
    return "".join(char for char in text if char.isalnum())


def question_error(question: str) -> str:
    text = normalize_question(question)
    vague = {
        "",
        "没事",
        "无事",
        "没有事",
        "随便",
        "看看",
        "测试",
        "测试一下",
        "试一下",
        "试试",
        "占卜",
        "起卦",
        "算一卦",
        "帮我算一卦",
        "为我起一卦",
        "帮我起卦",
        "帮我起一卦",
        "起一卦",
        "运势",
        "近期运势如何",
        "今日运势",
        "事业",
        "感情",
        "财富",
        "学业",
        "健康",
        "家庭",
        "出行",
        "综合",
    }
    casual = re.search(
        r"随便.{0,4}(看|算|占|卜|起|玩|测)|没(有)?(什么|啥)事|没事[就只想来]|"
        r"无事[可想就]|无聊|玩玩|闹着玩|纯属好奇|只是好奇|娱乐一下",
        text,
    )
    if text in vague or casual or len(text) < 3:
        return "无事不卜：请先说明确实想问的具体事情；只是随便看看、消遣或测试，不予起卦。"
    return ""
