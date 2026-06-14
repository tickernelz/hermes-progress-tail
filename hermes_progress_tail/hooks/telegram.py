from __future__ import annotations

import inspect
import logging
from contextlib import suppress
from functools import wraps
from typing import Any

from ..rendering.telegram_rich import (
    format_progress_tail_telegram_rich_markdown,
    telegram_rich_message_payload,
)

logger = logging.getLogger(__name__)
_TELEGRAM_ORIGINALS: dict[type, Any] = {}
_TELEGRAM_SEND_ORIGINALS: dict[type, Any] = {}
_TELEGRAM_TOPIC_RECOVERY_ORIGINALS: dict[type, Any] = {}
_TELEGRAM_PATCH_MARKER = "_hermes_progress_tail_telegram_format_patched"
_TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER = "_hermes_progress_tail_telegram_topic_recovery_patched"


def _telegram_edit_target_lost(error_text: str) -> bool:
    text = str(error_text or "").lower()
    return (
        "message to edit not found" in text
        or "message not found" in text
        or "message_id_invalid" in text
        or "unknown message" in text
        or ("message_id" in text and "not found" in text)
    )


def format_progress_tail_telegram_markdown(content: str, formatter: Any) -> str:
    text = str(content or "")
    placeholders: dict[str, str] = {}

    def stash(value: str) -> str:
        key = f"\x00HPT{len(placeholders)}\x00"
        placeholders[key] = value
        return key

    def title_repl(match):
        title = _escape_telegram_mdv2(match.group(1).strip())
        return stash(f"*__{title}__*")

    def bold_italic_repl(match):
        body = _escape_telegram_mdv2(match.group(1).strip())
        return stash(f"*_{body}_*")

    text = _replace_outside_code(text, r"\*\*__([^\n*_][^\n]*?)__\*\*", title_repl)
    text = _replace_outside_code(text, r"\*\*\*([^\n*][^\n]*?)\*\*\*", bold_italic_repl)
    formatted = formatter(text)
    for key, value in placeholders.items():
        formatted = formatted.replace(_escape_telegram_mdv2(key), value).replace(key, value)
    return formatted


def _runtime_telegram_settings() -> Any:
    try:
        from ..runtime import plugin as runtime_plugin

        return runtime_plugin._get_renderer().settings.telegram
    except Exception:
        return None


def _telegram_rich_enabled(adapter: Any) -> bool:
    override = getattr(adapter, "_hermes_progress_tail_rich_messages", None)
    if override is not None:
        return bool(override)
    if getattr(adapter, "_hermes_progress_tail_rich_disabled", False):
        return False
    settings = _runtime_telegram_settings()
    if settings is None:
        return True
    return bool(getattr(settings, "rich_messages", True))


def _telegram_rich_markdown(content: str) -> str:
    settings = _runtime_telegram_settings()
    return format_progress_tail_telegram_rich_markdown(
        content,
        max_table_rows=getattr(settings, "max_table_rows", 8),
        verification_table=getattr(settings, "verification_table", True),
        thinking_blocks=getattr(settings, "thinking_blocks", True),
        compact_success=getattr(settings, "compact_success", True),
        max_detail_items=getattr(settings, "max_detail_items", 8),
    )


def _bot_supports_rich_edit(bot: Any) -> bool:
    return inspect.iscoroutinefunction(getattr(bot, "do_api_request", None))


def _telegram_rich_capability_error(exc: Exception) -> bool:
    if isinstance(exc, (AttributeError, TypeError, NotImplementedError)):
        return True
    if getattr(exc, "error_code", None) == 404:
        return True
    text = str(exc).lower()
    return (
        "no such method" in text
        or (("method" in text or "endpoint" in text) and "not found" in text)
        or "unsupported" in text
        or "not implemented" in text
    )


def _telegram_rich_fallback_error(exc: Exception) -> bool:
    if _telegram_rich_capability_error(exc):
        return True
    name = exc.__class__.__name__.lower()
    text = str(exc).lower()
    return "badrequest" in name or "bad request" in text or "can't parse" in text


