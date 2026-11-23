"""Run the normal interpretation pipeline with a dashboard-only report."""

from __future__ import annotations

import asyncio
import copy
import re
from dataclasses import replace
from functools import partial
from time import perf_counter

from astrbot.api import logger

if __package__:
    from .case_library import extract_verdict, normalize_case_store
    from .debug_trace import AnalysisTrace
    from .divination import ManualCastError
    from .feedback_card import case_cast
    from .manual_commands import parse_manual_request
    from .paipan import chart_for
    from .token_usage import summarize
    from .user_management import CASES_KEY, ManagementError, require_text
else:
    from case_library import extract_verdict, normalize_case_store
    from debug_trace import AnalysisTrace
    from divination import ManualCastError
    from feedback_card import case_cast
    from manual_commands import parse_manual_request
    from paipan import chart_for
    from token_usage import summarize
    from user_management import CASES_KEY, ManagementError, require_text


def _brief(text, length=110):
    value = " ".join(str(text or "").split())
    return value if len(value) <= length else value[: length - 1] + "…"


class ReadingTestTrace(AnalysisTrace):
    def __init__(self, journal, test_id, report, run):
        self.journal = journal
        self.test_id = test_id
        self.report = report
        self.run = run
        self.started = perf_counter()
        self.calls = []

    def save(self, status="running"):
        self.report["usage"] = summarize([self.run])
        self.report["elapsed_ms"] = round((perf_counter() - self.started) * 1000)
        try:
            self.journal.save_test(self.test_id, status, self.report)
        except ManagementError:
            if status != "running":
                raise
            # Keep the snapshot in memory for the next write. A temporary report-write
            # failure after a model returns must not trigger another paid model call.
            logger.warning("liuyao：测试 #%s 的阶段记录暂未写入，后续保存时重试", self.test_id)

    async def stage(self, title, input_summary, inputs, output_summary, outputs):
        self.report["steps"].append(
            {
                "title": title,
                "input_summary": _brief(input_summary),
                "inputs": inputs,
                "output_summary": _brief(output_summary),
                "outputs": outputs,
            }
        )
        self.save()

    async def begin_model(self, label, data):
        request = data["request"]
        await self.stage(
            f"{label}模型调用",
            f"{request['chat_provider_id']} · {data['模型来源']} · "
            f"提示词 {len(request['prompt'])} 字",
            data,
            "等待模型返回",
            None,
        )

    async def end_model(self, label, data, *, failed=False):
        step = self.report["steps"][-1]
        calls = data["调用记录"]
        usage = (calls[-1].get("usage") or {}) if calls else {}
        tokens = usage.get("total_tokens")
        response = data["error_type"] if failed else data["completion_text"] or "空回复"
        step.update(
            outputs=data,
            output_summary=(
                f"{tokens if tokens is not None else '未知'} Token · {_brief(response, 80)}"
            ),
        )
        self.save()


