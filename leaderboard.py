"""Cumulative group rankings from the plugin's cast, model and feedback records."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from astrbot.api import logger

if __package__:
    from .case_library import analysis_history, normalize_case_store
    from .token_usage import case_usage_key
    from .user_management import CASES_KEY
else:
    from case_library import analysis_history, normalize_case_store
    from token_usage import case_usage_key
    from user_management import CASES_KEY


PAGE_SIZE = 5
HISTORY_KEY = "liuyao_leaderboard_history_v1"


def _reading_statuses(run: dict) -> set:
    return {
        call.get("status")
        for call in run.get("calls") or ()
        if isinstance(call, dict)
        and call.get("stage") in {"interpretation", "reuse_interpretation"}
        and call.get("status") != "not_called"
    }


def _member(members: dict, sender: str, record: dict) -> dict:
    row = members.setdefault(sender, {"name": "", "at": "", "casts": 0, "ai": 0})
    name = " ".join(str(record.get("caster_name") or "").replace("\x00", "").split())
    at = str(record.get("recorded_at") or record.get("created_at") or "")
    if name and (not row["name"] or at >= row["at"]):
        row.update(name=name[:80], at=at)
    return row


def _score(scores: dict, sender: str) -> int:
    if not isinstance(scores, dict):
        return 0
    try:
        return max(0, int(scores.get(sender, 0)))
    except (TypeError, ValueError):
        return 0


def historical_usage(payload, cases, runs) -> dict:
    """Keep only evidence that would otherwise disappear when old cases are pruned."""
    casts = set()
    reading_ids = set()
    returned_by_case = defaultdict(set)
    for run in runs:
        if run.get("kind") not in {"cast", "reuse"}:
            continue
        keys = {key for key in (run.get("case_id"), run.get("cast_key")) if key}
        if run["kind"] == "cast":
            casts.update(keys)
        if statuses := _reading_statuses(run):
            reading_ids.add(run["id"])
            if "returned" in statuses:
                for key in keys:
                    returned_by_case[key].add(run["id"])

    groups = {}
    valid_feedback = Counter()
    for case in cases:
        sender = str(case.get("caster_id") or "").strip()
        group = str(case.get("group_id") or "").strip()
        for item in case.get("feedback") or ():
            if isinstance(item, dict) and item.get("valid") is True:
                valid_feedback[str(item.get("reporter_id") or sender)] += 1
                break  # Each case can earn one accepted feedback.
        if not group or not sender:
            continue
        row = _member(groups.setdefault(group, {}), sender, case)
        case_id, cast_key = case["id"], case_usage_key(case)
        row["casts"] += int(casts.isdisjoint((case_id, cast_key)))
        identified = set()
        anonymous = 0
        for entry in analysis_history(case):
            success = entry.get("status") == "success" and entry.get("analysis")
            if not success and not _reading_statuses(entry):
                continue
            if entry.get("id") and not entry.get("legacy"):
                identified.add(entry["id"])
            else:
                anonymous += 1
        # A legacy original has no run ID. Only a returned reading of this
        # case can already account for it. A failed rereading is a separate use.
        returned = returned_by_case.get(case_id, set()) | returned_by_case.get(cast_key, set())
        unmatched_originals = max(0, anonymous - len(returned - identified))
        row["ai"] += len(identified - reading_ids) + unmatched_originals

    saved = payload if isinstance(payload, dict) else {}
    scores = saved.get("user_scores", {})
    events = saved.get("valid_feedback_events")
    for sender, dates in (events if isinstance(events, dict) else {}).items():
        if isinstance(dates, list):
            valid_feedback[sender] = max(valid_feedback[sender], len(dates))
    offsets = {
        sender: total - _score(scores, sender)
        for sender, total in valid_feedback.items()
        if total > _score(scores, sender)
    }
    return {"groups": groups, "feedback_offsets": offsets}


@dataclass(frozen=True)
class RankEntry:
    rank: int
    name: str
    count: int


@dataclass(frozen=True)
class RankSection:
    title: str
    scope: str
    total: int
    peak: int
    entries: list[RankEntry]


@dataclass(frozen=True)
class GroupLeaderboard:
    group: str
    participants: int
    page: int
    pages: int
    sections: list[RankSection]

    def as_text(self) -> str:
        lines = [f"六爻排行榜 · 群 {self.group} · 第 {self.page}/{self.pages} 页"]
        for section in self.sections:
            lines.extend(("", f"{section.title}（{section.scope}）"))
            lines.extend(f"{e.rank}. {e.name}　{e.count} 次" for e in section.entries)
            if not section.entries:
                lines.append("本页没有更多记录" if section.total else "暂无记录")
        if self.page < self.pages:
            lines.append(f"\n下一页 /六爻排行 {self.page + 1}")
        return "\n".join(lines)


class LeaderboardPageError(ValueError):
    """The requested page is outside this group's rankings."""


