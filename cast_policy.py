"""Persistent, atomic admission of new casts, separate from repeat readings."""

import math
import time
from datetime import datetime

from astrbot.api import logger

if __package__:
    from .divination import CastResult, cast_instant
    from .question_policy import normalize_question, question_error
else:
    from divination import CastResult, cast_instant
    from question_policy import normalize_question, question_error


class CastPolicyMixin:
    async def _prepare_cast(self, event, cast, *, question, intent, method):
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
                stamps = [stamp for stamp in state.get("timestamps", []) if stamp > now - 3600]
                previous = state.get("last", {})
                if normalize_question(question) == normalize_question(previous.get("question", "")):
                    return None, (
                        "同一事情禁止连续起六爻卦。请用 /六爻 解读 或 reuse_liuyao 解读六爻原卦；"
                        "补充信息、换措辞或更换六爻起卦方式都不应重起。本限制仅适用于六爻。"
                    )
                if len(stamps) >= 3:
                    wait = max(1, math.ceil((min(stamps) + 3600 - now) / 60))
                    return (
                        None,
                        f"六爻：每位用户每小时最多起卦 3 次，请约 {wait} 分钟后再试；"
                        "六爻原卦可继续解读。",
                    )
                # Generate only after admission; no await between generating and saving the record.
                cast = cast if cast is not None else cast_instant()
                await self.put_kv_data(
                    key,
                    {
                        "timestamps": stamps + [now],
                        "last": {
                            "question": question,
                            "intent": intent,
                            "method": method,
                            "lines": list(cast.lines),
                            "timestamp": now,
                            "group_id": str(event.get_group_id() or ""),
                        },
                    },
                )
            except Exception:
                logger.exception("liuyao：起卦准入记录处理失败")
                return None, "起卦记录读取或保存失败，本次未返回新卦，请稍后重试。"
        return cast, ""

    async def _reuse_cast(self, event, *, for_agent=False):
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
            reading = self.readings.render(
                CastResult(tuple(last["lines"])),
                intent=last["intent"],
                question=last["question"],
                method=last["method"],
                for_agent=for_agent,
            )
            cast_at = datetime.fromtimestamp(last["timestamp"]).astimezone()
            return (
                "复用原卦：仅复用六爻原卦，不重新起六爻卦、不占次数。可结合补充信息多次解读。\n"
                f"原卦时间：{cast_at.isoformat(timespec='seconds')}\n" + reading
            )
        except Exception:
            return "原卦读取失败，请稍后重试；不要因此重新起卦。"
