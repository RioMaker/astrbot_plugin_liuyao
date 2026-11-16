"""Authenticated AstrBot Plugin Pages endpoints."""

from __future__ import annotations

import asyncio
import base64
import copy
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from astrbot.api import logger

if __package__:
    from .case_export import build_case_export
    from .case_library import (
        analysis_history,
        feedback_closed,
        feedback_eligible,
        feedback_status,
        normalize_case_store,
        serialize_case_store,
    )
    from .feedback_card import case_cast
    from .paipan import BEIJING
    from .renderer import ChartRenderError
    from .token_usage import case_usage_key, overview, summarize
    from .user_management import CASES_KEY, ManagementError, case_revision, require_text
else:
    from case_export import build_case_export
    from case_library import (
        analysis_history,
        feedback_closed,
        feedback_eligible,
        feedback_status,
        normalize_case_store,
        serialize_case_store,
    )
    from feedback_card import case_cast
    from paipan import BEIJING
    from renderer import ChartRenderError
    from token_usage import case_usage_key, overview, summarize
    from user_management import CASES_KEY, ManagementError, case_revision, require_text


class AdminPageMixin:
    def _register_admin_page(self) -> None:
        self._admin_page_active = True
        register = getattr(self.context, "register_web_api", None)
        if not callable(register):
            logger.warning("liuyao：当前 AstrBot 不支持插件管理页 API")
            return
        try:
            from astrbot.api.web import error_response, json_response, request
        except ImportError:
            logger.warning("liuyao：管理页需要支持 astrbot.api.web 的 AstrBot 版本")
            return

        def handler_for(callback, *, post=False, raw_response=False):
            async def handler(**params):
                if not self._admin_page_active:
                    return error_response("六爻插件已卸载，请重载插件后刷新页面", status_code=503)
                # Identity is supplied by Dashboard, never accepted from Page JSON.
                if not request.username:
                    return error_response("请先登录 AstrBot 管理后台", status_code=401)
                try:
                    if post:
                        body = await request.json(default={})
                        if not isinstance(body, dict):
                            raise ManagementError("请求体必须为 JSON 对象")
                        result = await callback(**params, payload=body, actor=request.username)
                    else:
                        result = await callback(**params, query=request.query)
                    return result if raw_response else json_response(result)
                except ManagementError as exc:
                    return error_response(str(exc), status_code=exc.status)
                except Exception as exc:
                    logger.exception("liuyao：管理页操作失败：%s", type(exc).__name__)
                    return error_response(
                        "操作失败，请刷新确认状态；详细原因见 AstrBot 日志", status_code=500
                    )

            return handler

        for route, callback, post in (
            ("manage/users", self._page_users, False),
            ("manage/users/<sender>", self._page_user, False),
            ("manage/users/<sender>/grant", self._admin_grant, True),
            ("manage/users/<sender>/update", self._admin_update_user, True),
            ("manage/cases", self._page_cases, False),
            ("manage/cases/<case_id>", self._page_case, False),
            ("manage/cases/<case_id>/render", self._page_render_case, True),
            ("manage/cases/<case_id>/update", self._admin_change_case, True),
            ("manage/usage", self._page_usage, False),
            ("manage/usage/<usage_id>", self._page_usage_detail, False),
            ("manage/models", self._page_models, False),
            ("manage/models/save", self._admin_save_models, True),
        ):
            register(
                f"/astrbot_plugin_liuyao/{route}",
                handler_for(callback, post=post),
                ["POST" if post else "GET"],
                "六爻用户与卦例管理",
            )

        register(
            "/astrbot_plugin_liuyao/manage/export/cases",
            handler_for(self._page_export_cases, raw_response=True),
            ["GET"],
            "导出全部六爻卦例及存储说明",
        )

    async def _page_export_cases(self, *, query):
        from astrbot.api.web import stream_response

        # Export the stored value itself, including legacy and unknown fields.
        # Detach it under the writer lock, then release the lock before compression.
        async with self._case_lock_instance():
            snapshot = copy.deepcopy(await self.get_kv_data(CASES_KEY, serialize_case_store([])))
            exported_at = datetime.now(BEIJING)
        archive = await asyncio.to_thread(build_case_export, snapshot)
        filename = f"liuyao_cases_{exported_at.strftime('%Y%m%d_%H%M%S')}.zip"

        async def chunks():
            for offset in range(0, len(archive), 64 * 1024):
                yield archive[offset : offset + 64 * 1024]

        return stream_response(
            chunks(),
            content_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(archive)),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @staticmethod
    def _page_options(query) -> tuple[str, int]:
        search = require_text(query.get("search", ""), "搜索内容", 200, empty=True).lower()
        try:
            page = int(query.get("page", "1"))
        except (ValueError, TypeError) as exc:
            raise ManagementError("页码必须为整数") from exc
        if not 1 <= page <= 100000:
            raise ManagementError("页码超出范围")
        return search, page

    @staticmethod
    def _page_result(rows: list, page: int) -> dict:
        pages = max(1, (len(rows) + 19) // 20)
        page = min(page, pages)
        return {
            "items": rows[(page - 1) * 20 : page * 20],
            "page": page,
            "pages": pages,
            "total": len(rows),
        }

    async def _page_users(self, *, query) -> dict:
        await self._ensure_user_registry()
        search, page = self._page_options(query)
        registry = await self._load_user_registry()
        users = list(registry["users"].values())
        state = query.get("state", "all")
        if state not in {"all", "active", "banned"}:
            raise ManagementError("未知用户筛选条件")
        filtered = [
            u
            for u in users
            if (not search or search in " ".join([u["id"], u["name"], *u["groups"]]).lower())
            and (state == "all" or u["banned"] == (state == "banned"))
        ]
        filtered.sort(key=lambda u: u["last_cast_at"], reverse=True)
        result = self._page_result(filtered, page)
        runs = await self._token_runs()
        async with self._daily_lock():
            for user in result["items"]:
                user["token_usage"] = summarize([r for r in runs if r["caster_id"] == user["id"]])
                gift = await self._load_gift_record(self._gift_key(user["id"]))
                user["gift_remaining"] = self._gift_available(gift)
                user.pop("audit", None)
                user.pop("quota_keys", None)
        result["stats"] = {
            "users": len(users),
            "banned": sum(u["banned"] for u in users),
            "casts": sum(u["cast_count"] for u in users),
        }
        return result

    async def _page_user(self, sender: str, *, query) -> dict:
        await self._ensure_user_registry()
        user = await self._admin_user_detail(sender)
        runs = [r for r in await self._token_runs() if r["caster_id"] == sender]
        user["token_overview"] = overview(runs, self._ai_daily_now())
        return user

    async def _page_cases(self, *, query) -> dict:
        search, page = self._page_options(query)
        cases = normalize_case_store(await self.get_kv_data(CASES_KEY, {}))
        state = query.get("state", "all")
        if state not in {"all", "pending", "feedback", "unavailable"}:
            raise ManagementError("未知卦例筛选条件")
        sender = require_text(query.get("sender", ""), "用户编号", 128, empty=True)
        rows = []
        for case in cases:
            feedback = feedback_closed(case)
            eligible = feedback_eligible(case)
            text = " ".join(
                str(case.get(k) or "")
                for k in (
                    "id",
                    "serial",
                    "caster_id",
                    "caster_name",
                    "group_id",
                    "question",
                    "analysis_question",
                    "admin_note",
                )
            )
            if search and search not in text.lower():
                continue
            if sender and case.get("caster_id") != sender:
                continue
            if (
                (state == "pending" and not eligible)
                or (state == "feedback" and not feedback)
                or (state == "unavailable" and (feedback or eligible))
            ):
                continue
            rows.append(
                {
                    k: case.get(k, "")
                    for k in (
                        "id",
                        "serial",
                        "caster_id",
                        "caster_name",
                        "group_id",
                        "question",
                        "analysis_question",
                        "created_at",
                    )
                }
                | {
                    "feedback_closed": feedback,
                    "feedback_eligible": eligible,
                    "feedback_status": feedback_status(case),
                    "primary_name": case.get("cast", {}).get("primary_name", ""),
                }
            )
        rows.sort(key=lambda c: c["created_at"], reverse=True)
        result = self._page_result(rows, page)
        runs = await self._token_runs()
        keys = {c["id"]: case_usage_key(c) for c in cases}
        for row in result["items"]:
            row["usage_id"] = keys[row["id"]]
            row["token_usage"] = summarize([r for r in runs if r["cast_key"] == row["usage_id"]])
        result["all_count"] = len(cases)
        return result

    async def _page_case(self, case_id: str, *, query) -> dict:
        cases = normalize_case_store(await self.get_kv_data(CASES_KEY, {}))
        case = next((c for c in cases if c["id"] == case_id), None)
        if case is None:
            raise ManagementError("卦例不存在或已删除", 404)
        key = case_usage_key(case)
        runs = [r for r in await self._token_runs() if r["cast_key"] == key]
        return {
            "case": {**case, "analysis_history": analysis_history(case)},
            "revision": case_revision(case),
            "token_usage": summarize(runs),
            "usage_id": key,
        }

    async def _page_render_case(self, case_id: str, *, payload, actor) -> dict:
        # Read the saved facts only; rendering must never create a new cast or analysis.
        async with self._case_lock_instance():
            cases = normalize_case_store(await self.get_kv_data(CASES_KEY, {}))
            case = next((c for c in cases if c["id"] == case_id), None)
            if case is None:
                raise ManagementError("卦例不存在或已删除", 404)
            case = copy.deepcopy(case)
        renderer = getattr(self, "renderer", None)
        if renderer is None:
            raise ManagementError("卦图渲染器不可用，请检查字体配置并重载插件后重试", 503)

        def render_preview():
            # Own cleanup inside the worker, even if the HTTP request is cancelled.
            with TemporaryDirectory(prefix="liuyao_preview_") as directory:
                output = Path(directory) / "chart.png"
                try:
                    cast = case_cast(case)
                    renderer.render(
                        cast,
                        caster_name=str(case.get("caster_name") or ""),
                        caster_id=str(case.get("caster_id") or ""),
                        group_id=str(case.get("group_id") or ""),
                        intent_label=str(case.get("intent_label") or ""),
                        question=str(case.get("question") or ""),
                        method=str(case.get("method") or ""),
                        cast_at=cast.cast_at,
                        output_path=output,
                    )
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    raise ManagementError("卦例的六爻或排盘数据不完整，无法生成卦图", 422) from exc
                image = output.read_bytes()
                if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ChartRenderError("渲染器没有生成有效 PNG 文件")
                # JSON travels through the authenticated Bridge; no public file URL.
                return "data:image/png;base64," + base64.b64encode(image).decode("ascii")

        return {"case_id": case_id, "image_data_url": await asyncio.to_thread(render_preview)}
