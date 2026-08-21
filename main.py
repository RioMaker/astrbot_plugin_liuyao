"""AstrBot QQ-group six-line (六爻) divination plugin."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

if __package__:
    from .corpus import CorpusError, ZhouyiCorpus
    from .divination import (
        ManualCastError,
        cast_instant,
        extract_subcommand_tail,
        parse_intent_and_question,
        parse_manual_cast,
        split_manual_payload,
    )
    from .reading import ReadingService
else:  # pragma: no cover - direct local execution
    from corpus import CorpusError, ZhouyiCorpus
    from divination import (
        ManualCastError,
        cast_instant,
        extract_subcommand_tail,
        parse_intent_and_question,
        parse_manual_cast,
        split_manual_payload,
    )
    from reading import ReadingService


PLUGIN_NAME = "astrbot_plugin_liuyao"
PLUGIN_AUTHOR = "Rio"
PLUGIN_DESC = "面向 QQ 群的六爻问卦：即时起卦、手动铜币起卦、群主开关与 Agent Tool"
PLUGIN_VERSION = "0.1.0"
PLUGIN_REPO = "https://github.com/RioMaker/astrbot_plugin_liuyao"
SWITCHES_KEY = "group_switches"

HELP_TEXT = """六爻问卦插件
指令：
/问卦 即时 [方向] [问题]
  例：/问卦 即时 事业 今年是否适合换工作
/问卦 手动 <六爻> [方向] [问题]
  例：/问卦 手动 7 8 9 6 7 8 感情 这段关系该如何推进
  六爻必须按“初爻→上爻”（自下而上）填写。
  数字：6老阴、7少阳、8少阴、9老阳。
  也可输入六组三币，如：正反反 正正反 正正正 反反反 正反反 正正反
  约定：正/字/阳/H=3，反/花/阴/T=2。
/问卦 开关 开|关|状态
  仅当前 QQ 群群主可控制。
