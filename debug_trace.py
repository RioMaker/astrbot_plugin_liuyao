"""Per-request administrator diagnostics; no credentials or response internals."""

from __future__ import annotations

import asyncio
import json
from time import perf_counter
from uuid import uuid4

from astrbot.api import logger

if __package__:
    from .token_usage import summarize
else:
    from token_usage import summarize


class DebugTrace:
    def __init__(self, event):
        self.event = event
        self.id = uuid4().hex[:8]
        self.started = perf_counter()
        self.sequence = 0
        self.calls = []
        self.steps = []
        self.images = []
        self.run_id = ""

    async def step(self, title, data):
        self.sequence += 1
        body = (
            data
            if isinstance(data, str)
            else json.dumps(data, ensure_ascii=False, indent=2, default=str)
        )
        heading = f"【六爻调试 {self.id} · {self.sequence:02d} · {title}】"
        self.steps.append((title, f"{heading}\n{body}"))

    def _forward_sections(self):
        groups = {}
        for title, text in self.steps:
            if title == "调试汇总":
                continue
            if title.startswith("预审"):
                group = "预审"
            elif title in {"随机起卦请求", "排盘结果"}:
                group = "排盘"
            elif title in {"解卦请求", "解卦响应", "解卦调用异常", "解卦处理失败"}:
                group = "解卦"
            elif title in {"权限与群开关", "问题与执行设置"}:
                group = "输入与设置"
            else:
                group = "结果与状态"
            groups.setdefault(group, []).append(text)
        return [("汇总", self.steps[-1][1])] + [
            (group, "\n\n".join(items)) for group, items in groups.items()
        ]

    async def _send_forward(self, fallback):
        try:
            from astrbot.api import message_components as Comp

            get_self_id = getattr(self.event, "get_self_id", None)
            bot_id = str(
                (get_self_id() if callable(get_self_id) else "")
                or getattr(getattr(self.event, "message_obj", None), "self_id", "")
                or self.event.get_sender_id()
            )
            nodes = []
            for group, text in self._forward_sections():
                chunks = [text[i : i + 3000] for i in range(0, len(text), 3000)]
                for index, chunk in enumerate(chunks, 1):
                    suffix = f" {index}/{len(chunks)}" if len(chunks) > 1 else ""
                    nodes.append(
                        Comp.Node(
                            uin=bot_id,
                            name=f"六爻调试 · {group}{suffix}",
                            content=[Comp.Plain(chunk)],
                        )
                    )
            if self.images:
                nodes.append(
                    Comp.Node(
                        uin=bot_id,
                        name="六爻调试 · 卦图",
                        content=[
                            Comp.Image.fromFileSystem(str(path.absolute())) for path in self.images
                        ],
                    )
                )
            # One Nodes component is essential: separate Node components send separate forwards.
            await asyncio.wait_for(
                self.event.send(self.event.chain_result([Comp.Nodes(nodes)])), timeout=30
            )
        except Exception as exc:
            logger.warning("liuyao：调试合并转发失败：%s", type(exc).__name__)
            await asyncio.wait_for(
                self.event.send(
                    self.event.plain_result(
                        f"六爻调试 {self.id}：合并转发发送失败（{type(exc).__name__}）。\n"
                        f"{fallback}"
                    )
                ),
                timeout=15,
            )
        finally:
            for path in self.images:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("liuyao：清理调试卦图失败：%s", type(exc).__name__)
            self.images.clear()

    async def error(self, title, exc):
        # Provider exceptions may include credentials or whole HTTP requests.
        safe_messages = {
            "当前会话没有可用模型",
            "预审结果格式无效",
            "预审结果不是 JSON 对象",
            "预审结果缺少有效的 reject 字段",
            "预审模型未返回可用结果",
            "模型未返回可用解读",
        }
        await self.step(
            title,
            {
                "error_type": type(exc).__name__,
                "原因": str(exc) if str(exc) in safe_messages else "此阶段未能完成",
                "说明": "请结合前一步调用参数、模型响应及 AstrBot 日志定位。",
            },
        )

    async def finish(self, outcome, *, storage_warning=False):
        totals = summarize([{"calls": self.calls, "kind": "debug"}])
        output = next(
            (
                json.loads(body.partition("\n")[2])
                for title, body in reversed(self.steps)
                if title == "输出与发图"
            ),
            {},
        )
        await self.step(
            "调试汇总",
            {
                "状态": outcome,
                "结果": output.get("待发送正文", "未生成解卦正文，请查看阶段状态"),
                "run_id": self.run_id,
                "总耗时秒": round(perf_counter() - self.started, 3),
                "模型调用次数": totals["calls"],
                "已知输入 Token": totals["input_tokens"],
                "已知输出 Token": totals["output_tokens"],
                "已知缓存输入 Token": totals["cached_tokens"],
                "已知总 Token": totals["total_tokens"],
                "总量未知调用次数": totals["unknown_calls"],
                "逐次调用": [
                    {
                        key: call.get(key)
                        for key in ("stage", "provider", "status", "elapsed_ms", "usage")
                    }
                    for call in self.calls
                ],
                "说明": "仅累计供应商返回的用量，未知不代表 0；缓存已包含在输入中。",
                "每日额度": "调试不扣每日额度或赠额",
                "数据保存": "保留调试 Token 账本，不覆盖最近一卦或保存普通卦例",
                "Token 保存异常": storage_warning,
            },
        )
        await self._send_forward(
            f"{outcome}\n模型调用 {totals['calls']} 次，已知 Token {totals['total_tokens']}，"
            f"用量未知 {totals['unknown_calls']} 次。\n"
            f"记录：{self.run_id or '未开始调用'}\n"
            f"{str(output.get('待发送正文', ''))[:500]}"
        )


