"""AstrBot QQ-group six-line (六爻) divination plugin."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import GreedyStr

if __package__:
    from .corpus import CorpusError, ZhouyiCorpus
    from .divination import (
        CastResult,
        ManualCastError,
        cast_instant,
        parse_intent_and_question,
        parse_manual_cast,
        split_manual_payload,
    )
    from .reading import ReadingService
else:  # pragma: no cover - direct local execution
    from corpus import CorpusError, ZhouyiCorpus
    from divination import (
        CastResult,
        ManualCastError,
        cast_instant,
        parse_intent_and_question,
        parse_manual_cast,
        split_manual_payload,
    )
    from reading import ReadingService


PLUGIN_NAME = "astrbot_plugin_liuyao"
PLUGIN_AUTHOR = "Rio"
PLUGIN_DESC = "面向 QQ 群的六爻起卦：即时、手动、分术数群开关与 Agent Tool"
PLUGIN_VERSION = "0.2.1"
PLUGIN_REPO = "https://github.com/RioMaker/astrbot_plugin_liuyao"

METHOD_SWITCHES_KEY = "method_switches"
LEGACY_SWITCHES_KEY = "group_switches"
METHOD_LIUYAO = "liuyao"
METHOD_LABELS = {METHOD_LIUYAO: "六爻"}

HELP_TEXT = """六爻起卦插件
等价指令入口：
/六爻 ...
/起卦 六爻 ...

起卦：
/六爻 <问卦内容>
  例：/六爻 今年适合换工作吗
  例：/六爻 事业 今年适合换工作吗
/起卦 六爻 <问卦内容>
/六爻 即时 [方向] [问题]
  例：/六爻 即时 事业 今年是否适合换工作
/六爻 手动 <六爻或卦名> [方向] [问题]
  例：/六爻 手动 7 8 9 6 7 8 感情 这段关系该如何推进
  例：/六爻 手动 乾为天 事业 这个项目如何推进
  六爻数字必须按“初爻→上爻”（自下而上）填写。
  数字：6老阴、7少阳、8少阴、9老阳。
  也可输入六组三币，如：正反反 正正反 正正正 反反反 正反反 正正反
  约定：正/字/阳/H=3，反/花/阴/T=2。
  卦名支持：乾、乾为天、天风姤、第1卦、䷀ 等；直接指定卦名按无动爻静卦处理。

