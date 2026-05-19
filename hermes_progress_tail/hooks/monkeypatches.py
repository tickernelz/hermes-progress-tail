from __future__ import annotations

import logging
from contextlib import suppress
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)
_ORIGINALS: dict[type, dict[str, Any]] = {}
_ADAPTER_ORIGINALS: dict[type, dict[str, Any]] = {}
_DELEGATE_ORIGINALS: dict[int, tuple[Any, Any]] = {}
_TELEGRAM_ORIGINALS: dict[type, Any] = {}
_PROCESS_NOTIFICATION_ORIGINALS: dict[int, tuple[Any, Any]] = {}
_COMPRESSION_STATUS_ORIGINALS: dict[type, Any] = {}
_COMPRESSION_LIFECYCLE_ORIGINALS: dict[type, Any] = {}
_NOOP_MARKER = "_hermes_progress_tail_noop"
_PATCH_MARKER = "_hermes_progress_tail_patched"
_DELEGATE_PATCH_MARKER = "_hermes_progress_tail_delegate_patched"
_TELEGRAM_PATCH_MARKER = "_hermes_progress_tail_telegram_format_patched"
_PROCESS_NOTIFICATION_PATCH_MARKER = "_hermes_progress_tail_process_notification_patched"
_COMPRESSION_STATUS_PATCH_MARKER = "_hermes_progress_tail_compression_status_patched"
_COMPRESSION_LIFECYCLE_PATCH_MARKER = "_hermes_progress_tail_compression_lifecycle_patched"


def _noop_reasoning_callback(_text: str) -> None:
    return None


setattr(_noop_reasoning_callback, _NOOP_MARKER, True)


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


def install_monkeypatches(agent_cls: type | None = None) -> bool:
    agent_ok = install_agent_monkeypatches(agent_cls)
    adapter_ok = install_adapter_monkeypatches(agent_cls)
    delegate_ok = install_delegate_monkeypatches()
    telegram_ok = install_telegram_format_monkeypatch()
    process_ok = install_process_notification_monkeypatch()
    compression_ok = install_compression_status_monkeypatch(agent_cls)
    compression_lifecycle_ok = install_compression_lifecycle_monkeypatch(agent_cls)
    installed = any(
        (
            agent_ok,
            adapter_ok,
            delegate_ok,
            telegram_ok,
            process_ok,
            compression_ok,
            compression_lifecycle_ok,
        )
    )
    logger.info(
        "hermes-progress-tail monkeypatches installed: agent=%s adapter=%s delegate=%s "
        "telegram_format=%s process_notifications=%s compression_status=%s "
        "compression_lifecycle=%s any=%s",
        agent_ok,
        adapter_ok,
        delegate_ok,
        telegram_ok,
        process_ok,
        compression_ok,
        compression_lifecycle_ok,
        installed,
    )
    return installed


def install_adapter_monkeypatches(adapter_cls: type | None = None) -> bool:
    if adapter_cls is None:
        try:
            from gateway.platforms.base import BasePlatformAdapter as adapter_cls
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import BasePlatformAdapter: %s", exc)
            return False
    if getattr(adapter_cls, "_hermes_progress_tail_adapter_patched", False):
        return True
    set_handler = getattr(adapter_cls, "set_message_handler", None)
    handle_message = getattr(adapter_cls, "handle_message", None)
    if set_handler is None or handle_message is None:
        logger.debug("hermes-progress-tail adapter monkeypatch disabled: handler API missing")
        return False
    _ADAPTER_ORIGINALS[adapter_cls] = {
        "set_message_handler": set_handler,
        "handle_message": handle_message,
    }

    def patched_set_message_handler(self, handler):
        try:
            self._hermes_progress_tail_message_handler = handler
        except Exception:
            logger.debug("hermes-progress-tail could not remember adapter handler", exc_info=True)
        return set_handler(self, handler)

    async def patched_handle_message(self, event):
        if bool(getattr(event, "internal", False)):
            try:
                from ..runtime.plugin import register_context_from_adapter_event

                register_context_from_adapter_event(self, event)
            except Exception:
                logger.debug(
                    "hermes-progress-tail internal message context registration failed",
                    exc_info=True,
                )
        return await handle_message(self, event)

    adapter_cls.set_message_handler = patched_set_message_handler
    adapter_cls.handle_message = patched_handle_message
    adapter_cls._hermes_progress_tail_adapter_patched = True
    return True


