from __future__ import annotations

import logging
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)
_TELEGRAM_ORIGINALS: dict[type, Any] = {}
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
    original = getattr(runner_cls, "_recover_telegram_topic_thread_id", None)
    if original is None:
        logger.debug(
            "hermes-progress-tail Telegram topic recovery monkeypatch disabled: API missing"
        )
        return False
    _TELEGRAM_TOPIC_RECOVERY_ORIGINALS[runner_cls] = original

    @wraps(original)
    def patched_recover_telegram_topic_thread_id(self, source):
        if _should_preserve_telegram_topic_thread(self, source):
            logger.debug(
                "hermes-progress-tail preserving concrete Telegram topic thread_id=%s",
                getattr(source, "thread_id", None),
            )
            return None
        return original(self, source)

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
    if getattr(telegram_adapter_cls, _TELEGRAM_PATCH_MARKER, False):
        return True
    original = getattr(telegram_adapter_cls, "edit_message", None)
    if original is None:
        logger.warning(
            "hermes-progress-tail Telegram formatting monkeypatch disabled: edit_message missing"
        )
        return False
    _TELEGRAM_ORIGINALS[telegram_adapter_cls] = original

    @wraps(original)
    async def patched_edit_message(
        self, chat_id, message_id, content, *, finalize=False, metadata=None
    ):
        if finalize:
            return await original(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )
        if not getattr(self, "_bot", None):
            return await original(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )
        try:
            from gateway.platforms.base import SendResult, utf16_len
            from gateway.platforms.telegram import ParseMode
        except Exception:
            return await original(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )
        try:
            max_len = int(getattr(self, "MAX_MESSAGE_LENGTH", 4096) or 4096)
            if utf16_len(str(content or "")) > max_len:
                return await original(
                    self, chat_id, message_id, content, finalize=finalize, metadata=metadata
                )
        except Exception:
            return await original(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )
        try:
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
            return await original(
                self, chat_id, message_id, content, finalize=finalize, metadata=metadata
            )

    telegram_adapter_cls.edit_message = patched_edit_message
    setattr(telegram_adapter_cls, _TELEGRAM_PATCH_MARKER, True)
    return True


def uninstall_telegram_format_monkeypatch(telegram_adapter_cls: type | None = None) -> bool:
    if telegram_adapter_cls is None:
        try:
            from gateway.platforms.telegram import TelegramAdapter as telegram_adapter_cls
        except Exception:
            return False
    original = _TELEGRAM_ORIGINALS.pop(telegram_adapter_cls, None)
    if original is None:
        return False
    telegram_adapter_cls.edit_message = original
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
    original = _TELEGRAM_TOPIC_RECOVERY_ORIGINALS.pop(runner_cls, None)
    if original is None:
        return False
    runner_cls._recover_telegram_topic_thread_id = original
    try:
        delattr(runner_cls, _TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER)
    except Exception:
        setattr(runner_cls, _TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER, False)
    return True