class ReadingTestMixin:
    async def _page_reading_tests(self, *, query):
        return self._journal().list_tests(self._page_options(query)[1])

    async def _page_reading_test(self, test_id, *, query):
        return self._journal().test_detail(test_id)

    async def _admin_start_reading_test(self, *, payload, actor):
        key = require_text(payload.get("request_id"), "操作编号", 64)
        if not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", key):
            raise ManagementError("操作编号格式无效")
        journal = self._journal()
        if existing := journal.existing_test(actor, key):
            return {"id": existing}
        source = payload.get("source")
        question = require_text(payload.get("question", ""), "测试问题", 1000, empty=True)
        inputs = {"来源": source, "填写的问题": question}
        serial = record = cast = None
        if source == "case":
            number = str(payload.get("serial", ""))
            if not re.fullmatch(r"[0-9]{1,12}", number):
                raise ManagementError("请选择一个已有卦例")
            serial = int(number)
            cases = normalize_case_store(await self.get_kv_data(CASES_KEY, {}))
            record = next((c for c in cases if c["serial"] == serial), None)
            if record is None:
                raise ManagementError("卦例不存在或已删除，请重新选择", 404)
            try:
                cast = case_cast(record)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise ManagementError("所选卦例的排盘数据不完整", 422) from exc
            inputs.update(
                卦例编号=serial,
                保存的六爻=cast.lines,
                原起卦时间=str(cast.cast_at or ""),
                保存的排盘=cast.chart,
            )
            question = question or str(
                record.get("analysis_question") or record.get("question") or ""
            )
        elif source == "manual":
            lines = require_text(payload.get("lines"), "六爻", 128)
            at = require_text(payload.get("cast_at", ""), "北京时间", 32, empty=True)
            try:
                cast, extra, _ = parse_manual_request(lines + (f" 时间={at}" if at else ""))
                if extra:
                    raise ManualCastError("六爻和起卦时间请分别填写")
            except ManualCastError as exc:
                raise ManagementError(str(exc)) from exc
            inputs.update(手动六爻=lines, 指定起卦时间=at)
        elif source != "random":
            raise ManagementError("请选择卦例、手动排盘或模拟起卦")

        # The numbered command also uses the saved question when the override is empty.
        question, intent, no_question = self._reading_question(question, case_record=record)
        run = self._new_token_run(None, kind="reading_test", question=question)
        run["cast_key"] = run["id"]
        report = {"run_id": run["id"], "steps": [], "request": inputs}
        if not self._admin_page_active:
            raise ManagementError("六爻插件已卸载，请重载后重试", 503)
        test_id, created = journal.start_test(actor, key, source, serial, question, report)
        if created:
            trace = ReadingTestTrace(journal, test_id, report, run)
            task = asyncio.create_task(
                self._run_reading_test(
                    trace,
                    source,
                    cast,
                    question,
                    intent,
                    no_question,
                    str(record.get("method") or "历史排盘") if record else "",
                )
            )
            self._reading_test_tasks.add(task)
            task.add_done_callback(partial(self._reading_test_done, test_id))
        return {"id": test_id}

    def _reading_test_done(self, test_id, task):
        self._reading_test_tasks.discard(task)
        if task.cancelled():
            status = "interrupted"  # Also covers cancellation before the coroutine starts.
        elif error := task.exception():
            logger.error("liuyao：测试 #%s 结束时保存失败：%r", test_id, error)
            status = "failed"
        else:
            return
        try:
            self._journal().stop_unfinished_test(test_id, status)
        except ManagementError:
            logger.exception("liuyao：无法更新测试 #%s 状态", test_id)

    async def _run_reading_test(self, trace, source, cast, question, intent, no_question, method):
        status = "interrupted"
        try:
            inputs = trace.report["request"]
            if source == "case":
                # A missing legacy timestamp must stay unknown. Never substitute today's chart.
                cast = replace(cast, chart=copy.deepcopy(chart_for(cast, cast.cast_at)))
                origin = f"卦例 #{inputs['卦例编号']} · 原盘"
            else:
                cast = self._new_cast_snapshot(cast, self._ai_daily_now().timestamp())
                if source == "manual":
                    method, origin = "手动摇卦排盘", inputs["手动六爻"]
                else:
                    method = "普通排盘" if no_question else "AI 预审分类起卦"
                    origin = "随机摇六爻"
            primary = self.corpus.get(cast.primary_number)["name"]
            changed = self.corpus.get(cast.changed_number)["name"]
            chart_label = f"{primary} → {changed}" if cast.moving_lines else f"{primary} · 静卦"
            await trace.stage(
                "取得卦盘",
                origin,
                inputs,
                chart_label + " · " + " ".join(map(str, cast.lines)),
                {
                    "六爻（自下而上）": cast.lines,
                    "动爻位置": cast.moving_lines,
                    "本卦": primary,
                    "之卦": changed,
                    "起卦时间": cast.cast_at,
                    "排盘快照": cast.chart,
                },
            )
            await trace.stage(
                "问题与设置",
                inputs["填写的问题"] or question,
                {
                    "填写的问题": inputs["填写的问题"],
                    "采用卦例原问": source == "case" and not inputs["填写的问题"],
                },
                question,
                {
                    "实际问句": question,
                    "初始分类": intent,
                    "仅排盘": no_question,
                    "预审配置": self._analysis_provider_config(
                        None, "command_pre_review_provider_id"
                    ),
                    "解卦配置": self._analysis_provider_config(
                        None, "command_interpretation_provider_id"
                    ),
                    "备用模型": self._config_get("command_interpretation_backup_provider_id", ""),
                    "解卦字数上限": self._reply_budget(),
                },
            )
            if cast.cast_at is not None:
                await self._bind_token_cast(trace.run, cast)
            outcome = await self._interpret_cast(
                None,
                trace.run,
                cast,
                question=question,
                intent=intent,
                method=method,
                no_question=no_question,
                reuse=source == "case",
                manual=source == "manual",
                trace=trace,
            )
            text = self._format_reading_reply(outcome.notice or outcome.analysis)
            verdict = extract_verdict(outcome.analysis) if outcome.status == "success" else ""
            trace.report.update(text=text, verdict=verdict)
            await trace.stage(
                "整理输出",
                f"解卦字数上限 {self._reply_budget()}",
                {
                    "解读正文": outcome.analysis,
                    "提示": outcome.notice,
                    "字数上限": self._reply_budget(),
                    "按配置裁剪": bool(self._config_get("command_reply_trim", True)),
                },
                verdict or text,
                {"最终正文": text, "断语": verdict},
            )
            status = outcome.status
        except Exception as exc:
            status = "failed"
            logger.exception("liuyao：后台解卦测试失败")
            await trace.error("测试中断", exc)
        finally:
            trace.save(status)