def install_process_notification_monkeypatch(process_module: Any | None = None) -> bool:
    if process_module is None:
        try:
            from tools import process_registry as process_module
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import process_registry: %s", exc)
            return False
    if getattr(process_module, _PROCESS_NOTIFICATION_PATCH_MARKER, False):
        return True
    original = getattr(process_module, "format_process_notification", None)
    if original is None:
        logger.debug(
            "hermes-progress-tail process notification monkeypatch disabled: formatter missing"
        )
        return False
    _PROCESS_NOTIFICATION_ORIGINALS[id(process_module)] = (process_module, original)

    @wraps(original)
    def patched_format_process_notification(evt: dict):
        if _should_suppress_native_process_notification(evt):
            return None
        compact = _compact_process_failure_notification(evt)
        if compact is not None:
            return compact
        return original(evt)

    process_module.format_process_notification = patched_format_process_notification
    setattr(process_module, _PROCESS_NOTIFICATION_PATCH_MARKER, True)
    return True


def _should_suppress_native_process_notification(evt: Any) -> bool:
    if not isinstance(evt, dict):
        return False
    event_type = str(evt.get("type") or "")
    if event_type in {
        "watch_match",
        "watch_disabled",
        "watch_overflow_tripped",
        "watch_overflow_released",
    }:
        return True
    if event_type != "completion":
        return False
    try:
        exit_code = int(evt.get("exit_code") or 0)
    except (TypeError, ValueError):
        exit_code = 0
    return exit_code == 0


def _compact_process_failure_notification(evt: Any) -> str | None:
    if not isinstance(evt, dict) or evt.get("type") != "completion":
        return None
    try:
        exit_code = int(evt.get("exit_code") or 0)
    except (TypeError, ValueError):
        return None
    if exit_code == 0:
        return None
    process_id = str(evt.get("session_id") or "process")
    command = " ".join(str(evt.get("command") or "").split())
    output = _process_output_tail(str(evt.get("output") or ""))
    header = f"[Background process {process_id} failed with exit {exit_code}"
    if command:
        header += f": {command}"
    header += "]"
    if output:
        return f"{header}\nOutput tail:\n{output}"
    return header


def _process_output_tail(output: str, *, max_lines: int = 3, max_chars: int = 800) -> str:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    tail = "\n".join(lines[-max_lines:])
    if len(tail) > max_chars:
        return tail[-max_chars:]
    return tail


