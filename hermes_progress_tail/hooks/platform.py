from __future__ import annotations

import logging
from contextlib import suppress
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)
_ADAPTER_ORIGINALS: dict[type, dict[str, Any]] = {}
_GATEWAY_INTERRUPT_ORIGINALS: dict[type, Any] = {}
_PROCESS_NOTIFICATION_ORIGINALS: dict[int, tuple[Any, Any]] = {}
_GATEWAY_INTERRUPT_PATCH_MARKER = "_hermes_progress_tail_gateway_interrupt_patched"
_PROCESS_NOTIFICATION_PATCH_MARKER = "_hermes_progress_tail_process_notification_patched"


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
            gateway = getattr(handler, "__self__", None)
            if gateway is not None and gateway is not self:
                self._hermes_progress_tail_gateway = gateway
            else:
                with suppress(AttributeError):
                    delattr(self, "_hermes_progress_tail_gateway")
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


def install_gateway_interrupt_monkeypatch(gateway_runner_cls: type | None = None) -> bool:
    runner_cls = gateway_runner_cls
    if runner_cls is None:
        try:
            from gateway.run import GatewayRunner

            runner_cls = GatewayRunner
        except Exception as exc:
            logger.debug(
                "hermes-progress-tail could not import GatewayRunner for interrupt lifecycle: %s",
                exc,
            )
            return False
    if getattr(runner_cls, _GATEWAY_INTERRUPT_PATCH_MARKER, False):
        return True
    original = getattr(runner_cls, "_interrupt_and_clear_session", None)
    if original is None:
        logger.debug("hermes-progress-tail gateway interrupt monkeypatch disabled: API missing")
        return False
    _GATEWAY_INTERRUPT_ORIGINALS[runner_cls] = original

    @wraps(original)
    async def patched_interrupt_and_clear_session(self, session_key, source, *args, **kwargs):
        interrupt_reason, invalidation_reason = _extract_interrupt_reasons(args, kwargs)
        result = await original(self, session_key, source, *args, **kwargs)
        if _is_stop_interrupt(interrupt_reason, invalidation_reason):
            try:
                from ..runtime.plugin import on_gateway_stop_from_runner

                on_gateway_stop_from_runner(self, session_key=str(session_key or ""), source=source)
            except Exception:
                logger.debug(
                    "hermes-progress-tail gateway stop lifecycle capture failed",
                    exc_info=True,
                )
        return result

    runner_cls._interrupt_and_clear_session = patched_interrupt_and_clear_session
    setattr(runner_cls, _GATEWAY_INTERRUPT_PATCH_MARKER, True)
    return True


def _extract_interrupt_reasons(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, str]:
    interrupt_reason = kwargs.get("interrupt_reason")
    invalidation_reason = kwargs.get("invalidation_reason")
    if interrupt_reason is None and len(args) > 0:
        interrupt_reason = args[0]
    if invalidation_reason is None and len(args) > 1:
        invalidation_reason = args[1]
    return str(interrupt_reason or ""), str(invalidation_reason or "")


def _is_stop_interrupt(interrupt_reason: str, invalidation_reason: str) -> bool:
    reason = str(interrupt_reason or "").strip().lower()
    invalidation = str(invalidation_reason or "").strip().lower()
    return reason == "stop requested" or invalidation.startswith("stop_command")


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


def uninstall_gateway_interrupt_monkeypatch(gateway_runner_cls: type | None = None) -> bool:
    runner_cls = gateway_runner_cls
    if runner_cls is None:
        try:
            from gateway.run import GatewayRunner

            runner_cls = GatewayRunner
        except Exception:
            return False
    original = _GATEWAY_INTERRUPT_ORIGINALS.pop(runner_cls, None)
    if original is None:
        return False
    runner_cls._interrupt_and_clear_session = original
    try:
        delattr(runner_cls, _GATEWAY_INTERRUPT_PATCH_MARKER)
    except Exception:
        setattr(runner_cls, _GATEWAY_INTERRUPT_PATCH_MARKER, False)
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
