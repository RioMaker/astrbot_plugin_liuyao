"""The interpretation pipeline shared by chat and dashboard tests."""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import logger

if __package__:
    from . import prompts
    from .ai_daily_quota import AIQuotaStorageError
    from .divination import infer_intent, parse_intent_and_question
    from .question_policy import NO_VALID_QUESTION_NOTICE, is_obvious_non_question
    from .reply_policy import limit_reply
else:
    import prompts
    from ai_daily_quota import AIQuotaStorageError
    from divination import infer_intent, parse_intent_and_question
    from question_policy import NO_VALID_QUESTION_NOTICE, is_obvious_non_question
    from reply_policy import limit_reply


@dataclass
class ReadingOutcome:
    intent: str
    method: str
    status: str = "skipped"
    reviewed: bool = False
    classification_source: str = ""
    analysis: str = ""
    notice: str = ""


class InterpretationMixin:
    def _reading_question(self, content, original=None, case_record=None):
        raw = self._limited_question(content)
        intent, question = parse_intent_and_question(
            raw, lambda value: self.readings.normalize_intent(value, "")
        )
        question = question or raw or "（未提供具体内容）"
        if case_record is not None:
            question = raw or str(
                case_record.get("analysis_question") or case_record.get("question") or ""
            )
        elif original is not None:
            question = str(original["question"])
            no_question = is_obvious_non_question(question) and is_obvious_non_question(raw)
            if raw:
                question += f"\n本轮追问或补充：{raw}"
            return question, self.readings.normalize_intent(original["intent"]), no_question
        return question, intent, is_obvious_non_question(question)

    def _format_reading_reply(self, text):
        return limit_reply(
            text, self._reply_budget(), trim=bool(self._config_get("command_reply_trim", True))
        )

    async def _review_reading(self, event, run, question, *, intent, reuse, trace):
        provider = await self._analysis_provider_id(event, "command_pre_review_provider_id")
        classification = self._prompt_template(
            "command_classification_rules", prompts.CLASSIFICATION_RULES
        )
        prompt = self._configured_prompt(
            "command_pre_review_prompt",
            prompts.PRE_REVIEW_PROMPT,
            guardrail=(
                f"{classification}\n候选分类及适用范围：\n{self.readings.classification_catalog()}\n"
                f"初始分类供参考：{self.readings.directions[intent]['label']}\n"
                f"{prompts.PRE_REVIEW_PROTOCOL}"
            ),
            question=question,
        )
        response = await self._call_analysis_model(
            event, run, "reuse_review" if reuse else "pre_review", provider, prompt, trace=trace
        )
        raw = str(getattr(response, "completion_text", "") or "")
        if getattr(response, "role", "") == "err":
            raise ValueError("预审模型未返回可用结果")
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("预审结果格式无效")
        review = json.loads(raw[start : end + 1])
        if not isinstance(review, dict):
            raise ValueError("预审结果不是 JSON 对象")
        for field in ("reject", "valid_question"):
            value = review.get(field)
            if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
                review[field] = value.strip().lower() == "true"
            elif not isinstance(value, bool):
                raise ValueError(f"预审结果缺少有效的 {field} 字段")
        return raw, review

    async def _interpret_cast(
        self,
        event,
        run,
        cast,
        *,
        question,
        intent,
        method,
        no_question,
        assigned_intent="",
        reuse=False,
        manual=False,
        chart_only=False,
        lease=None,
        trace=None,
    ):
        result = ReadingOutcome(assigned_intent or intent, method)
        if assigned_intent:
            result.classification_source = "人工分类"
        if chart_only:
            result.notice = "排盘已保存；未调用 AI，不参与反馈。可用 /六爻解卦 #编号 问题 补解。"
        elif no_question:
            result.notice = NO_VALID_QUESTION_NOTICE
        elif lease and lease.error:
            result.notice = lease.error
        if result.notice:
            return result
        try:
            raw, review = await self._review_reading(
                event, run, question, intent=result.intent, reuse=reuse, trace=trace
            )
            predicted = self.readings.normalize_intent(str(review.get("intent") or ""), "")
            if not assigned_intent:
                result.intent = predicted or intent
                result.classification_source = "AI分类" if predicted else "本地分类"
                if predicted == "shefu" and (
                    intent == "lost_item" or infer_intent(question) == "lost_item"
                ):
                    result.intent = "lost_item"
                    result.classification_source = "寻物规则校正"
            result.reviewed = True
            if trace is not None:
                proceed = not review["reject"] and review["valid_question"]
                direction = self.readings.directions[result.intent]["label"]
                decision = "继续解卦" if proceed else "仅排盘"
                await trace.stage(
                    "预审解析与分类",
                    "预审模型的原始回复",
                    {"原文": raw},
                    f"{direction} · {decision}",
                    {
                        "解析结果": review,
                        "人工指定分类": assigned_intent,
                        "分类来源": result.classification_source,
                        "最终分类": result.intent,
                        "继续解卦": proceed,
                        "本地解析 Token": 0,
                    },
                )
        except Exception as exc:
            if trace is not None:
                await trace.error("预审处理失败", exc)
            logger.warning("liuyao：内容预审失败：%r", exc)
            if lease is not None:
                try:
                    await lease.release()
                except AIQuotaStorageError:
                    logger.warning("liuyao：释放未使用的 AI 日额度预约失败")
            result.status = "failed"
            result.notice = (
                "AI 预审暂不可用，本次仍已起卦；为避免误解读，暂不提供解卦，不消耗 AI 解卦次数。"
            )
            return result
        if review["reject"]:
            reason = self._clean_short_text(
                str(review.get("reason") or "可能造成伤害或违法风险"), 100
            )
            result.notice = (
                f"已为你起卦，但因该内容涉及{reason}，我不能提供相关解读或行动建议。"
                "请遵守法律并保护自身与他人安全；如涉及现实紧急情况，请联系当地专业机构。"
                "本次不消耗 AI 解卦次数。"
            )
            return result
        if not review["valid_question"]:
            result.notice = NO_VALID_QUESTION_NOTICE
            if not manual and not reuse:
                result.method = "普通排盘"
            return result

        chart_text = self.readings.render(
            cast, intent=result.intent, question=question, method=method, for_agent=True
        )
        try:
            prompt = self._configured_prompt(
                "command_interpretation_prompt",
                prompts.INTERPRETATION_PROMPT,
                guardrail=prompts.INTERPRETATION_GUARDRAIL,
                question=question,
                chart=chart_text,
                max_chars=self._max_chars_instruction(self._reply_budget()),
            )
            if trace is not None:
                direction = self.readings.directions[result.intent]["label"]
                await trace.stage(
                    "解卦依据",
                    f"{direction} · {question}",
                    {
                        "问题": question,
                        "分类": result.intent,
                        "起卦方式": method,
                        "六爻": cast.lines,
                        "起卦时间": cast.cast_at,
                        "排盘快照": cast.chart,
                    },
                    f"排盘、卦爻辞及{direction}参考材料 · {len(chart_text)} 字",
                    {"排盘与参考材料": chart_text},
                )
            result.analysis = await self._call_interpretation_model(
                event,
                run,
                "reuse_interpretation" if reuse else "interpretation",
                prompt,
                trace=trace,
            )
            result.status = "success"
        except Exception as exc:
            if trace is not None:
                await trace.error("解卦处理失败", exc)
            logger.warning("liuyao：卦象解读失败：%r", exc)
            result.status = "failed"
            result.analysis = (
                "AI 解读暂时失败；排盘结果见调试报告。"
                if trace is not None
                else "AI 解读暂时失败，请稍后再试；本次卦图已保留，不消耗 AI 解卦次数。"
            )
        return result