def install_compression_status_monkeypatch(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import AIAgent for compression: %s", exc)
            return False
    if getattr(agent_cls, _COMPRESSION_STATUS_PATCH_MARKER, False):
        return True
    emit_status = getattr(agent_cls, "_emit_status", None)
    if emit_status is None:
        logger.debug("hermes-progress-tail compression status monkeypatch disabled: API missing")
        return False
    _COMPRESSION_STATUS_ORIGINALS[agent_cls] = emit_status

    @wraps(emit_status)
    def patched_emit_status(self, text, *args, **kwargs):
        if _looks_like_compression_status(text):
            handled = False
            try:
                from ..runtime.plugin import on_compression_status_from_agent

                handled = on_compression_status_from_agent(self, str(text or ""))
            except Exception:
                logger.debug(
                    "hermes-progress-tail compression status capture failed", exc_info=True
                )
            if handled:
                return None
        return emit_status(self, text, *args, **kwargs)

    agent_cls._emit_status = patched_emit_status
    setattr(agent_cls, _COMPRESSION_STATUS_PATCH_MARKER, True)
    return True


def _looks_like_compression_status(text: Any) -> bool:
    value = str(text or "").lower()
    return (
        "compacting context" in value
        or "preflight compression" in value
        or "compacting" in value
        and "context" in value
    )


def install_compression_lifecycle_monkeypatch(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception as exc:
            logger.debug(
                "hermes-progress-tail could not import AIAgent for compression lifecycle: %s",
                exc,
            )
            return False
    if getattr(agent_cls, _COMPRESSION_LIFECYCLE_PATCH_MARKER, False):
        return True
    compress_context = getattr(agent_cls, "_compress_context", None)
    if compress_context is None:
        logger.debug("hermes-progress-tail compression lifecycle disabled: API missing")
        return False
    _COMPRESSION_LIFECYCLE_ORIGINALS[agent_cls] = compress_context

    @wraps(compress_context)
    def patched_compress_context(self, messages, system_message, *args, **kwargs):
        old_session_id = str(getattr(self, "session_id", "") or "")
        before_count = len(messages) if hasattr(messages, "__len__") else 0
        before_tokens = kwargs.get("approx_tokens")
        try:
            from ..runtime.plugin import on_compression_lifecycle_from_agent

            on_compression_lifecycle_from_agent(
                self,
                phase="started",
                old_session_id=old_session_id,
                before_count=before_count,
                before_tokens=before_tokens,
            )
        except Exception:
            logger.debug("hermes-progress-tail compression lifecycle start failed", exc_info=True)
        try:
            result = compress_context(self, messages, system_message, *args, **kwargs)
        except Exception as exc:
            try:
                from ..runtime.plugin import on_compression_lifecycle_from_agent

                on_compression_lifecycle_from_agent(
                    self,
                    phase="failed",
                    old_session_id=old_session_id,
                    before_count=before_count,
                    before_tokens=before_tokens,
                    error=str(exc),
                )
            except Exception:
                logger.debug(
                    "hermes-progress-tail compression lifecycle failure capture failed",
                    exc_info=True,
                )
            raise
        try:
            compressed = result[0] if isinstance(result, tuple) and result else result
            after_count = len(compressed) if hasattr(compressed, "__len__") else 0
            compressor = getattr(self, "context_compressor", None)
            status = compressor.get_status() if hasattr(compressor, "get_status") else {}
            after_tokens = getattr(compressor, "last_prompt_tokens", None)
            if isinstance(status, dict):
                after_tokens = status.get("last_prompt_tokens") or after_tokens
            from ..runtime.plugin import on_compression_lifecycle_from_agent

            on_compression_lifecycle_from_agent(
                self,
                phase="completed",
                old_session_id=old_session_id,
                new_session_id=str(getattr(self, "session_id", "") or ""),
                before_count=before_count,
                after_count=after_count,
                before_tokens=before_tokens,
                after_tokens=after_tokens,
                compression_count=getattr(compressor, "compression_count", 0),
            )
        except Exception:
            logger.debug(
                "hermes-progress-tail compression lifecycle completion failed", exc_info=True
            )
        return result

    agent_cls._compress_context = patched_compress_context
    setattr(agent_cls, _COMPRESSION_LIFECYCLE_PATCH_MARKER, True)
    return True


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
    async def patched_edit_message(self, chat_id, message_id, content, *, finalize=False):
        if finalize:
            return await original(self, chat_id, message_id, content, finalize=finalize)
        if not getattr(self, "_bot", None):
            return await original(self, chat_id, message_id, content, finalize=finalize)
        try:
            from gateway.platforms.base import SendResult, utf16_len
            from gateway.platforms.telegram import ParseMode
        except Exception:
            return await original(self, chat_id, message_id, content, finalize=finalize)
        try:
            max_len = int(getattr(self, "MAX_MESSAGE_LENGTH", 4096) or 4096)
            if utf16_len(str(content or "")) > max_len:
                return await original(self, chat_id, message_id, content, finalize=finalize)
        except Exception:
            return await original(self, chat_id, message_id, content, finalize=finalize)
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
            return await original(self, chat_id, message_id, content, finalize=finalize)

    telegram_adapter_cls.edit_message = patched_edit_message
    setattr(telegram_adapter_cls, _TELEGRAM_PATCH_MARKER, True)
    return True


def install_agent_monkeypatches(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import AIAgent: %s", exc)
            return False
    if getattr(agent_cls, _PATCH_MARKER, False):
        return True
    init = getattr(agent_cls, "__init__", None)
    fire_reasoning = getattr(agent_cls, "_fire_reasoning_delta", None)
    emit_interim = getattr(agent_cls, "_emit_interim_assistant_message", None)
    if init is None or fire_reasoning is None:
        logger.warning("hermes-progress-tail monkeypatch disabled: AIAgent callback API missing")
        return False
    _ORIGINALS[agent_cls] = {
        "__init__": init,
        "_fire_reasoning_delta": fire_reasoning,
        "_emit_interim_assistant_message": emit_interim,
    }

    @wraps(init)
    def patched_init(self, *args, **kwargs):
        init(self, *args, **kwargs)
        if getattr(self, "reasoning_callback", None) is None:
            try:
                self.reasoning_callback = _noop_reasoning_callback
            except Exception:
                logger.debug(
                    "hermes-progress-tail could not set noop reasoning callback", exc_info=True
                )
        try:
            self.stream_delta_callback = _wrap_stream_delta_callback(
                self, getattr(self, "stream_delta_callback", None)
            )
        except Exception:
            logger.debug("hermes-progress-tail could not wrap stream delta callback", exc_info=True)

    @wraps(fire_reasoning)
    def patched_fire_reasoning_delta(self, *args, **kwargs):
        text = args[0] if args else None
        if text is None:
            for key in ("text", "delta", "reasoning", "reasoning_text"):
                if kwargs.get(key):
                    text = kwargs[key]
                    break
        if text:
            try:
                from ..runtime.plugin import on_reasoning_delta_from_agent

                on_reasoning_delta_from_agent(self, str(text))
            except Exception:
                logger.debug("hermes-progress-tail reasoning capture failed", exc_info=True)
        return fire_reasoning(self, *args, **kwargs)

    def patched_emit_interim_assistant_message(self, assistant_msg):
        if emit_interim is None:
            return None
        handled = False
        visible = _assistant_visible_text(self, assistant_msg)
        already_streamed = _assistant_already_streamed(self, visible, assistant_msg)
        if visible:
            try:
                from ..runtime.plugin import on_assistant_progress_from_agent

                handled = on_assistant_progress_from_agent(
                    self, visible, already_streamed=already_streamed
                )
            except Exception:
                logger.debug(
                    "hermes-progress-tail assistant progress capture failed", exc_info=True
                )
        if handled:
            return None
        return emit_interim(self, assistant_msg)

    agent_cls.__init__ = patched_init
    agent_cls._fire_reasoning_delta = patched_fire_reasoning_delta
    if emit_interim is not None:
        agent_cls._emit_interim_assistant_message = wraps(emit_interim)(
            patched_emit_interim_assistant_message
        )
    setattr(agent_cls, _PATCH_MARKER, True)
    return True


def _assistant_visible_text(agent: Any, assistant_msg: Any) -> str:
    if not isinstance(assistant_msg, dict):
        return ""
    content = str(assistant_msg.get("content") or "")
    if not content:
        return ""
    stripper = getattr(agent, "_strip_think_blocks", None)
    if callable(stripper):
        with suppress(Exception):
            content = stripper(content)
    return content.strip()


def _assistant_already_streamed(agent: Any, visible: str, assistant_msg: Any) -> bool:
    checker = getattr(agent, "_interim_content_was_streamed", None)
    if callable(checker) and visible:
        with suppress(Exception):
            return bool(checker(visible))
    if isinstance(assistant_msg, dict):
        return bool(assistant_msg.get("already_streamed"))
    return False


def _wrap_stream_delta_callback(agent: Any, callback: Any) -> Any:
    if callback is None or getattr(callback, "_hermes_progress_tail_inline_think_wrapped", False):
        return callback

    @wraps(callback)
    def wrapped_stream_delta(text, *args, **kwargs):
        raw_text = str(text or "")
        fail_open_text = _inline_reasoning_pending(agent) + raw_text
        if not _agent_reasoning_enabled(agent):
            _reset_inline_reasoning_state(agent)
            return callback(fail_open_text, *args, **kwargs)
        captured, visible = _capture_inline_reasoning(agent, raw_text)
        if captured:
            try:
                from ..runtime.plugin import on_reasoning_delta_from_agent

                on_reasoning_delta_from_agent(agent, captured, source="inline_think")
            except Exception:
                logger.debug("hermes-progress-tail inline think capture failed", exc_info=True)
                _reset_inline_reasoning_state(agent)
                visible = fail_open_text
        return callback(visible, *args, **kwargs)

    wrapped_stream_delta._hermes_progress_tail_inline_think_wrapped = True
    return wrapped_stream_delta


def _agent_reasoning_enabled(agent: Any) -> bool:
    try:
        from ..runtime.plugin import _get_renderer

        renderer = _get_renderer()
        from ..runtime.plugin import _agent_session_id, _agent_session_key

        session_id = _agent_session_id(agent)
        session_key = _agent_session_key(agent)
        ctx = renderer.find_context(session_id, session_key)
        return bool(
            ctx is not None and ctx.reasoning_enabled and renderer.settings.reasoning.enabled
        )
    except Exception:
        logger.debug("hermes-progress-tail reasoning availability check failed", exc_info=True)
        return False


def _inline_reasoning_pending(agent: Any) -> str:
    state = getattr(agent, "_hermes_progress_tail_inline_think_state", None)
    if not isinstance(state, dict):
        return ""
    return str(state.get("pending") or "")


def _reset_inline_reasoning_state(agent: Any) -> None:
    with suppress(Exception):
        agent._hermes_progress_tail_inline_think_state = {
            "inside": False,
            "pending": "",
            "captured_chars": 0,
        }


def _capture_inline_reasoning(agent: Any, text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    state = getattr(agent, "_hermes_progress_tail_inline_think_state", None)
    if not isinstance(state, dict):
        state = {"inside": False, "pending": "", "captured_chars": 0}
        agent._hermes_progress_tail_inline_think_state = state
    combined = str(state.get("pending") or "") + text
    captured, visible, inside, pending = _split_inline_reasoning(
        combined, bool(state.get("inside"))
    )
    captured_chars = int(state.get("captured_chars") or 0) + len(captured)
    if inside and captured_chars > 8000:
        _reset_inline_reasoning_state(agent)
        return "", text
    state["inside"] = inside
    state["pending"] = pending
    state["captured_chars"] = captured_chars if inside else 0
    return captured, visible


def _split_inline_reasoning(text: str, inside: bool = False) -> tuple[str, str, bool, str]:
    import re

    tag_names = "thinking|reasoning|thought|analysis|REASONING_SCRATCHPAD|think"
    tag_re = re.compile(rf"</?(?:{tag_names})\b[^>]*>", re.IGNORECASE)
    captured: list[str] = []
    visible: list[str] = []
    pos = 0
    for match in tag_re.finditer(text):
        segment = text[pos : match.start()]
        if inside:
            captured.append(segment)
        else:
            visible.append(segment)
        tag = match.group(0)
        inside = not tag.startswith("</")
        pos = match.end()
    tail = text[pos:]
    incomplete = ""
    last_lt = tail.rfind("<")
    if last_lt != -1 and _looks_like_partial_reasoning_tag(tail[last_lt:]):
        incomplete = tail[last_lt:]
        tail = tail[:last_lt]
    if inside:
        captured.append(tail)
    else:
        visible.append(tail)
    return "".join(captured), "".join(visible), inside, incomplete


def _looks_like_partial_reasoning_tag(value: str) -> bool:
    lowered = value.lower().lstrip("<").lstrip("/")
    return any(
        tag.startswith(lowered)
        for tag in ("think", "thinking", "reasoning", "thought", "analysis", "reasoning_scratchpad")
    )


def install_delegate_monkeypatches(delegate_module: Any | None = None) -> bool:
    if delegate_module is None:
        try:
            from tools import delegate_tool as delegate_module
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import delegate_tool: %s", exc)
            return False
    if getattr(delegate_module, _DELEGATE_PATCH_MARKER, False):
        return True
    original = getattr(delegate_module, "_build_child_progress_callback", None)
    if original is None:
        logger.warning(
            "hermes-progress-tail delegate monkeypatch disabled: delegate callback builder missing"
        )
        return False
    _DELEGATE_ORIGINALS[id(delegate_module)] = (delegate_module, original)

    @wraps(original)
    def patched_build_child_progress_callback(*args, **kwargs):
        original_cb = original(*args, **kwargs)
        task_index, goal, parent_agent = _extract_delegate_builder_args(args, kwargs)

        def progress_tail_delegate_callback(
            event_type,
            tool_name: str | None = None,
            preview: str | None = None,
            cb_args=None,
            **event_kwargs,
        ):
            captured_args = dict(cb_args) if isinstance(cb_args, dict) else cb_args
            if original_cb is not None:
                try:
                    original_cb(event_type, tool_name, preview, cb_args, **event_kwargs)
                except Exception:
                    logger.debug(
                        "hermes-progress-tail original delegate callback failed", exc_info=True
                    )
            try:
                from ..runtime.plugin import on_delegate_progress_from_agent

                if "task_index" not in event_kwargs:
                    event_kwargs["task_index"] = task_index
                if "goal" not in event_kwargs and goal:
                    event_kwargs["goal"] = goal
                on_delegate_progress_from_agent(
                    parent_agent,
                    str(event_type or ""),
                    tool_name,
                    preview,
                    captured_args,
                    **event_kwargs,
                )
            except Exception:
                logger.debug("hermes-progress-tail delegate capture failed", exc_info=True)

        if original_cb is not None and hasattr(original_cb, "_flush"):
            progress_tail_delegate_callback._flush = original_cb._flush
        return progress_tail_delegate_callback

    delegate_module._build_child_progress_callback = patched_build_child_progress_callback
    setattr(delegate_module, _DELEGATE_PATCH_MARKER, True)
    return True


def _extract_delegate_builder_args(args, kwargs) -> tuple[int, str, Any]:
    task_index = kwargs.get("task_index")
    goal = kwargs.get("goal")
    parent_agent = kwargs.get("parent_agent")
    if task_index is None and len(args) > 0:
        task_index = args[0]
    if goal is None and len(args) > 1:
        goal = args[1]
    if parent_agent is None and len(args) > 2:
        parent_agent = args[2]
    try:
        task_index = int(task_index)
    except (TypeError, ValueError):
        task_index = 0
    return task_index, str(goal or ""), parent_agent


def uninstall_monkeypatches(agent_cls: type | None = None) -> bool:
    agent_ok = uninstall_agent_monkeypatches(agent_cls)
    adapter_ok = uninstall_adapter_monkeypatches(agent_cls)
    delegate_ok = uninstall_delegate_monkeypatches()
    telegram_ok = uninstall_telegram_format_monkeypatch()
    process_ok = uninstall_process_notification_monkeypatch()
    compression_ok = uninstall_compression_status_monkeypatch(agent_cls)
    compression_lifecycle_ok = uninstall_compression_lifecycle_monkeypatch(agent_cls)
    return any(
        (
            agent_ok,
            adapter_ok,
            delegate_ok,
            telegram_ok,
            process_ok,
            compression_ok,
            compression_lifecycle_ok,
        )
    )


def uninstall_adapter_monkeypatches(adapter_cls: type | None = None) -> bool:
    if adapter_cls is None:
        try:
            from gateway.platforms.base import BasePlatformAdapter as adapter_cls
        except Exception:
            return False
    originals = _ADAPTER_ORIGINALS.pop(adapter_cls, None)
    if not originals:
        return False
    adapter_cls.set_message_handler = originals["set_message_handler"]
    adapter_cls.handle_message = originals["handle_message"]
    try:
        delattr(adapter_cls, "_hermes_progress_tail_adapter_patched")
    except Exception:
        adapter_cls._hermes_progress_tail_adapter_patched = False
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


def uninstall_process_notification_monkeypatch(process_module: Any | None = None) -> bool:
    if process_module is None:
        try:
            from tools import process_registry as process_module
        except Exception:
            return False
    entry = _PROCESS_NOTIFICATION_ORIGINALS.pop(id(process_module), None)
    if entry is None:
        return False
    _, original = entry
    process_module.format_process_notification = original
    try:
        delattr(process_module, _PROCESS_NOTIFICATION_PATCH_MARKER)
    except Exception:
        setattr(process_module, _PROCESS_NOTIFICATION_PATCH_MARKER, False)
    return True


def uninstall_compression_status_monkeypatch(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception:
            return False
    original = _COMPRESSION_STATUS_ORIGINALS.pop(agent_cls, None)
    if original is None:
        return False
    agent_cls._emit_status = original
    try:
        delattr(agent_cls, _COMPRESSION_STATUS_PATCH_MARKER)
    except Exception:
        setattr(agent_cls, _COMPRESSION_STATUS_PATCH_MARKER, False)
    return True


def uninstall_compression_lifecycle_monkeypatch(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception:
            return False
    original = _COMPRESSION_LIFECYCLE_ORIGINALS.pop(agent_cls, None)
    if original is None:
        return False
    agent_cls._compress_context = original
    try:
        delattr(agent_cls, _COMPRESSION_LIFECYCLE_PATCH_MARKER)
    except Exception:
        setattr(agent_cls, _COMPRESSION_LIFECYCLE_PATCH_MARKER, False)
    return True


def uninstall_agent_monkeypatches(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception:
            return False
    originals = _ORIGINALS.pop(agent_cls, None)
    if not originals:
        return False
    agent_cls.__init__ = originals["__init__"]
    agent_cls._fire_reasoning_delta = originals["_fire_reasoning_delta"]
    emit_interim = originals.get("_emit_interim_assistant_message")
    if emit_interim is not None:
        agent_cls._emit_interim_assistant_message = emit_interim
    try:
        delattr(agent_cls, _PATCH_MARKER)
    except Exception:
        setattr(agent_cls, _PATCH_MARKER, False)
    return True


def uninstall_delegate_monkeypatches(delegate_module: Any | None = None) -> bool:
    if delegate_module is None:
        try:
            from tools import delegate_tool as delegate_module
        except Exception:
            return False
    entry = _DELEGATE_ORIGINALS.pop(id(delegate_module), None)
    if entry is None:
        return False
    _, original = entry
    delegate_module._build_child_progress_callback = original
    try:
        delattr(delegate_module, _DELEGATE_PATCH_MARKER)
    except Exception:
        setattr(delegate_module, _DELEGATE_PATCH_MARKER, False)
    return True