开关（按术数分别保存）：
/六爻 开
/六爻 关
/六爻 状态
/起卦 六爻 开|关|状态
  当前只有“六爻”一种术数；QQ 群主或 QQ 群管理员可以设置。

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

    # ------------------------------------------------------------------
    # 两条等价根指令：自由文本与保留子命令都由插件统一分派。
    # ------------------------------------------------------------------
    @filter.command("六爻", alias={"liunyao"})
    async def liuyao_command(
        self,
        event: AstrMessageEvent,
        content: GreedyStr,
    ):
        """六爻根指令；未命中保留子命令时，将全部参数作为问卦内容。"""
        yield event.plain_result(
            await self._dispatch_liuyao(event, str(content or ""))
        )

    @filter.command("起卦", alias={"divination"})
    async def divination_command(
        self,
        event: AstrMessageEvent,
        content: GreedyStr,
    ):
        """术数总入口；当前支持 /起卦 六爻 ...。"""
        parts = str(content or "").strip().split(maxsplit=1)
        if not parts or parts[0].lower() not in {"六爻", "liunyao"}:
            yield event.plain_result("当前仅支持六爻。\n\n" + HELP_TEXT)
            return
        liuyao_tail = parts[1].strip() if len(parts) == 2 else ""
        yield event.plain_result(
            await self._dispatch_liuyao(event, liuyao_tail)
        )
    # ------------------------------------------------------------------
    # Agent tools
    # ------------------------------------------------------------------
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
            mode(string): 起卦方式，instant=即时天机；manual=手摇结果或直接指定卦名
            intent(string): 问卦方向，可选 general/career/relationship/wealth/study/health/family/travel 或对应中文
            question(string): 用户所问的具体问题，可省略
            manual_lines(string): mode=manual 时填写六个 6/7/8/9，或填写乾、乾为天、第1卦等卦名
        """
        error = await self._group_gate(event, METHOD_LIUYAO)
        if error:
            return error

        normalized_mode = (mode or "instant").strip().lower()
        if normalized_mode in {"instant", "即时", "天机"}:
            cast = cast_instant()
            method = "即时天机（三枚铜币等概率模拟）"
        elif normalized_mode in {"manual", "手动", "铜币"}:
            try:
                cast = parse_manual_cast(manual_lines)
                method = "手动铜币"
            except ManualCastError:
                try:
                    row = self.corpus.resolve(manual_lines)
                except CorpusError as exc:
                    return f"手动起卦输入有误：{exc}"
                cast = self._cast_from_hexagram(row)
                method = f"手动指定卦名（{row['name']}静卦）"
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
        error = await self._group_gate(event, METHOD_LIUYAO)
        if error:
            return error
        try:
            return self.corpus.lookup_text(int(hexagram), int(line))
        except (CorpusError, TypeError, ValueError) as exc:
            return f"查询参数错误：{exc}"

    # ------------------------------------------------------------------
    # Shared command implementations
    # ------------------------------------------------------------------
    async def _dispatch_liuyao(
        self,
        event: AstrMessageEvent,
        tail: str,
    ) -> str:
        """分派保留子命令；其他全部文本按即时问卦内容处理。"""
        text = (tail or "").strip()
        if not text:
            return HELP_TEXT

        parts = text.split(maxsplit=1)
        token = parts[0].lower()
        remainder = parts[1].strip() if len(parts) == 2 else ""

        if token in {"help", "帮助", "说明"}:
            return HELP_TEXT
        if token in {"即时", "天机", "instant"}:
            return await self._instant_reply(event, remainder)
        if token in {"手动", "manual", "铜币"}:
            return await self._manual_reply(event, remainder)
        if token in {"开", "开启", "启用", "on"}:
            return await self._switch_reply(event, METHOD_LIUYAO, True)
        if token in {"关", "关闭", "停用", "off"}:
            return await self._switch_reply(event, METHOD_LIUYAO, False)
        if token in {"状态", "status"}:
            return await self._switch_reply(event, METHOD_LIUYAO, None)
        if token in {"开关", "switch"}:
            desired = self._parse_switch_state(remainder)
            if desired == "invalid":
                return (
                    "用法：/六爻 开|关|状态"
                    "（旧写法：/六爻 开关 开|关|状态）"
                )
            return await self._switch_reply(event, METHOD_LIUYAO, desired)

        return await self._content_reply(event, text)

    async def _content_reply(
        self,
        event: AstrMessageEvent,
        content: str,
    ) -> str:
        """把自由文本解析为可选方向和问题，并直接即时起卦。"""
        error = await self._group_gate(event, METHOD_LIUYAO)
        if error:
            return error
        intent, question = parse_intent_and_question(content)
        return self.readings.render(
            cast_instant(),
            intent=intent,
            question=self._limited_question(question),
            method="即时天机（简捷问卦）",
            show_disclaimer=self._show_disclaimer(),
        )

    async def _instant_reply(
        self,
        event: AstrMessageEvent,
        tail: str,
    ) -> str:
        error = await self._group_gate(event, METHOD_LIUYAO)
        if error:
            return error
        intent, question = parse_intent_and_question(tail)
        return self.readings.render(
            cast_instant(),
            intent=intent,
            question=self._limited_question(question),
            method="即时天机（三枚铜币等概率模拟）",
            show_disclaimer=self._show_disclaimer(),
        )

    async def _manual_reply(
        self,
        event: AstrMessageEvent,
        tail: str,
    ) -> str:
        error = await self._group_gate(event, METHOD_LIUYAO)
        if error:
            return error

        try:
            manual_text, remainder = split_manual_payload(tail)
            cast = parse_manual_cast(manual_text)
            method = "手动铜币"
        except ManualCastError as numeric_error:
            parts = tail.split(maxsplit=1)
            if not parts or parts[0].isdigit():
                return self._manual_error(numeric_error)
            try:
                row = self.corpus.resolve(parts[0])
            except CorpusError:
                return self._manual_error(numeric_error)
            cast = self._cast_from_hexagram(row)
            remainder = parts[1].strip() if len(parts) == 2 else ""
            method = f"手动指定卦名（{row['name']}静卦）"

        intent, question = parse_intent_and_question(remainder)
        return self.readings.render(
            cast,
            intent=intent,
            question=self._limited_question(question),
            method=method,
            show_disclaimer=self._show_disclaimer(),
        )
    async def _switch_reply(
        self,
        event: AstrMessageEvent,
        method: str,
        desired: bool | None,
    ) -> str:
        group_id = str(event.get_group_id() or "").strip()
        if not group_id:
            return "开关指令只能在 QQ 群聊中使用。"
        if not await self._is_group_operator(event):
            return "无权限：只有当前 QQ 群主或 QQ 群管理员可以设置术数开关。"

        label = METHOD_LABELS.get(method, method)
        if desired is None:
            enabled = await self._is_method_enabled(group_id, method)
            return f"本群“{label}”功能当前为：{'开启' if enabled else '关闭'}。"
        await self._set_method_enabled(group_id, method, desired)
        return f"本群“{label}”功能已{'开启' if desired else '关闭'}。"

    def _manual_error(self, exc: Exception) -> str:
        return (
            f"手动起卦输入有误：{exc}\n"
            "示例一：/六爻 手动 7 8 9 6 7 8 事业 是否适合换工作\n"
            "示例二：/六爻 手动 乾为天 事业 这个项目如何推进"
        )

    @staticmethod
    def _cast_from_hexagram(row: dict[str, Any]) -> CastResult:
        bits = str(row["binary_bottom_up"])
        if len(bits) != 6 or any(bit not in {"0", "1"} for bit in bits):
            raise CorpusError("卦象二进制数据无效")
        lines = tuple(7 if bit == "1" else 8 for bit in bits)
        return CastResult(lines)  # type: ignore[arg-type]

    @staticmethod
    def _parse_switch_state(value: str) -> bool | None | str:
        normalized = (value or "").strip().lower()
        if normalized in {"", "状态", "status"}:
            return None
        if normalized in {"开", "开启", "启用", "on", "true", "1"}:
            return True
        if normalized in {"关", "关闭", "停用", "off", "false", "0"}:
            return False
        return "invalid"

    # ------------------------------------------------------------------
    # Per-group, per-divination-method state and QQ role checks
    # ------------------------------------------------------------------
    async def _group_gate(self, event: AstrMessageEvent, method: str) -> str:
        group_id = str(event.get_group_id() or "").strip()
        if not group_id:
            return "六爻起卦仅面向 QQ 群聊使用。"
        if not await self._is_method_enabled(group_id, method):
            label = METHOD_LABELS.get(method, method)
            return (
                f"本群“{label}”功能尚未开启，"
                f"请群主或管理员发送 /{label} 开。"
            )
        return ""

    async def _is_method_enabled(self, group_id: str, method: str) -> bool:
        switches = await self.get_kv_data(METHOD_SWITCHES_KEY, {})
        if isinstance(switches, dict):
            group_switches = switches.get(str(group_id))
            if isinstance(group_switches, dict) and method in group_switches:
                return bool(group_switches[method])

        # 兼容 0.1.x 的 {group_id: bool} 六爻开关。
        if method == METHOD_LIUYAO:
            legacy = await self.get_kv_data(LEGACY_SWITCHES_KEY, {})
            if isinstance(legacy, dict) and str(group_id) in legacy:
                return bool(legacy[str(group_id)])
        return self._default_enabled(method)

    async def _set_method_enabled(
        self,
        group_id: str,
        method: str,
        enabled: bool,
    ) -> None:
        async with self._switch_lock:
            switches = await self.get_kv_data(METHOD_SWITCHES_KEY, {})
            if not isinstance(switches, dict):
                switches = {}
            updated = dict(switches)
            current = updated.get(str(group_id))
            group_switches = dict(current) if isinstance(current, dict) else {}
            group_switches[method] = bool(enabled)
            updated[str(group_id)] = group_switches
            await self.put_kv_data(METHOD_SWITCHES_KEY, updated)

    async def _is_group_operator(self, event: AstrMessageEvent) -> bool:
        allowed_roles = {"owner", "admin"}
        raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
        sender = self._raw_get(raw, "sender")
        role = str(
            self._raw_get(sender, "role")
            or self._raw_get(raw, "role")
            or ""
        ).lower()
        if role in allowed_roles:
            return True
        if role:
            return False

        allow_lookup = self._config_get(
            "allow_operator_api_lookup",
            self._config_get("allow_owner_api_lookup", True),
        )
        if not bool(allow_lookup):
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
            return str(self._raw_get(data, "role") or "").lower() in allowed_roles
        except Exception as exc:
            logger.warning(f"liuyao：查询群主/管理员身份失败：{exc}")
            return False

    def _default_enabled(self, method: str) -> bool:
        method_defaults = self._config_get("method_defaults", {})
        if isinstance(method_defaults, dict) and method in method_defaults:
            return bool(method_defaults[method])
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