def group_leaderboard(group, history, users, case_payload, runs, page=1) -> GroupLeaderboard:
    members = {s: dict(row) for s, row in history["groups"].get(group, {}).items()}
    for sender, user in users.items():
        if group in user.get("groups", []):
            row = _member(members, sender, {})
            if not row["name"] and user["groups"] == [group]:
                row["name"] = user.get("name", "")
    for run in runs:
        if str(run.get("group_id")) != group or run.get("kind") not in {"cast", "reuse"}:
            continue
        sender = str(run.get("caster_id") or "").strip()
        if not sender:
            continue
        row = _member(members, sender, run)
        # Prior versions recorded the new cast, but no image delivery receipt.
        archived_cast = bool(run.get("cast_key") or run.get("case_id"))
        if run["kind"] == "cast" and run.get("chart_sent", archived_cast):
            row["casts"] += 1
        row["ai"] += bool(_reading_statuses(run))

    scores = case_payload.get("user_scores", {}) if isinstance(case_payload, dict) else {}
    names = Counter(row["name"] for row in members.values())
    for sender, row in members.items():
        row["feedback"] = _score(scores, sender) + history["feedback_offsets"].get(sender, 0)
        if not row["name"] or row["name"] == "群友" or names[row["name"]] > 1:
            row["name"] = f"{row['name'] or '群友'}·{sender[-4:]}"

    sections = []
    pages = 1
    start = (page - 1) * PAGE_SIZE
    for key, title, scope in (
        ("casts", "起卦排行", "本群新卦图"),
        ("ai", "AI 解卦排行", "本群解卦请求"),
        ("feedback", "反馈排行", "本群群友 · 跨群累计通过"),
    ):
        ordered = sorted(
            (sender for sender, row in members.items() if row[key] > 0),
            key=lambda sender: (-members[sender][key], sender),
        )
        pages = max(pages, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
        entries = []
        rank, previous = 0, None
        for i, sender in enumerate(ordered[: start + PAGE_SIZE]):
            count = members[sender][key]
            if count != previous:
                rank, previous = i + 1, count
            if i >= start:
                entries.append(RankEntry(rank, members[sender]["name"], count))
        total = sum(members[s][key] for s in ordered)
        peak = members[ordered[0]][key] if ordered else 0
        sections.append(RankSection(title, scope, total, peak, entries))
    if not 1 <= page <= pages:
        raise LeaderboardPageError(f"排行榜共 {pages} 页，请输入 1 至 {pages} 之间的页码。")
    return GroupLeaderboard(group, len(members), page, pages, sections)


class LeaderboardMixin:
    async def _leaderboard_history(self):
        # Snapshot once, before old cases can be trimmed. Never add these
        # historical counts to the user's quota, score or token records.
        async with self._case_lock_instance():
            history = await self.get_kv_data(HISTORY_KEY, None)
            if history is None:
                payload = await self.get_kv_data(CASES_KEY, {})
                cases = normalize_case_store(payload)
                runs = await self._token_runs() if cases else []
                history = historical_usage(payload, cases, runs)
                await self.put_kv_data(HISTORY_KEY, history)
            return history

    async def _leaderboard_reply(self, event, argument: str) -> str:
        if error := await self._group_gate(event, "liuyao"):
            return error
        if argument and not (argument.isascii() and argument.isdecimal() and len(argument) <= 9):
            return "用法：/六爻排行 或 /六爻排行 2。"
        try:
            history = await self._leaderboard_history()
            registry = await self._load_user_registry()
            payload = await self.get_kv_data(CASES_KEY, {})
            runs = await self._token_runs()
            board = group_leaderboard(
                str(event.get_group_id()).strip(),
                history,
                registry["users"],
                payload,
                runs,
                int(argument or "1"),
            )
        except LeaderboardPageError as exc:
            return str(exc)
        except Exception as exc:
            logger.warning("liuyao：读取排行榜失败：%r", exc)
            return "排行榜数据暂时无法读取，请稍后重试。"
        if await self._send_text_card_image(event, "render_leaderboard", board):
            return ""
        return board.as_text()
