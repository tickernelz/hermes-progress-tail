from __future__ import annotations

import inspect
from typing import Any


def adapter_supports_edit(adapter: Any) -> bool:
    edit = getattr(adapter, "edit_message", None)
    if not callable(edit):
        return False
    try:
        from gateway.platforms.base import BasePlatformAdapter

        return type(adapter).edit_message is not BasePlatformAdapter.edit_message
    except Exception:
        return True


def platform_name(source: Any) -> str:
    platform = getattr(source, "platform", "")
    return str(getattr(platform, "value", platform) or "")


def source_thread_id(source: Any) -> str | None:
    value = getattr(source, "thread_id", None)
    return str(value) if value else None


def source_chat_id(source: Any) -> str:
    return str(getattr(source, "chat_id", "") or "")


def source_chat_type(source: Any) -> str:
    return str(getattr(source, "chat_type", "") or "")


def source_message_id(source: Any) -> str | None:
    value = getattr(source, "message_id", None)
    return str(value) if value is not None and str(value) else None


async def delete_message(adapter: Any, chat_id: str, message_id: str) -> bool:
    delete = getattr(adapter, "delete_message", None)
    if callable(delete):
        result = delete(chat_id, message_id)
        if inspect.isawaitable(result):
            result = await result
        return bool(getattr(result, "success", result if result is not None else True))
    bot = getattr(adapter, "_bot", None)
    bot_delete = getattr(bot, "delete_message", None)
    if callable(bot_delete):
        result = bot_delete(chat_id=chat_id, message_id=_coerce_numeric_id(message_id))
        if inspect.isawaitable(result):
            result = await result
        return bool(getattr(result, "success", result if result is not None else True))
    return False


def _coerce_numeric_id(value: str) -> str | int:
    text = str(value or "")
    try:
        return int(text)
    except ValueError:
        return text