class DebugMixin:
    async def _run_debug(self, event, question):
        if not self._is_astrbot_admin(event):
            return "无权限：/六爻test 仅限 AstrBot 管理员使用。"
        if error := await self._user_access_error(event):
            return error
        if not question.strip():
            return "用法：/六爻test 问题\n结束后以一条合并转发返回结果、调用详情和 Token 用量。"
        trace = DebugTrace(event)
        outcome = "流程完成，请查看各阶段结果"
        try:
            result = await self._content_reply(event, question, trace=trace)
            if result:
                outcome = "流程未完成"
                await trace.step("流程停止", result)
        except asyncio.CancelledError:
            outcome = "已取消"
            raise
        except Exception as exc:
            outcome = "流程异常"
            logger.warning("liuyao：调试流程异常：%s", type(exc).__name__)
            await trace.error("流程异常", exc)
        finally:
            await trace.finish(
                outcome, storage_warning=bool(getattr(self, "_token_storage_warning", False))
            )
        return ""

    async def _call_analysis_model(self, event, run, stage, provider_id, prompt, *, trace=None):
        timeout = self._bounded_config_int("command_analysis_timeout_seconds", 60, 5, 180)
        label = "预审" if stage in {"pre_review", "reuse_review"} else "解卦"
        if trace is not None:
            key = (
                "command_pre_review_provider_id"
                if label == "预审"
                else "command_interpretation_provider_id"
            )
            _configured, source = self._analysis_provider_config(event, key)
            model = "未提供模型元数据"
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    provider = getter(provider_id)
                    if provider is not None:
                        model = str(provider.get_model())
                except Exception:
                    pass
            await trace.step(
                f"{label}请求",
                {
                    "调用": "Context.llm_generate",
                    "stage": stage,
                    "模型来源": source,
                    "模型名称": model,
                    "timeout_seconds": timeout,
                    "request": {"chat_provider_id": provider_id, "prompt": prompt},
                    "其他参数": "未附加主聊天上下文、人格或工具；生成参数沿用所选提供商配置。",
                },
            )
        before = len(trace.calls) if trace is not None else 0
        try:
            response = await asyncio.wait_for(
                self._token_llm(
                    run, stage, trace=trace, chat_provider_id=provider_id, prompt=prompt
                ),
                timeout=timeout,
            )
        except BaseException as exc:
            if trace is not None:
                await trace.step(
                    f"{label}调用异常",
                    {
                        "error_type": type(exc).__name__,
                        "调用记录": trace.calls[before:],
                        "说明": "无用量字段表示尚未取得供应商报告，不代表零消耗。",
                    },
                )
            raise
        if trace is not None:
            await trace.step(
                f"{label}响应",
                {
                    "role": str(getattr(response, "role", "") or ""),
                    "completion_text": str(getattr(response, "completion_text", "") or ""),
                    "调用记录": trace.calls[before:],
                    "用量字段": (
                        "input_tokens=输入；output_tokens=输出；cached_tokens=输入中的缓存；"
                        "total_tokens=总量；null=未知"
                    ),
                },
            )
        return response
