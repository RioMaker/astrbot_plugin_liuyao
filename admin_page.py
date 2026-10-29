"""Authenticated AstrBot Plugin Pages endpoints."""

from __future__ import annotations

from astrbot.api import logger

if __package__:
    from .case_library import normalize_case_store
    from .user_management import CASES_KEY, ManagementError, case_revision, require_text
else:
    from case_library import normalize_case_store
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

        def handler_for(callback, *, post=False):
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
                    return json_response(result)
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
            ("manage/cases/<case_id>/update", self._admin_change_case, True),
        ):
            register(
                f"/astrbot_plugin_liuyao/{route}",
                handler_for(callback, post=post),
                ["POST" if post else "GET"],
                "六爻用户与卦例管理",
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
        async with self._daily_lock():
            for user in result["items"]:
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
        return await self._admin_user_detail(sender)

    async def _page_cases(self, *, query) -> dict:
        search, page = self._page_options(query)
        cases = normalize_case_store(await self.get_kv_data(CASES_KEY, {}))
        state = query.get("state", "all")
        if state not in {"all", "pending", "feedback"}:
            raise ManagementError("未知卦例筛选条件")
        sender = require_text(query.get("sender", ""), "用户编号", 128, empty=True)
        rows = []
        for case in cases:
            feedback = any(f.get("valid") is not False for f in case.get("feedback", []))
            text = " ".join(
                str(case.get(k) or "")
                for k in (
                    "id",
                    "serial",
                    "caster_id",
                    "caster_name",
                    "group_id",
                    "question",
                    "admin_note",
                )
            )
            if search and search not in text.lower():
                continue
            if sender and case.get("caster_id") != sender:
                continue
            if state != "all" and feedback != (state == "feedback"):
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
                        "created_at",
                    )
                }
                | {
                    "feedback_closed": feedback,
                    "primary_name": case.get("cast", {}).get("primary_name", ""),
                }
            )
        rows.sort(key=lambda c: c["created_at"], reverse=True)
        result = self._page_result(rows, page)
        result["all_count"] = len(cases)
        return result

    async def _page_case(self, case_id: str, *, query) -> dict:
        cases = normalize_case_store(await self.get_kv_data(CASES_KEY, {}))
        case = next((c for c in cases if c["id"] == case_id), None)
        if case is None:
            raise ManagementError("卦例不存在或已删除", 404)
        return {"case": case, "revision": case_revision(case)}
