"""Local question checks; Agent tools also enforce the semantic rule."""

import re
import unicodedata

NO_VALID_QUESTION_NOTICE = (
    "未识别到有效问题，本次仅提供普通排盘，不调用 AI 解卦，也不消耗 AI 解卦次数。"
    "请说明想问的具体事情，例如“这个项目能否在月底前完成”。"
)


def normalize_question(question: str) -> str:
    text = unicodedata.normalize("NFKC", str(question or "")).casefold()
    return "".join(char for char in text if char.isalnum())


def is_obvious_non_question(question: str) -> bool:
    """Catch only empty/boilerplate input; leave other wording to semantic review."""
    text = normalize_question(question)
    return not text or bool(
        re.fullmatch(
            r"(?:请|麻烦|劳驾|可以|能不能|能否|帮忙|帮我|替我|给我|为我|我想|我要|想要|"
            r"普通|随机|来|一个|一下|一次|个|吧|吗|呢|呀|啊|呗|谢谢|"
            r"六爻|起卦|起一卦|算卦|算一卦|占卜|排盘|解卦|解读|"
            r"随便|看看|看一看|测试|试试|试一下|玩玩|无聊|没事|无事|没有事|"
            r"运势|近期运势如何|今日运势|事业|感情|财富|学业|健康|家庭|出行|综合|"
            r"未提供具体内容)+",
            text,
        )
    )


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
