"""Persistent, atomic admission of new casts, separate from repeat readings."""

import math
import time
from dataclasses import replace
from datetime import datetime

from astrbot.api import logger

if __package__:
    from .divination import CastResult, cast_instant
    from .paipan import BEIJING, build_chart
    from .question_policy import normalize_question, question_error
else:
    from divination import CastResult, cast_instant
    from paipan import BEIJING, build_chart
    from question_policy import normalize_question, question_error


class CastPolicyMixin:
    def _cast_limits(self):
        def read_int(key, default, minimum, maximum):
            value = self._config_get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                return default
            try:
                value = int(value)
            except ValueError:
                return default
            return value if minimum <= value <= maximum else default

        return (
            read_int("cast_cooldown_seconds", 3600, 1, 86400),
            read_int("cast_max_per_window", 3, 0, 100),
        )

    @staticmethod
    def _cast_limit_description(window_seconds, max_casts):
        if max_casts == 0:
            return "每位用户的六爻起卦频率限制已关闭"
        if window_seconds == 3600:
            window = "每小时"
        elif window_seconds % 60 == 0:
            window = f"滚动 {window_seconds // 60} 分钟内"
        else:
            window = f"滚动 {window_seconds} 秒内"
        return f"每位用户{window}最多起卦 {max_casts} 次"

    async def _prepare_cast(
        self,
        event,
        cast,
        *,
        question,
        intent,
        method,
        enforce_frequency=True,
        confirm_ai_usage=None,
        enforce_question=True,
        enforce_duplicate=True,
    ):
        gate = getattr(self, "_user_access_error", None)
        if callable(gate) and (error := await gate(event)):
            return None, error
        if enforce_question:
            error = question_error(question)
            if error:
                return None, error
        sender = str(event.get_sender_id() or "").strip()
        if not sender:
            return None, "无法识别起卦用户，本次未起卦。"
        async with self._cast_lock:
            key = "cast_policy:" + sender
            try:
                state = await self.get_kv_data(key, {})
                now = time.time()
                window_seconds, max_casts = self._cast_limits()
                stamps = sorted(
                    stamp for stamp in state.get("timestamps", []) if stamp > now - window_seconds
                )
                previous = state.get("last", {})
                if enforce_duplicate and normalize_question(question) == normalize_question(
                    previous.get("question", "")
                ):
                    return None, (
                        "同一事情禁止连续起六爻卦。请改用 reuse_liuyao 解读六爻原卦；"
                        "补充信息、换措辞或更换六爻起卦方式都不应重起。本限制仅适用于六爻。"
                    )
                if enforce_frequency and max_casts and len(stamps) >= max_casts:
                    # Lowering the quota may require multiple existing casts to expire.
                    wait_seconds = max(1, math.ceil(stamps[-max_casts] + window_seconds - now))
                    wait = (
                        f"{wait_seconds} 秒"
                        if wait_seconds < 60
                        else f"{math.ceil(wait_seconds / 60)} 分钟"
                    )
                    return (
                        None,
                        f"六爻：{self._cast_limit_description(window_seconds, max_casts)}，"
                        f"请约 {wait}后再试；"
                        "六爻原卦可继续解读。",
                    )
                # Generate only after admission; no await between generating and saving the record.
                cast = cast if cast is not None else cast_instant()
                cast = replace(cast, cast_at=datetime.fromtimestamp(now, BEIJING), chart=None)
                cast = replace(cast, chart=build_chart(cast))
                await self.put_kv_data(
                    key,
                    {
                        # Only AI tool casts enter the rolling frequency history.
                        "timestamps": (stamps + ([now] if enforce_frequency else []))[-100:],
                        "last": {
                            "question": question,
                            "intent": intent,
                            "method": method,
                            "lines": list(cast.lines),
                            "timestamp": now,
                            "chart": cast.chart,
                            "group_id": str(event.get_group_id() or ""),
                        },
                    },
                )
                if confirm_ai_usage is not None:
                    try:
                        await confirm_ai_usage()
                    except Exception:
                        # Restore both the original cast and hourly history
                        # under this lock, before another cast can interleave.
                        await self.put_kv_data(key, state)
                        raise
                record_user = getattr(self, "_record_cast_user", None)
                if callable(record_user):
                    await record_user(event, cast.cast_at)
            except Exception:
                logger.exception("liuyao：起卦准入记录处理失败")
                return None, "起卦记录读取或保存失败，本次未返回新卦，请稍后重试。"
        return cast, ""

    async def _reuse_cast(self, event, *, for_agent=False, question=""):
        error = await self._group_gate(event, "liuyao")
        if error:
            return error
        sender = str(event.get_sender_id() or "").strip()
        if not sender:
            return "无法识别用户，不能读取原卦。"
        try:
            async with self._cast_lock:
                state = await self.get_kv_data("cast_policy:" + sender, {})
            last = state.get("last", {})
            if not last:
                return (
                    "尚无可复用的六爻原卦。五行数字记录不能替代六爻原卦；"
                    "有具体事情可调用 cast_liuyao 起六爻卦。"
                )
            if last.get("group_id") != str(event.get_group_id() or ""):
                return "最近一卦在其他群，请回原群解读。"
            try:
                cast_at = datetime.fromtimestamp(last["timestamp"], BEIJING)
            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                cast_at = None
            cast = CastResult(tuple(last["lines"]), cast_at=cast_at, chart=last.get("chart"))
            if for_agent:
                return await self._content_reply(event, question, original=last, original_cast=cast)
            reading = self.readings.render(
                cast,
                intent=last["intent"],
                question=last["question"],
                method=last["method"],
                for_agent=for_agent,
            )
            return (
                "复用原卦：仅复用六爻原卦，不重新起六爻卦、不占起卦频率次数。"
                + "本地查看不占 AI 日额度。\n"
                + f"原卦时间：{cast_at.isoformat(timespec='seconds') if cast_at else '未知'}\n"
                + reading
            )
        except Exception:
            return "原卦读取失败，请稍后重试；不要因此重新起卦。"
