from __future__ import annotations

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
