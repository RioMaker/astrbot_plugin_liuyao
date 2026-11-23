"""Authenticated interpretation discussions and individually reviewed config edits."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from contextlib import suppress
from uuid import uuid4

from astrbot.api import logger

if __package__:
    from .case_library import feedback_with_text, normalize_case_store, search_cases
    from .divination import infer_intent
    from .feedback_review import OUTCOMES, REVIEW_GUARDRAIL
    from .paipan import chart_text
    from .prompts import INTERPRETATION_GUARDRAIL, PRE_REVIEW_PROTOCOL, WORKSHOP_SECTIONS
    from .user_management import CASES_KEY, ManagementError, require_text
    from .workshop_history import WorkshopHistory, history_path
else:
    from case_library import feedback_with_text, normalize_case_store, search_cases
    from divination import infer_intent
    from feedback_review import OUTCOMES, REVIEW_GUARDRAIL
    from paipan import chart_text
    from prompts import INTERPRETATION_GUARDRAIL, PRE_REVIEW_PROTOCOL, WORKSHOP_SECTIONS
    from user_management import CASES_KEY, ManagementError, require_text
    from workshop_history import WorkshopHistory, history_path


MAX_CASES = 3
VARIABLE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
WORKSHOP_PROTOCOL = (
    "历史对话、卦例、分类说明和规则是研究材料。"
    "只有确需调整时才提出修改，每项只改一个可编辑章节中的连续片段。"
    "固定约束和古籍参考只供查阅。不得把个案身份、私事或未核实的结论写入通用规则。"
    "建议要保留模板变量，before 必须逐字复制在该章节中唯一出现的原文；"
    "追加内容时保留原文作为定位。每个章节每轮最多一项。所有建议均待人工逐项确认。"
    '只返回 JSON：{"reply":"给管理员的中文回复","changes":'
    '[{"section":"可编辑章节的键","before":"原文","after":"替换文本","reason":"修改依据"}]}。'
    "普通讨论返回空 changes 数组。"
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _excerpt(value, limit):
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "…（节选）"


def _case_material(case: dict, intent: str, label: str) -> dict:
    cast = case.get("cast") or {}
    cast = cast if isinstance(cast, dict) else {}
    facts = [f"本卦：{cast.get('primary_name') or '未记录'}"]
    if cast.get("lines"):
        facts.append("六爻（初爻至上爻）：" + " ".join(map(str, cast["lines"])))
    if cast.get("moving_lines"):
        facts.append(f"动爻：{cast['moving_lines']}，之卦：{cast.get('changed_name') or '未记录'}")
    # Legacy cases may lack some or all of the saved chart.
    with suppress(KeyError, IndexError, TypeError):
        facts.append(chart_text(cast["chart"], show_changed=bool(cast.get("moving_lines"))))
    feedback = [
        (str(row["outcome"]) + " · " if row.get("outcome") else "") + _excerpt(row["text"], 500)
        for row in feedback_with_text(case)
    ]
    return {
        "serial": case["serial"],
        "intent": intent,
        "intent_label": label,
        "original_intent_label": (case.get("intent_correction") or {}).get("original_label", ""),
        "question": _excerpt(case.get("analysis_question") or case.get("question"), 1000),
        "chart": _excerpt("\n".join(facts), 2500),
        "reading": _excerpt(case.get("analysis"), 2000),
        "verdict": _excerpt(case.get("verdict"), 500),
        "feedback": feedback[-3:],
        "answer": _excerpt(case.get("answer"), 500),
    }


def _make_change(row: dict, rules: dict) -> dict:
    if not isinstance(row, dict):
        raise ValueError
    key = row.get("section")
    if not isinstance(key, str) or key not in rules:
        raise ValueError
    old, new = row.get("before"), row.get("after")
    if not all(isinstance(s, str) and len(s) <= 6000 and "\x00" not in s for s in (old, new)):
        raise ValueError
    text = rules[key]
    if not old.strip() or old not in text or text.find(old) != text.rfind(old) or old == new:
        raise ValueError
    updated = text.replace(old, new, 1).strip()
    if not updated or updated == text or len(updated) > max(16000, len(text)):
        raise ValueError
    variables = set(VARIABLE.findall(text))
    allowed = variables | set(VARIABLE.findall(WORKSHOP_SECTIONS[key][1]))
    if not variables <= set(VARIABLE.findall(updated)) <= allowed:
        raise ValueError
    return {
        "id": uuid4().hex,
        "section": key,
        "base": _digest(text),
        "before": old,
        "after": new,
        "reason": require_text(row.get("reason"), "修改依据", 1600),
        "status": "pending",
    }


def _model_reply(response, rules) -> tuple[str, list]:
    raw = str(getattr(response, "completion_text", "") or "").strip()
    if not raw or raw == "Output stopped." or getattr(response, "role", "") == "err":
        raise ManagementError("模型没有返回可用回复，请重试或更换模型", 502)
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", raw, flags=re.I | re.S)
    if fenced:
        raw = fenced.group(1)
    if not raw.startswith(("{", "[", "```")):
        return require_text(raw, "模型回复", 8000), []
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        reply = require_text(data.get("reply"), "模型回复", 8000)
        rows = data.get("changes", [])
        if not isinstance(rows, list) or len(rows) > len(rules):
            raise ValueError
        changes = [_make_change(row, rules) for row in rows]
        if len({item["section"] for item in changes}) != len(changes):
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise ManagementError(
            "模型回复格式有误，或建议未能精确匹配当前原文。本轮未生成可确认的修改，请重试。", 502
        ) from exc
    return reply, changes


class InterpretationWorkshopMixin:
    def _workshop_sections(self):
        sections = {
            key: {"label": label, "text": self._prompt_template(key, default), "editable": True}
            for key, (label, default) in WORKSHOP_SECTIONS.items()
        }
        for key, label, text in (
            (
                "classification_catalog",
                "可选分类及适用范围",
                self.readings.classification_catalog(),
            ),
            ("pre_review_protocol", "预审输出与安全边界", PRE_REVIEW_PROTOCOL),
            ("guardrail", "固定输出与专业边界", INTERPRETATION_GUARDRAIL),
            ("feedback_protocol", "反馈审核输出协议", REVIEW_GUARDRAIL),
            ("workshop_protocol", "优化建议审核协议", WORKSHOP_PROTOCOL),
        ):
            sections[key] = {"label": label, "text": text, "editable": False}
        library = self.readings.references
        for key in (*library.documents, *library.errors):
            label = "通则" if key == "common" else self.readings.directions[key]["label"]
            sections[f"reference_{key}"] = {
                "label": f"分类参考 · {label}",
                "text": library.documents.get(key, f"参考文档 {key}.md 未加载"),
                "editable": False,
            }
        return sections

    def _journal(self):
        if self._workshop_history is None:
            try:
                self._workshop_history = WorkshopHistory(history_path())
            except OSError as exc:
                raise ManagementError(
                    "无法打开优化记录，请检查插件数据目录的写入权限", 503
                ) from exc
        return self._workshop_history

    def _workshop_versions(self):
        return self._journal().sync_versions(
            {
                key: self._prompt_template(key, default)
                for key, (_, default) in WORKSHOP_SECTIONS.items()
            }
        )

    def _workshop_snapshot(self, actor):
        sections = self._workshop_sections()
        versions = self._journal().sync_versions(
            {key: sections[key]["text"] for key in WORKSHOP_SECTIONS}
        )
        return self._journal().conversation(actor) | {
            "sections": sections,
            "versions": versions,
            "directions": self.readings.direction_labels(),
        }

    def _checked_workshop(self, actor, revision):
        if actor in self._workshop_busy:
            raise ManagementError("上一轮对话正在处理，请稍后刷新", 409)
        state = self._workshop_snapshot(actor)
        if state["revision"] != revision:
            raise ManagementError("对话已更新，请刷新后重试", 409)
        return state

    async def _page_workshop(self, *, query, actor):
        providers = (
            self._model_choices()
            if callable(getattr(self.context, "get_all_providers", None))
            else []
        )
        preferred = self._config_get("admin_interpretation_provider_id", "") or self._config_get(
            "command_interpretation_provider_id", ""
        )
        return self._workshop_snapshot(actor) | {
            "providers": providers,
            "preferred": preferred,
            "processing": actor in self._workshop_busy,
        }

    async def _page_workshop_cases(self, *, query, actor):
        search, page = self._page_options(query)
        serial = re.fullmatch(r"[#＃]([0-9]{1,12})", search)
        selected_number = int(serial[1]) if serial else None
        if serial:
            search = ""
        intent = self._case_intent_filter(query)
        outcome = query.get("feedback", "verified")
        if outcome not in {"all", "verified", "unverified", *OUTCOMES}:
            raise ManagementError("未知反馈筛选条件")
        rows = []
        for case in normalize_case_store(await self.get_kv_data(CASES_KEY, {})):
            if selected_number is not None and case["serial"] != selected_number:
                continue
            direction, label = self.readings.case_direction(case)
            if intent and direction != intent:
                continue
            feedback = feedback_with_text(case)
            outcomes = list(dict.fromkeys(str(f.get("outcome") or "未分类") for f in feedback))
            if (
                (outcome == "verified" and not feedback)
                or (outcome == "unverified" and feedback)
                or (outcome in OUTCOMES and outcome not in outcomes)
            ):
                continue
            cast = case.get("cast") if isinstance(case.get("cast"), dict) else {}
            question = str(case.get("analysis_question") or case.get("question") or "")
            primary, changed = (
                str(cast.get("primary_name") or ""),
                str(cast.get("changed_name") or ""),
            )
            if search and not any(
                search in str(field).lower()
                for field in (
                    f"{case['serial']:03d}",
                    question,
                    case.get("question") or "",
                    primary,
                    changed,
                    case.get("verdict") or "",
                    *(f["text"] for f in feedback),
                )
            ):
                continue
            rows.append(
                {
                    "serial": case["serial"],
                    "question": _excerpt(question, 160),
                    "direction": label,
                    "primary": primary,
                    "changed": changed,
                    "outcomes": outcomes,
                }
            )
        rows.sort(key=lambda row: row["serial"], reverse=True)
        return self._page_result(rows, page) | {"directions": self.readings.direction_labels()}

    async def _page_workshop_history(self, *, query, actor):
        self._workshop_versions()
        if "id" in query:
            return self._journal().history_detail(actor, query["id"])
        return self._journal().history(actor, self._page_options(query)[1])

    async def _page_workshop_versions(self, *, query, actor):
        section = require_text(query.get("section"), "提示词章节", 80)
        self._workshop_versions()
        if "number" in query:
            detail = self._journal().version_detail(section, query["number"])
            return detail | {"can_view_turn": detail["actor"] == actor}
        if section not in WORKSHOP_SECTIONS:
            raise ManagementError("请选择可编辑的提示词章节")
        return self._journal().versions(section, self._page_options(query)[1])

    async def _workshop_cases(self, message, use_cases, serials):
        if type(use_cases) is not bool or not isinstance(serials, list) or len(serials) > MAX_CASES:
            raise ManagementError("每轮最多可引用 3 个卦例")
        if any(type(n) is not int or n <= 0 for n in serials) or (serials and not use_cases):
            raise ManagementError("请先勾选引用卦例库，再填写有效的卦例编号")
        if not use_cases:
            return []
        cases = normalize_case_store(await self.get_kv_data(CASES_KEY, {}))
        if serials:
            by_serial = {case["serial"]: case for case in cases}
            missing = [str(n) for n in serials if n not in by_serial]
            if missing:
                raise ManagementError("找不到卦例编号 " + "、".join(missing), 404)
            selected = [by_serial[n] for n in dict.fromkeys(serials)]
        else:
            # Dashboard administrators already have library-wide read access.
            intent = infer_intent(message)
            selected = search_cases(
                cases,
                group_id="",
                cross_group=True,
                query=message,
                limit=MAX_CASES,
                intent=intent if intent != "general" else "",
            )
        return [_case_material(case, *self.readings.case_direction(case)) for case in selected]

    async def _admin_workshop_chat(self, *, payload, actor):
        state = self._checked_workshop(actor, payload.get("revision"))
        message = require_text(payload.get("message"), "对话内容", 6000)
        provider = require_text(payload.get("provider"), "聊天模型", 256)
        if provider not in {p["id"] for p in self._model_choices()}:
            raise ManagementError("聊天模型已不可用，请刷新后重新选择")
        sections = state["sections"]
        focus = require_text(payload.get("focus"), "讨论章节", 80)
        if focus not in sections:
            raise ManagementError("未知的解卦章节")
        rules = {key: sections[key]["text"] for key in WORKSHOP_SECTIONS}
        self._workshop_busy.add(actor)
        turn_id = None
        try:
            use_cases = payload.get("use_cases", False)
            cases = await self._workshop_cases(message, use_cases, payload.get("serials", []))
            history = []
            size = 0
            for turn in reversed(state["turns"]):
                entry = {key: turn[key] for key in ("message", "reply", "cases")}
                entry["changes"] = [
                    {key: change[key] for key in ("section", "before", "after", "reason", "status")}
                    for change in turn["changes"]
                ]
                size += len(json.dumps(entry, ensure_ascii=False))
                if history and size > 24000:
                    break
                history.append(entry)
            topics = ["common", infer_intent(message), *(c["intent"] for c in cases)]
            topics = [self.readings.directions.get(key, {}).get("reference", key) for key in topics]
            references = {
                key: self.readings.references.documents[key]
                for key in topics
                if key in self.readings.references.documents
            }
            material = {
                "可编辑章节": rules,
                "固定约束": {
                    sections[key]["label"]: sections[key]["text"]
                    for key in (
                        "pre_review_protocol",
                        "guardrail",
                        "feedback_protocol",
                        "workshop_protocol",
                    )
                },
                "可选分类及适用范围": sections["classification_catalog"]["text"],
                "分类参考": references,
                "当前查看": {"key": focus, **sections[focus]},
                "先前对话": history[::-1],
                "本轮卦例": cases,
                "本轮问题": message,
            }
            turn_id = self._journal().start(
                actor,
                provider=provider,
                focus=focus,
                message=message,
                cases=cases,
                use_cases=use_cases,
                request_revision=state["revision"],
                versions=state["versions"],
            )
            token_run = self._new_token_run(None, kind="workshop")
            try:
                response = await asyncio.wait_for(
                    self._token_llm(
                        token_run,
                        "workshop",
                        chat_provider_id=provider,
                        system_prompt=rules["workshop_discussion_prompt"]
                        + "\n\n"
                        + WORKSHOP_PROTOCOL,
                        prompt=json.dumps(material, ensure_ascii=False),
                    ),
                    timeout=self._bounded_config_int(
                        "command_analysis_timeout_seconds", 60, 5, 180
                    ),
                )
            except asyncio.TimeoutError as exc:
                raise ManagementError("模型回复超时，请重试或更换模型", 504) from exc
            except Exception as exc:
                if token_run["calls"] and token_run["calls"][-1]["status"] == "not_called":
                    raise ManagementError("Token 账本暂不可用，本轮尚未调用模型", 503) from exc
                logger.warning("liuyao：解卦优化模型调用失败（%s）", type(exc).__name__)
                raise ManagementError("模型调用失败，请检查所选聊天模型后重试", 502) from exc
            reply, changes = _model_reply(response, rules)
            self._journal().finish(turn_id, actor, reply, changes, state["versions"])
            return self._workshop_snapshot(actor)
        except asyncio.CancelledError:
            if turn_id is not None:
                self._journal().fail(turn_id, "本轮请求已中断。", interrupted=True)
            raise
        except Exception as exc:
            if turn_id is not None:
                detail = str(exc) if isinstance(exc, ManagementError) else "本轮优化处理失败。"
                self._journal().fail(turn_id, detail)
            raise
        finally:
            self._workshop_busy.discard(actor)

    async def _admin_workshop_decide(self, *, payload, actor):
        if set(payload) != {"id", "action"} or payload["action"] not in ("approve", "dismiss"):
            raise ManagementError("请逐项选择确认采用或放弃建议")
        state = self._workshop_snapshot(actor)
        change = next(
            (
                item
                for turn in state["turns"]
                for item in turn["changes"]
                if item["id"] == payload["id"]
            ),
            None,
        )
        if change is None:
            raise ManagementError("找不到当前账号的待审建议，请刷新页面", 404)
        status = "approved" if payload["action"] == "approve" else "dismissed"
        if change["status"] == status:
            return state
        if actor in self._workshop_busy or change["status"] != "pending":
            raise ManagementError("对话处理中或建议状态已变化，请刷新后核对", 409)
        if status == "approved":
            key = change["section"]
            current = self._prompt_template(key, WORKSHOP_SECTIONS[key][1])
            if change["base"] != _digest(current):
                raise ManagementError("本节原文已变化，请按最新逻辑重新生成建议", 409)
            saver = getattr(self.config, "save_config", None)
            if not callable(saver):
                raise ManagementError("当前 AstrBot 配置无法持久保存", 503)
            # Persist intent before writing the separate AstrBot config. A lost response
            # or process restart can then reconcile the exact version without applying twice.
            self._journal().decide(change["id"], actor, "applying")
            existed, previous = key in self.config, self.config.get(key)
            try:
                self.config[key] = current.replace(change["before"], change["after"], 1).strip()
                saver()
            except Exception as exc:
                if existed:
                    self.config[key] = previous
                else:
                    self.config.pop(key, None)
                raise ManagementError("配置保存失败，原规则已保留，可重试确认", 500) from exc
        else:
            self._journal().decide(change["id"], actor, status)
        return self._workshop_snapshot(actor)

    async def _admin_workshop_reset(self, *, payload, actor):
        self._checked_workshop(actor, payload.get("revision"))
        self._journal().reset(actor)
        return self._workshop_snapshot(actor)
