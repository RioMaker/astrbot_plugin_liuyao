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
        self.delivery_failures = 0
        self.run_id = ""

    async def step(self, title, data):
        self.sequence += 1
        body = (
            data
            if isinstance(data, str)
            else json.dumps(data, ensure_ascii=False, indent=2, default=str)
        )
        chunks = [body[i : i + 1500] for i in range(0, len(body), 1500)] or ["（空）"]
        for index, chunk in enumerate(chunks, 1):
            heading = f"【六爻调试 {self.id} · {self.sequence:02d} · {title}】"
            if len(chunks) > 1:
                heading += f"（{index}/{len(chunks)}）"
            try:
                await asyncio.wait_for(
                    self.event.send(self.event.plain_result(f"{heading}\n{chunk}")), timeout=15
                )
            except Exception as exc:
                self.delivery_failures += len(chunks) - index + 1
                logger.warning("liuyao：调试消息发送失败：%s", type(exc).__name__)
                break

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
        await self.step(
            "调试汇总",
            {
                "状态": outcome,
                "run_id": self.run_id,
                "总耗时秒": round(perf_counter() - self.started, 3),
                "模型调用次数": totals["calls"],
                "已知输入 Token": totals["input_tokens"],
                "已知输出 Token": totals["output_tokens"],
                "已知缓存输入 Token": totals["cached_tokens"],
                "已知总 Token": totals["total_tokens"],
                "总量未知调用次数": totals["unknown_calls"],
                "说明": "仅累计供应商返回的用量，未知不代表 0；缓存已包含在输入中。",
                "每日额度": "调试不扣每日额度或赠额",
                "数据保存": "保留调试 Token 账本，不覆盖最近一卦或保存普通卦例",
                "Token 保存异常": storage_warning,
                "调试消息发送失败段数": self.delivery_failures,
            },
        )


class DebugMixin:
    async def _run_debug(self, event, question):
        if not self._is_astrbot_admin(event):
            return "无权限：/六爻test 仅限 AstrBot 管理员使用。"
        if error := await self._user_access_error(event):
            return error
        if not question.strip():
            return "用法：/六爻test 问题\n逐步返回调用参数、提示词、模型正文和 Token 用量。"
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