/问卦 help
方向：综合、事业、感情、财富、学业、健康、家庭、出行。
也可直接对 Agent 说“为我起一卦问事业”，由 Agent 调用 cast_liuyao。
说明：结果用于传统文化体验与自我反思，不替代现实决策或专业意见。"""


@register(
    PLUGIN_NAME,
    PLUGIN_AUTHOR,
    PLUGIN_DESC,
    PLUGIN_VERSION,
    PLUGIN_REPO,
)
class LiuyaoPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.config = context.get_config() or {}
        self.plugin_dir = Path(__file__).resolve().parent
        self.corpus = ZhouyiCorpus(self.plugin_dir / "data" / "zhouyi.json")
        self.readings = ReadingService(
            self.corpus,
            self.plugin_dir / "data" / "intents.json",
        )
        self._switch_lock = asyncio.Lock()
        logger.info("liuyao：已加载 64 卦与 384 条基础爻辞")

    @filter.command_group("问卦", alias={"六爻", "liunyao"})
    def liuyao():
        """六爻问卦指令组。"""

    @liuyao.command("help", alias={"帮助", "说明"})
    async def help_command(self, event: AstrMessageEvent):
        """查看六爻问卦插件帮助。"""
        yield event.plain_result(HELP_TEXT)

    @liuyao.command("即时", alias={"天机", "instant"})
    async def instant_command(self, event: AstrMessageEvent):
        """即时天机起卦，不需要手摇铜币。"""
        error = await self._group_gate(event)
        if error:
            yield event.plain_result(error)
            return

        tail = extract_subcommand_tail(
            self._message_text(event),
            {"即时", "天机", "instant"},
        )
        intent, question = parse_intent_and_question(tail)
        cast = cast_instant()
        yield event.plain_result(
            self.readings.render(
                cast,
                intent=intent,
                question=self._limited_question(question),
                method="即时天机（三枚铜币等概率模拟）",
                show_disclaimer=self._show_disclaimer(),
            )
        )

    @liuyao.command("手动", alias={"manual", "铜币"})
    async def manual_command(self, event: AstrMessageEvent):
        """按用户手摇铜币的结果起卦。"""
        error = await self._group_gate(event)
        if error:
            yield event.plain_result(error)
            return

        tail = extract_subcommand_tail(
            self._message_text(event),
            {"手动", "manual", "铜币"},
        )
        try:
            manual_text, remainder = split_manual_payload(tail)
            cast = parse_manual_cast(manual_text)
        except ManualCastError as exc:
            yield event.plain_result(
                f"手动起卦输入有误：{exc}\n"
                "示例：/问卦 手动 7 8 9 6 7 8 事业 是否适合换工作"
            )
            return

        intent, question = parse_intent_and_question(remainder)
        yield event.plain_result(
            self.readings.render(
                cast,
                intent=intent,
                question=self._limited_question(question),
                method="手动铜币",
                show_disclaimer=self._show_disclaimer(),
            )
        )

    @liuyao.command("开关", alias={"switch"})
    async def switch_command(self, event: AstrMessageEvent):
        """仅 QQ 群群主可查看或改变本群插件开关。"""
        group_id = str(event.get_group_id() or "").strip()
        if not group_id:
            yield event.plain_result("开关指令只能在 QQ 群聊中使用。")
            return
        if not await self._is_group_owner(event):
            yield event.plain_result("无权限：只有当前 QQ 群群主可以控制问卦开关。")
            return

        tail = extract_subcommand_tail(
            self._message_text(event),
            {"开关", "switch"},
        ).lower()
        if tail in {"", "状态", "status"}:
            enabled = await self._is_group_enabled(group_id)
            yield event.plain_result(f"本群问卦功能当前为：{'开启' if enabled else '关闭'}。")
            return
        if tail in {"开", "开启", "启用", "on", "true", "1"}:
            await self._set_group_enabled(group_id, True)
            yield event.plain_result("本群问卦功能已开启。")
            return
        if tail in {"关", "关闭", "停用", "off", "false", "0"}:
            await self._set_group_enabled(group_id, False)
            yield event.plain_result("本群问卦功能已关闭。")
            return
        yield event.plain_result("用法：/问卦 开关 开|关|状态")

    @filter.llm_tool(name="cast_liuyao")
    async def cast_liuyao_tool(
        self,
        event: AstrMessageEvent,
        mode: str = "instant",
        intent: str = "general",
        question: str = "",
        manual_lines: str = "",
    ) -> str:
        """为当前 QQ 群会话起六爻卦，并返回可供 Agent 解读的完整古籍上下文。

        Args:
            mode(string): 起卦方式，instant=即时天机；manual=使用用户手摇结果
            intent(string): 问卦方向，可选 general/career/relationship/wealth/study/health/family/travel 或对应中文
            question(string): 用户所问的具体问题，可省略
            manual_lines(string): mode=manual 时必填，自下而上的六个 6/7/8/9，例如“7 8 9 6 7 8”
        """
        error = await self._group_gate(event)
        if error:
            return error

        normalized_mode = (mode or "instant").strip().lower()
        if normalized_mode in {"instant", "即时", "天机"}:
            cast = cast_instant()
            method = "即时天机（三枚铜币等概率模拟）"
        elif normalized_mode in {"manual", "手动", "铜币"}:
            try:
                cast = parse_manual_cast(manual_lines)
            except ManualCastError as exc:
                return f"手动起卦输入有误：{exc}"
            method = "手动铜币"
        else:
            return "mode 只能是 instant 或 manual"

        return self.readings.render(
            cast,
            intent=intent,
            question=self._limited_question(question),
            method=method,
            for_agent=True,
            show_disclaimer=True,
        )

    @filter.llm_tool(name="lookup_zhouyi_text")
    async def lookup_zhouyi_text_tool(
        self,
        event: AstrMessageEvent,
        hexagram: int,
        line: int = 0,
    ) -> str:
        """查询插件内置的《周易》卦辞或指定爻辞，供 Agent 核对原文。

        Args:
            hexagram(number): 文王卦序，1 到 64
            line(number): 0 返回卦辞；1 到 6 返回初爻至上爻的对应爻辞
        """
        error = await self._group_gate(event)
        if error:
            return error
        try:
            return self.corpus.lookup_text(int(hexagram), int(line))
        except (CorpusError, TypeError, ValueError) as exc:
            return f"查询参数错误：{exc}"

    async def _group_gate(self, event: AstrMessageEvent) -> str:
        group_id = str(event.get_group_id() or "").strip()
        if not group_id:
            return "六爻问卦仅面向 QQ 群聊使用。"
        if not await self._is_group_enabled(group_id):
            return "本群问卦功能尚未开启，请群主发送 /问卦 开关 开。"
        return ""

    async def _is_group_enabled(self, group_id: str) -> bool:
        switches = await self.get_kv_data(SWITCHES_KEY, {})
        if not isinstance(switches, dict):
            switches = {}
        return bool(switches.get(group_id, self._default_enabled()))

    async def _set_group_enabled(self, group_id: str, enabled: bool) -> None:
        async with self._switch_lock:
            switches = await self.get_kv_data(SWITCHES_KEY, {})
            if not isinstance(switches, dict):
                switches = {}
            updated = dict(switches)
            updated[str(group_id)] = bool(enabled)
            await self.put_kv_data(SWITCHES_KEY, updated)

    async def _is_group_owner(self, event: AstrMessageEvent) -> bool:
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        sender = self._raw_get(raw, "sender")
        role = str(
            self._raw_get(sender, "role")
            or self._raw_get(raw, "role")
            or ""
        ).lower()
        if role == "owner":
            return True
        if role:
            return False

        if not bool(self._config_get("allow_owner_api_lookup", True)):
            return False
        client = getattr(event, "bot", None)
        call_action = getattr(client, "call_action", None)
        if not callable(call_action):
            return False
        group_id = str(event.get_group_id() or "")
        user_id = str(event.get_sender_id() or "")
        try:
            result = await call_action(
                action="get_group_member_info",
                group_id=int(group_id) if group_id.isdigit() else group_id,
                user_id=int(user_id) if user_id.isdigit() else user_id,
                no_cache=True,
            )
            data = self._raw_get(result, "data") or result
            return str(self._raw_get(data, "role") or "").lower() == "owner"
        except Exception as exc:
            logger.warning(f"liuyao：查询群主身份失败：{exc}")
            return False

    def _default_enabled(self) -> bool:
        return bool(self._config_get("default_enabled", False))

    def _show_disclaimer(self) -> bool:
        return bool(self._config_get("show_disclaimer", True))

    def _limited_question(self, value: str) -> str:
        limit = int(self._config_get("max_question_length", 200) or 200)
        limit = min(max(limit, 20), 1000)
        return (value or "").strip()[:limit]

    def _config_get(self, key: str, default: Any) -> Any:
        getter = getattr(self.config, "get", None)
        if callable(getter):
            return getter(key, default)
        return default

    @staticmethod
    def _message_text(event: AstrMessageEvent) -> str:
        getter = getattr(event, "get_message_str", None)
        if callable(getter):
            return str(getter() or "")
        return str(getattr(event, "message_str", "") or "")

    @staticmethod
    def _raw_get(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        getter = getattr(obj, "get", None)
        if callable(getter):
            try:
                value = getter(key)
                if value is not None:
                    return value
            except Exception:
                pass
        return getattr(obj, key, None)

    async def terminate(self):
        logger.info("liuyao 插件已卸载")
