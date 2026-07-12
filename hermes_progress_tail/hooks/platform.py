from __future__ import annotations

import logging
from contextlib import suppress
from functools import wraps
from typing import Any

from .contracts import HookCallbacks, current_hook_callbacks
from .install_report import PatchStatus
from .status_helpers import structured_patch_status

logger = logging.getLogger(__name__)
_ADAPTER_ORIGINALS: dict[type, dict[str, Any]] = {}
_GATEWAY_INTERRUPT_ORIGINALS: dict[type, Any] = {}
_GATEWAY_DISPLAY_ORIGINALS: dict[int, tuple[Any, Any]] = {}
_PROCESS_NOTIFICATION_ORIGINALS: dict[int, tuple[Any, Any]] = {}
_GATEWAY_INTERRUPT_PATCH_MARKER = "_hermes_progress_tail_gateway_interrupt_patched"
_GATEWAY_DISPLAY_PATCH_MARKER = "_hermes_progress_tail_gateway_display_patched"
_PROCESS_NOTIFICATION_PATCH_MARKER = "_hermes_progress_tail_process_notification_patched"
_NATIVE_GATEWAY_FALSE_SETTINGS = {
    "show_reasoning",
    "streaming",
    "interim_assistant_messages",
    "long_running_notifications",
    "thinking_progress",
}
_NATIVE_GATEWAY_OFF_SETTINGS = {"tool_progress"}


def _prepare_concrete_adapter(adapter: Any, platform: Any) -> None:
    platform_value = getattr(platform, "value", platform)
    if str(platform_value or "").strip().lower() != "telegram":
        return
    try:
        from .telegram import install_telegram_format_monkeypatch

        install_telegram_format_monkeypatch(type(adapter))
    except Exception:
        logger.debug(
            "hermes-progress-tail could not prepare concrete Telegram adapter",
            exc_info=True,
        )


def _mutate_adapter_monkeypatches(
    adapter_cls: type | None = None, *, callbacks: HookCallbacks | None = None
) -> bool:
    callbacks = callbacks if callbacks is not None else current_hook_callbacks()
    if adapter_cls is None:
        try:
            from gateway.platforms.base import BasePlatformAdapter as adapter_cls
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import BasePlatformAdapter: %s", exc)
            return False
    if getattr(adapter_cls, "_hermes_progress_tail_adapter_patched", False):
        originals = _ADAPTER_ORIGINALS.get(adapter_cls, {})
        return bool(
            callable(originals.get("set_message_handler"))
            and callable(originals.get("handle_message"))
            and callable(getattr(adapter_cls, "set_message_handler", None))
            and callable(getattr(adapter_cls, "handle_message", None))
        )
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
        source = getattr(event, "source", None)
        _prepare_concrete_adapter(self, getattr(source, "platform", ""))
        if bool(getattr(event, "internal", False)):
            try:
                callbacks.register_adapter_context(self, event)
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


def _mutate_gateway_interrupt_monkeypatch(
    gateway_runner_cls: type | None = None, *, callbacks: HookCallbacks | None = None
) -> bool:
    callbacks = callbacks if callbacks is not None else current_hook_callbacks()
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
        return bool(callable(_GATEWAY_INTERRUPT_ORIGINALS.get(runner_cls)))
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
                callbacks.on_gateway_stop(self, session_key=str(session_key or ""), source=source)
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


def _mutate_gateway_display_suppression_monkeypatch(display_module: Any | None = None) -> bool:
    if display_module is None:
        try:
            from gateway import display_config as display_module
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import gateway.display_config: %s", exc)
            return False
    if getattr(display_module, _GATEWAY_DISPLAY_PATCH_MARKER, False):
        entry = _GATEWAY_DISPLAY_ORIGINALS.get(id(display_module))
        return bool(entry and entry[0] is display_module and callable(entry[1]))
    original = getattr(display_module, "resolve_display_setting", None)
    if original is None:
        logger.debug("hermes-progress-tail gateway display monkeypatch disabled: resolver missing")
        return False
    _GATEWAY_DISPLAY_ORIGINALS[id(display_module)] = (display_module, original)

    @wraps(original)
    def patched_resolve_display_setting(*args, **kwargs):
        value = original(*args, **kwargs)
        config, platform_key, setting = _extract_display_resolver_args(args, kwargs)
        if _should_suppress_native_gateway_display(config, platform_key, setting):
            if setting in _NATIVE_GATEWAY_OFF_SETTINGS:
                return "off"
            return False
        return value

    display_module.resolve_display_setting = patched_resolve_display_setting
    setattr(display_module, _GATEWAY_DISPLAY_PATCH_MARKER, True)
    return True