async def _try_edit_rich_message(
    adapter: Any,
    chat_id: str,
    message_id: str,
    content: str,
    send_result_cls: Any,
) -> Any | None:
    if not _telegram_rich_enabled(adapter):
        return None
    bot = getattr(adapter, "_bot", None)
    if not bot or not _bot_supports_rich_edit(bot):
        return None
    try:
        rich_markdown = _telegram_rich_markdown(content)
        payload = {
            "chat_id": int(chat_id),
            "message_id": int(message_id),
            "rich_message": telegram_rich_message_payload(rich_markdown),
        }
        await bot.do_api_request("editMessageText", api_kwargs=payload)
        return send_result_cls(success=True, message_id=message_id)
    except Exception as exc:
        err_text = str(exc)
        err_lower = err_text.lower()
        if "not modified" in err_lower:
            return send_result_cls(success=True, message_id=message_id)
        if _telegram_edit_target_lost(err_lower):
            return send_result_cls(
                success=False,
                message_id=message_id,
                error=f"message_lost: {err_text}",
                retryable=False,
            )
        if _telegram_rich_fallback_error(exc):
            if _telegram_rich_capability_error(exc):
                adapter._hermes_progress_tail_rich_disabled = True
            logger.debug(
                "hermes-progress-tail Telegram rich edit rejected; falling back MarkdownV2",
                exc_info=True,
            )
            return None
        logger.debug("hermes-progress-tail Telegram rich edit transient failure", exc_info=True)
        return send_result_cls(success=False, message_id=message_id, error=err_text, retryable=True)


