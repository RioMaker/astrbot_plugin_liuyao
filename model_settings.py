"""Administrator model overrides and authenticated settings-page operations."""

from __future__ import annotations

import hashlib
import json

from astrbot.api import logger

if __package__:
    from .user_management import ManagementError, require_text
else:
    from user_management import ManagementError, require_text


ADMIN_MODEL_KEYS = ("admin_pre_review_provider_id", "admin_interpretation_provider_id")


class ModelSettingsMixin:
    @staticmethod
    def _is_astrbot_admin(event) -> bool:
        try:
            check = getattr(event, "is_admin", None)
            return callable(check) and check() is True
        except Exception:
            return False

    def _analysis_provider_config(self, event, config_key):
        if self._is_astrbot_admin(event):
            admin_key = config_key.replace("command_", "admin_", 1)
            configured = str(self._config_get(admin_key, "") or "").strip()
            if configured:
                return configured, "管理员专用配置"
        configured = str(self._config_get(config_key, "") or "").strip()
        return configured, "插件通用配置" if configured else "当前会话系统模型"

    async def _analysis_provider_id(self, event, config_key) -> str:
        configured, _source = self._analysis_provider_config(event, config_key)
        if configured:
            return configured
        provider_id = await self.context.get_current_chat_provider_id(
            umo=str(getattr(event, "unified_msg_origin", "") or "")
        )
        if not provider_id:
            raise RuntimeError("当前会话没有可用模型")
        return provider_id

    def _model_choices(self):
        getter = getattr(self.context, "get_all_providers", None)
        if not callable(getter):
            raise ManagementError("当前 AstrBot 无法列出聊天模型，请在插件配置中设置", 503)
        choices = {}
        for provider in getter():
            meta = provider.meta()
            provider_id = str(meta.id)
            choices[provider_id] = {"id": provider_id, "model": str(meta.model or "")}
        return sorted(choices.values(), key=lambda item: item["id"])

    def _admin_model_values(self):
        return {key: str(self._config_get(key, "") or "").strip() for key in ADMIN_MODEL_KEYS}

    @staticmethod
    def _model_revision(values):
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    async def _page_models(self, *, query):
        values = self._admin_model_values()
        return {
            "values": values,
            "revision": self._model_revision(values),
            "providers": self._model_choices(),
            "defaults": {
                key.replace("admin_", "command_", 1): str(
                    self._config_get(key.replace("admin_", "command_", 1), "") or ""
                ).strip()
                for key in ADMIN_MODEL_KEYS
            },
        }

    async def _admin_save_models(self, *, payload, actor):
        values = {
            key: require_text(payload.get(key), "模型 ID", 256, empty=True)
            for key in ADMIN_MODEL_KEYS
        }
        current = self._admin_model_values()
        if payload.get("revision") != self._model_revision(current):
            raise ManagementError("模型设置已变化，请刷新后重新选择", 409)
        available = {item["id"] for item in self._model_choices()}
        if any(value and value not in available for value in values.values()):
            raise ManagementError("所选聊天模型已不可用，请刷新后重新选择")
        saver = getattr(self.config, "save_config", None)
        if not callable(saver):
            raise ManagementError("当前配置无法持久保存，请使用 AstrBot 插件配置页", 503)
        # No await between checking the revision, applying values and persisting.
        missing = object()
        previous = {key: self.config.get(key, missing) for key in ADMIN_MODEL_KEYS}
        try:
            self.config.update(values)
            saver()
        except Exception:
            for key, value in previous.items():
                if value is missing:
                    self.config.pop(key, None)
                else:
                    self.config[key] = value
            raise ManagementError("模型设置保存失败，当前运行设置已恢复，请刷新确认", 500) from None
        logger.info("liuyao：管理员模型设置已由后台保存")
        return {"saved": True, "values": values, "revision": self._model_revision(values)}