def _extract_display_resolver_args(
    args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[Any, str, str]:
    config = kwargs.get("user_config") or kwargs.get("config")
    platform_key = kwargs.get("platform_key") or kwargs.get("platform")
    setting = kwargs.get("setting") or kwargs.get("key")
    if config is None and len(args) > 0:
        config = args[0]
    if platform_key is None and len(args) > 1:
        platform_key = args[1]
    if setting is None and len(args) > 2:
        setting = args[2]
    return config, str(platform_key or "").strip().lower(), str(setting or "")


def _should_suppress_native_gateway_display(config: Any, platform_key: str, setting: str) -> bool:
    if setting not in _NATIVE_GATEWAY_FALSE_SETTINGS | _NATIVE_GATEWAY_OFF_SETTINGS:
        return False
    if not _native_gateway_suppression_enabled(config):
        return False
    return _progress_tail_owns_platform(config, platform_key)


def _native_gateway_suppression_enabled(config: Any) -> bool:
    if not isinstance(config, dict):
        return False
    progress_tail = config.get("progress_tail")
    if isinstance(progress_tail, dict) and progress_tail.get("enabled") is False:
        return False
    native = progress_tail.get("native_gateway") if isinstance(progress_tail, dict) else None
    if isinstance(native, dict):
        return native.get("suppress") is not False
    return True


def _progress_tail_owns_platform(config: Any, platform_key: str) -> bool:
    if not isinstance(config, dict):
        return False
    platform = str(platform_key or "").strip().lower()
    if not platform:
        return False
    try:
        from ..settings.config import load_settings, resolve_platform_settings

        settings = load_settings(config)
        platform_settings = resolve_platform_settings(settings, platform)
        return bool(platform_settings.enabled and platform_settings.strategy != "off")
    except Exception:
        logger.debug("hermes-progress-tail platform ownership check failed", exc_info=True)
        progress_tail = config.get("progress_tail")
        platforms = progress_tail.get("platforms") if isinstance(progress_tail, dict) else None
        platform_raw = platforms.get(platform) if isinstance(platforms, dict) else None
        if isinstance(platform_raw, dict):
            if platform_raw.get("enabled") is False:
                return False
            if str(platform_raw.get("strategy") or "").strip().lower() == "off":
                return False
        return True


def _process_notification_config(config: Any | None) -> dict[str, Any]:
    if isinstance(config, dict):
        return config
    try:
        from ..runtime.config_runtime import _load_runtime_config

        loaded = _load_runtime_config()
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _process_notification_is_owned_gateway_event(evt: Any, config: dict[str, Any]) -> bool:
    if not isinstance(evt, dict):
        return False
    platform = str(evt.get("platform") or evt.get("watcher_platform") or "").strip().lower()
    has_gateway_route = bool(
        platform
        or evt.get("chat_id")
        or evt.get("user_id")
        or evt.get("thread_id")
        or evt.get("message_id")
    )
    if not has_gateway_route:
        return False
    if not platform:
        return False
    return _native_gateway_suppression_enabled(config) and _progress_tail_owns_platform(
        config, platform
    )


def _native_background_suppression_enabled(config: dict[str, Any], key: str) -> bool:
    progress_tail = config.get("progress_tail")
    background = progress_tail.get("background_jobs") if isinstance(progress_tail, dict) else None
    if not isinstance(background, dict):
        return True
    return background.get(key) is not False


def _is_successful_completion(evt: dict[str, Any]) -> bool:
    try:
        exit_code = int(evt.get("exit_code") or 0)
    except (TypeError, ValueError):
        exit_code = 0
    return exit_code == 0


def _is_watch_notification_type(event_type: str) -> bool:
    return event_type in {
        "watch_match",
        "watch_disabled",
        "watch_overflow_tripped",
        "watch_overflow_released",
    }


def _legacy_global_suppression_warnings(config: dict[str, Any]) -> list[str]:
    if not _native_gateway_suppression_enabled(config):
        return []
    warnings: list[str] = []
    display = config.get("display") if isinstance(config.get("display"), dict) else {}
    streaming = config.get("streaming") if isinstance(config.get("streaming"), dict) else {}
    agent = config.get("agent") if isinstance(config.get("agent"), dict) else {}
    if display.get("tool_progress") == "off":
        warnings.append(
            "warning: display.tool_progress is globally off; progress-tail suppresses native gateway progress plugin-side now"
        )
    if display.get("streaming") is False:
        warnings.append(
            "warning: display.streaming is globally false; restore it if you want native streaming outside progress-tail-owned gateway updates"
        )
    if streaming.get("enabled") is False:
        warnings.append(
            "warning: streaming.enabled is globally false; restore it if you want native streaming outside progress-tail-owned gateway updates"
        )
    if display.get("show_reasoning") is False:
        warnings.append(
            "warning: display.show_reasoning is globally false; progress-tail suppresses native gateway reasoning plugin-side now"
        )
    if display.get("interim_assistant_messages") is False:
        warnings.append(
            "warning: display.interim_assistant_messages is globally false; progress-tail suppresses native gateway interim assistant messages plugin-side now"
        )
    if agent.get("gateway_notify_interval") == 0:
        warnings.append(
            "warning: agent.gateway_notify_interval is globally disabled; progress-tail suppresses native gateway long-running notices plugin-side now"
        )
    return warnings


def _mutate_process_notification_monkeypatch(process_module: Any | None = None) -> bool:
    if process_module is None:
        try:
            from tools import process_registry as process_module
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import process_registry: %s", exc)
            return False
    if getattr(process_module, _PROCESS_NOTIFICATION_PATCH_MARKER, False):
        entry = _PROCESS_NOTIFICATION_ORIGINALS.get(id(process_module))
        return bool(entry and entry[0] is process_module and callable(entry[1]))
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


def _should_suppress_native_process_notification(
    evt: Any, *, config: dict[str, Any] | None = None
) -> bool:
    runtime_config = _process_notification_config(config)
    if not isinstance(evt, dict):
        return False
    if not _process_notification_is_owned_gateway_event(evt, runtime_config):
        return False
    event_type = str(evt.get("type") or "")
    if _is_watch_notification_type(event_type):
        return _native_background_suppression_enabled(
            runtime_config, "suppress_watch_notifications"
        )
    if event_type != "completion":
        return False
    if not _is_successful_completion(evt):
        return False
    return _native_background_suppression_enabled(runtime_config, "suppress_native_notify")


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


def uninstall_gateway_display_suppression_monkeypatch(display_module: Any | None = None) -> bool:
    if display_module is None:
        try:
            from gateway import display_config as display_module
        except Exception:
            return False
    entry = _GATEWAY_DISPLAY_ORIGINALS.pop(id(display_module), None)
    if entry is None:
        return False
    _, original = entry
    display_module.resolve_display_setting = original
    try:
        delattr(display_module, _GATEWAY_DISPLAY_PATCH_MARKER)
    except Exception:
        setattr(display_module, _GATEWAY_DISPLAY_PATCH_MARKER, False)
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


def _adapter_patch_status(
    adapter_cls: type | None = None, *, callbacks: HookCallbacks | None = None
) -> PatchStatus:
    def resolver():
        from gateway.platforms.base import BasePlatformAdapter

        return BasePlatformAdapter

    return structured_patch_status(
        name="adapter_context",
        target_label="gateway.platforms.base.BasePlatformAdapter.set_message_handler+handle_message",
        target=adapter_cls,
        resolver=resolver,
        members=("set_message_handler", "handle_message"),
        mutate=lambda target: _mutate_adapter_monkeypatches(target, callbacks=callbacks),
    )


def install_adapter_monkeypatches(
    adapter_cls: type | None = None, *, callbacks: HookCallbacks | None = None
) -> bool:
    return bool(_adapter_patch_status(adapter_cls, callbacks=callbacks).installed)


def _gateway_interrupt_patch_status(
    gateway_runner_cls: type | None = None, *, callbacks: HookCallbacks | None = None
) -> PatchStatus:
    def resolver():
        from gateway.run import GatewayRunner

        return GatewayRunner

    return structured_patch_status(
        name="gateway_interrupt",
        target_label="gateway.run.GatewayRunner._interrupt_and_clear_session",
        target=gateway_runner_cls,
        resolver=resolver,
        members=("_interrupt_and_clear_session",),
        mutate=lambda target: _mutate_gateway_interrupt_monkeypatch(target, callbacks=callbacks),
    )


def install_gateway_interrupt_monkeypatch(
    gateway_runner_cls: type | None = None, *, callbacks: HookCallbacks | None = None
) -> bool:
    return bool(_gateway_interrupt_patch_status(gateway_runner_cls, callbacks=callbacks).installed)


def _gateway_display_patch_status(display_module: Any | None = None) -> PatchStatus:
    def resolver():
        from gateway import display_config

        return display_config

    return structured_patch_status(
        name="gateway_display",
        target_label="gateway.display_config.resolve_display_setting",
        target=display_module,
        resolver=resolver,
        members=("resolve_display_setting",),
        mutate=lambda target: _mutate_gateway_display_suppression_monkeypatch(target),
    )


def install_gateway_display_suppression_monkeypatch(display_module: Any | None = None) -> bool:
    return bool(_gateway_display_patch_status(display_module).installed)


def _process_notification_patch_status(process_module: Any | None = None) -> PatchStatus:
    def resolver():
        from tools import process_registry

        return process_registry

    return structured_patch_status(
        name="process_notifications",
        target_label="tools.process_registry.format_process_notification",
        target=process_module,
        resolver=resolver,
        members=("format_process_notification",),
        mutate=lambda target: _mutate_process_notification_monkeypatch(target),
    )


def install_process_notification_monkeypatch(process_module: Any | None = None) -> bool:
    return bool(_process_notification_patch_status(process_module).installed)