async def _try_send_rich_message(
    adapter: Any,
    chat_id: str,
    content: str,
    send_result_cls: Any,
    *,
    reply_to: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any | None:
    if not _telegram_rich_enabled(adapter):
        return None
    rich_markdown = _telegram_rich_markdown(content)
    try_send_rich = getattr(adapter, "_try_send_rich", None)
    if callable(try_send_rich):
        try:
            should_attempt = getattr(adapter, "_should_attempt_rich", None)
            if callable(should_attempt) and not should_attempt(rich_markdown, metadata=metadata):
                return None
            result = await try_send_rich(chat_id, rich_markdown, reply_to, metadata)
            if result is None and getattr(adapter, "_rich_send_disabled", False):
                adapter._hermes_progress_tail_rich_disabled = True
            return result
        except Exception as exc:
            return _send_rich_exception_result(adapter, exc, send_result_cls)
    bot = getattr(adapter, "_bot", None)
    if not bot or not _bot_supports_rich_edit(bot):
        return None
    try:
        payload = {
            "chat_id": int(chat_id),
            "rich_message": telegram_rich_message_payload(rich_markdown),
        }
        thread_id = (metadata or {}).get("thread_id")
        if thread_id is not None:
            payload["message_thread_id"] = int(thread_id)
        msg = await bot.do_api_request("sendRichMessage", api_kwargs=payload)
        message_id = None
        if isinstance(msg, dict):
            message_id = msg.get("message_id") or (msg.get("result") or {}).get("message_id")
        else:
            message_id = getattr(msg, "message_id", None)
        return send_result_cls(success=True, message_id=str(message_id) if message_id else None)
    except Exception as exc:
        return _send_rich_exception_result(adapter, exc, send_result_cls)


def _send_rich_exception_result(adapter: Any, exc: Exception, send_result_cls: Any) -> Any | None:
    if _telegram_rich_fallback_error(exc):
        if _telegram_rich_capability_error(exc):
            adapter._hermes_progress_tail_rich_disabled = True
            with suppress(Exception):
                adapter._rich_send_disabled = True
        logger.debug(
            "hermes-progress-tail Telegram rich send rejected; falling back MarkdownV2",
            exc_info=True,
        )
        return None
    logger.debug("hermes-progress-tail Telegram rich send transient failure", exc_info=True)
    return send_result_cls(success=False, error=str(exc), retryable=True)


def _legacy_send_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    legacy = dict(metadata or {})
    legacy["expect_edits"] = True
    return legacy


def _original_send_kwargs(reply_to: str | None, metadata: dict[str, Any] | None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"metadata": _legacy_send_metadata(metadata)}
    if reply_to is not None:
        kwargs["reply_to"] = reply_to
    return kwargs


def _replace_outside_code(text: str, pattern: str, repl: Any) -> str:
    import re

    parts = re.split(r"(```[\s\S]*?```|`[^`]*`)", str(text or ""))
    for index, part in enumerate(parts):
        if part.startswith("`"):
            continue
        parts[index] = re.sub(pattern, repl, part)
    return "".join(parts)


def _escape_telegram_mdv2(text: str) -> str:
    specials = r"\\_*[]()~`>#+-=|{}.!"
    return "".join("\\" + char if char in specials else char for char in str(text or ""))


def install_telegram_topic_recovery_monkeypatch(gateway_runner_cls: type | None = None) -> bool:
    runner_cls = gateway_runner_cls
    if runner_cls is None:
        try:
            from gateway.run import GatewayRunner

            runner_cls = GatewayRunner
        except Exception as exc:
            logger.debug(
                "hermes-progress-tail could not import GatewayRunner for Telegram topic recovery: %s",
                exc,
            )
            return False
    if getattr(runner_cls, _TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER, False):
        return True
    original_edit = getattr(runner_cls, "_recover_telegram_topic_thread_id", None)
    if original_edit is None:
        logger.debug(
            "hermes-progress-tail Telegram topic recovery monkeypatch disabled: API missing"
        )
        return False
    _TELEGRAM_TOPIC_RECOVERY_ORIGINALS[runner_cls] = original_edit

    @wraps(original_edit)
    def patched_recover_telegram_topic_thread_id(self, source):
        if _should_preserve_telegram_topic_thread(self, source):
            logger.debug(
                "hermes-progress-tail preserving concrete Telegram topic thread_id=%s",
                getattr(source, "thread_id", None),
            )
            return None
        return original_edit(self, source)

    runner_cls._recover_telegram_topic_thread_id = patched_recover_telegram_topic_thread_id
    setattr(runner_cls, _TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER, True)
    return True


def _should_preserve_telegram_topic_thread(gateway: Any, source: Any) -> bool:
    if str(getattr(source, "chat_type", "") or "") != "dm":
        return False
    platform = getattr(source, "platform", "")
    platform_value = getattr(platform, "value", platform)
    if str(platform_value or "").lower() != "telegram":
        return False
    inbound = str(getattr(source, "thread_id", "") or "")
    general_ids = getattr(gateway, "_TELEGRAM_GENERAL_TOPIC_IDS", frozenset({"", "1"}))
    general_ids = {str(item) for item in general_ids}
    return bool(inbound) and inbound not in general_ids


def install_telegram_format_monkeypatch(telegram_adapter_cls: type | None = None) -> bool:
    if telegram_adapter_cls is None:
        try:
            from gateway.platforms.telegram import TelegramAdapter as telegram_adapter_cls
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import TelegramAdapter: %s", exc)
            return False
    if telegram_adapter_cls.__dict__.get(_TELEGRAM_PATCH_MARKER, False):
        return True
    original_edit = getattr(telegram_adapter_cls, "edit_message", None)
    original_send = getattr(telegram_adapter_cls, "send", None)
    if original_edit is None:
        logger.warning(
            "hermes-progress-tail Telegram formatting monkeypatch disabled: edit_message missing"
        )
        return False
    _TELEGRAM_ORIGINALS[telegram_adapter_cls] = original_edit
    if original_send is not None:
        _TELEGRAM_SEND_ORIGINALS[telegram_adapter_cls] = original_send

    @wraps(original_edit)
    async def patched_edit_message(
        self, chat_id, message_id, content, *, finalize=False, metadata=None
    ):
        if finalize:
            return await original_edit(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )
        if not getattr(self, "_bot", None):
            return await original_edit(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )
        try:
            from gateway.platforms.base import SendResult, utf16_len
            from gateway.platforms.telegram import ParseMode
        except Exception:
            return await original_edit(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )
        try:
            max_len = int(getattr(self, "MAX_MESSAGE_LENGTH", 4096) or 4096)
            if utf16_len(str(content or "")) > max_len:
                return await original_edit(
                    self, chat_id, message_id, content, finalize=finalize, metadata=metadata
                )
        except Exception:
            return await original_edit(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )
        try:
            rich_result = await _try_edit_rich_message(
                self, chat_id, message_id, content, SendResult
            )
            if rich_result is not None:
                return rich_result
            formatted = format_progress_tail_telegram_markdown(content, self.format_message)
            await self._bot.edit_message_text(
                chat_id=int(chat_id),
                message_id=int(message_id),
                text=formatted,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return SendResult(success=True, message_id=message_id)
        except Exception as fmt_err:
            err_text = str(fmt_err)
            err_lower = err_text.lower()
            if "not modified" in err_lower:
                return SendResult(success=True, message_id=message_id)
            if _telegram_edit_target_lost(err_lower):
                logger.debug(
                    "hermes-progress-tail Telegram live edit target disappeared; "
                    "requesting fresh progress message",
                    exc_info=True,
                )
                return SendResult(
                    success=False,
                    message_id=message_id,
                    error=f"message_lost: {err_text}",
                    retryable=False,
                )
            logger.debug(
                "hermes-progress-tail Telegram formatted live edit failed; falling back plain",
                exc_info=True,
            )
            return await original_edit(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )

    if original_send is not None:

        @wraps(original_send)
        async def patched_send(self, chat_id, content, reply_to=None, metadata=None):
            try:
                from gateway.platforms.base import SendResult
            except Exception:
                return await original_send(
                    self, chat_id, content, **_original_send_kwargs(reply_to, metadata)
                )
            rich_result = await _try_send_rich_message(
                self,
                chat_id,
                content,
                SendResult,
                reply_to=reply_to,
                metadata=metadata,
            )
            if rich_result is not None:
                return rich_result
            return await original_send(
                self, chat_id, content, **_original_send_kwargs(reply_to, metadata)
            )

        telegram_adapter_cls.send = patched_send

    telegram_adapter_cls.edit_message = patched_edit_message
    setattr(telegram_adapter_cls, _TELEGRAM_PATCH_MARKER, True)
    return True


def uninstall_telegram_format_monkeypatch(telegram_adapter_cls: type | None = None) -> bool:
    if telegram_adapter_cls is None:
        try:
            from gateway.platforms.telegram import TelegramAdapter as telegram_adapter_cls
        except Exception:
            return False
    original_edit = _TELEGRAM_ORIGINALS.pop(telegram_adapter_cls, None)
    original_send = _TELEGRAM_SEND_ORIGINALS.pop(telegram_adapter_cls, None)
    if original_edit is None:
        return False
    telegram_adapter_cls.edit_message = original_edit
    if original_send is not None:
        telegram_adapter_cls.send = original_send
    try:
        delattr(telegram_adapter_cls, _TELEGRAM_PATCH_MARKER)
    except Exception:
        setattr(telegram_adapter_cls, _TELEGRAM_PATCH_MARKER, False)
    return True


def uninstall_telegram_topic_recovery_monkeypatch(gateway_runner_cls: type | None = None) -> bool:
    runner_cls = gateway_runner_cls
    if runner_cls is None:
        try:
            from gateway.run import GatewayRunner

            runner_cls = GatewayRunner
        except Exception:
            return False
    original_edit = _TELEGRAM_TOPIC_RECOVERY_ORIGINALS.pop(runner_cls, None)
    if original_edit is None:
        return False
    runner_cls._recover_telegram_topic_thread_id = original_edit
    try:
        delattr(runner_cls, _TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER)
    except Exception:
        setattr(runner_cls, _TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER, False)
    return True
