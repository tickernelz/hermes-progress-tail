from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .agent import (
    _agent_patch_status,
    _agent_reasoning_enabled,
    _assistant_already_streamed,
    _assistant_visible_text,
    _capture_inline_reasoning,
    _inline_reasoning_pending,
    _looks_like_partial_reasoning_tag,
    _noop_reasoning_callback,
    _pop_tool_agent_context,
    _push_tool_agent_context,
    _reset_inline_reasoning_state,
    _split_inline_reasoning,
    _tool_context_matches,
    _wrap_stream_delta_callback,
    current_tool_agent_context,
    install_agent_monkeypatches,
    uninstall_agent_monkeypatches,
)
from .command_menus import (
    _command_menu_patch_status,
    _pin_pairs,
    command_menu_monkeypatch_active,
    install_command_menu_monkeypatch,
    uninstall_command_menu_monkeypatch,
)
from .compression import (
    _compression_lifecycle_patch_status,
    _compression_status_patch_status,
    _looks_like_compression_status,
    install_compression_lifecycle_monkeypatch,
    install_compression_status_monkeypatch,
    uninstall_compression_lifecycle_monkeypatch,
    uninstall_compression_status_monkeypatch,
)
from .contracts import HookCallbacks, configure_hook_callbacks, current_hook_callbacks
from .delegate import (
    _delegate_patch_status,
    _extract_delegate_builder_args,
    install_delegate_monkeypatches,
    uninstall_delegate_monkeypatches,
)
from .install_report import (
    PatchFailureCategory,
    PatchInstallReport,
    PatchStatus,
    safe_patch_reason,
)
from .platform import (
    _adapter_patch_status,
    _compact_process_failure_notification,
    _extract_interrupt_reasons,
    _gateway_display_patch_status,
    _gateway_interrupt_patch_status,
    _is_stop_interrupt,
    _legacy_global_suppression_warnings,
    _native_gateway_suppression_enabled,
    _process_notification_patch_status,
    _process_output_tail,
    _progress_tail_owns_platform,
    _should_suppress_native_gateway_display,
    _should_suppress_native_process_notification,
    install_adapter_monkeypatches,
    install_gateway_display_suppression_monkeypatch,
    install_gateway_interrupt_monkeypatch,
    install_process_notification_monkeypatch,
    uninstall_adapter_monkeypatches,
    uninstall_gateway_display_suppression_monkeypatch,
    uninstall_gateway_interrupt_monkeypatch,
    uninstall_process_notification_monkeypatch,
)
from .telegram import (
    _escape_telegram_mdv2,
    _replace_outside_code,
    _resolve_telegram_adapter_cls,
    _should_preserve_telegram_topic_thread,
    _telegram_edit_target_lost,
    _telegram_format_patch_status,
    _telegram_topic_recovery_patch_status,
    format_progress_tail_telegram_markdown,
    format_progress_tail_telegram_rich_markdown,
    install_telegram_format_monkeypatch,
    install_telegram_topic_recovery_monkeypatch,
    uninstall_telegram_format_monkeypatch,
    uninstall_telegram_topic_recovery_monkeypatch,
)

__all__ = [
    "_assistant_already_streamed",
    "_assistant_visible_text",
    "_capture_inline_reasoning",
    "_agent_reasoning_enabled",
    "_inline_reasoning_pending",
    "_looks_like_partial_reasoning_tag",
    "_noop_reasoning_callback",
    "_pop_tool_agent_context",
    "_push_tool_agent_context",
    "_reset_inline_reasoning_state",
    "_split_inline_reasoning",
    "_tool_context_matches",
    "_wrap_stream_delta_callback",
    "current_tool_agent_context",
    "install_agent_monkeypatches",
    "uninstall_agent_monkeypatches",
    "_pin_pairs",
    "command_menu_monkeypatch_active",
    "install_command_menu_monkeypatch",
    "uninstall_command_menu_monkeypatch",
    "_looks_like_compression_status",
    "install_compression_lifecycle_monkeypatch",
    "install_compression_status_monkeypatch",
    "uninstall_compression_lifecycle_monkeypatch",
    "uninstall_compression_status_monkeypatch",
    "configure_hook_callbacks",
    "current_hook_callbacks",
    "_extract_delegate_builder_args",
    "install_delegate_monkeypatches",
    "uninstall_delegate_monkeypatches",
    "_compact_process_failure_notification",
    "_extract_interrupt_reasons",
    "_is_stop_interrupt",
    "_legacy_global_suppression_warnings",
    "_native_gateway_suppression_enabled",
    "_process_output_tail",
    "_progress_tail_owns_platform",
    "_should_suppress_native_gateway_display",
    "_should_suppress_native_process_notification",
    "install_adapter_monkeypatches",
    "install_gateway_display_suppression_monkeypatch",
    "install_gateway_interrupt_monkeypatch",
    "install_process_notification_monkeypatch",
    "uninstall_adapter_monkeypatches",
    "uninstall_gateway_display_suppression_monkeypatch",
    "uninstall_gateway_interrupt_monkeypatch",
    "uninstall_process_notification_monkeypatch",
    "_escape_telegram_mdv2",
    "_replace_outside_code",
    "_should_preserve_telegram_topic_thread",
    "_telegram_edit_target_lost",
    "format_progress_tail_telegram_markdown",
    "format_progress_tail_telegram_rich_markdown",
    "install_telegram_format_monkeypatch",
    "install_telegram_topic_recovery_monkeypatch",
    "uninstall_telegram_format_monkeypatch",
    "uninstall_telegram_topic_recovery_monkeypatch",
    "install_monkeypatches",
    "uninstall_monkeypatches",
    "PatchTargets",
    "install_monkeypatches_report",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PatchTargets:
    agent_cls: type | None = None
    adapter_cls: type | None = None
    delegate_module: Any | None = None
    telegram_adapter_cls: type | None = None
    gateway_runner_cls: type | None = None
    display_module: Any | None = None
    process_module: Any | None = None
    commands_module: Any | None = None


@dataclass(frozen=True)
class _CapabilitySpec:
    name: str
    field: str
    members: tuple[str, ...]
    target: str
    resolver: Callable[[], Any]
    installer: Callable[..., bool]
    accepts_callbacks: bool = True


def _agent():
    from run_agent import AIAgent

    return AIAgent


def _adapter():
    from gateway.platforms.base import BasePlatformAdapter

    return BasePlatformAdapter


def _delegate():
    from tools import delegate_tool

    return delegate_tool


def _runner():
    from gateway.run import GatewayRunner

    return GatewayRunner


def _display():
    from gateway import display_config

    return display_config


def _process():
    from tools import process_registry

    return process_registry


def _commands():
    import hermes_cli.commands

    return hermes_cli.commands


_CAPABILITY_SPECS = (
    _CapabilitySpec(
        "agent_callbacks",
        "agent_cls",
        ("__init__", "_fire_reasoning_delta"),
        "run_agent.AIAgent.__init__+_fire_reasoning_delta",
        _agent,
        _agent_patch_status,
    ),
    _CapabilitySpec(
        "adapter_context",
        "adapter_cls",
        ("set_message_handler", "handle_message"),
        "gateway.platforms.base.BasePlatformAdapter.set_message_handler+handle_message",
        _adapter,
        _adapter_patch_status,
    ),
    _CapabilitySpec(
        "delegate_progress",
        "delegate_module",
        ("_build_child_progress_callback",),
        "tools.delegate_tool._build_child_progress_callback",
        _delegate,
        _delegate_patch_status,
    ),
    _CapabilitySpec(
        "telegram_format",
        "telegram_adapter_cls",
        ("edit_message",),
        "TelegramAdapter.edit_message",
        _resolve_telegram_adapter_cls,
        _telegram_format_patch_status,
    ),
    _CapabilitySpec(
        "telegram_topic_recovery",
        "gateway_runner_cls",
        ("_recover_telegram_topic_thread_id",),
        "gateway.run.GatewayRunner._recover_telegram_topic_thread_id",
        _runner,
        _telegram_topic_recovery_patch_status,
        False,
    ),
    _CapabilitySpec(
        "command_menu",
        "commands_module",
        ("telegram_menu_commands",),
        "hermes_cli.commands.telegram_menu_commands",
        _commands,
        _command_menu_patch_status,
        False,
    ),
    _CapabilitySpec(
        "gateway_interrupt",
        "gateway_runner_cls",
        ("_interrupt_and_clear_session",),
        "gateway.run.GatewayRunner._interrupt_and_clear_session",
        _runner,
        _gateway_interrupt_patch_status,
    ),
    _CapabilitySpec(
        "gateway_display",
        "display_module",
        ("resolve_display_setting",),
        "gateway.display_config.resolve_display_setting",
        _display,
        _gateway_display_patch_status,
        False,
    ),
    _CapabilitySpec(
        "process_notifications",
        "process_module",
        ("format_process_notification",),
        "tools.process_registry.format_process_notification",
        _process,
        _process_notification_patch_status,
        False,
    ),
    _CapabilitySpec(
        "compression_status",
        "agent_cls",
        ("_emit_status",),
        "run_agent.AIAgent._emit_status",
        _agent,
        _compression_status_patch_status,
    ),
    _CapabilitySpec(
        "compression_lifecycle",
        "agent_cls",
        ("_compress_context",),
        "run_agent.AIAgent._compress_context",
        _agent,
        _compression_lifecycle_patch_status,
    ),
)


def _status(spec: _CapabilitySpec, callbacks: HookCallbacks, targets: PatchTargets) -> PatchStatus:
    resolved = getattr(targets, spec.field)
    if resolved is None:
        try:
            resolved = spec.resolver()
        except Exception as exc:
            return PatchStatus(
                spec.name,
                False,
                spec.target,
                PatchFailureCategory.IMPORT_UNAVAILABLE,
                safe_patch_reason(exc),
            )
    if not all(callable(getattr(resolved, member, None)) for member in spec.members):
        return PatchStatus(
            spec.name,
            False,
            spec.target,
            PatchFailureCategory.TARGET_API_MISSING,
            "required callable API missing",
        )
    try:
        installed = (
            spec.installer(resolved, callbacks=callbacks)
            if spec.accepts_callbacks
            else spec.installer(resolved)
        )
    except Exception as exc:
        return PatchStatus(
            spec.name,
            False,
            spec.target,
            PatchFailureCategory.INSTALL_FAILED,
            safe_patch_reason(exc),
        )
    if isinstance(installed, PatchStatus):
        return installed
    if not installed:
        return PatchStatus(
            spec.name,
            False,
            spec.target,
            PatchFailureCategory.INSTALL_FAILED,
            "installer returned false",
        )
    return PatchStatus(spec.name, True, spec.target)


def install_monkeypatches_report(
    callbacks: HookCallbacks, *, targets: PatchTargets | None = None
) -> PatchInstallReport:
    resolved_targets = targets or PatchTargets()
    return PatchInstallReport(
        tuple(_status(spec, callbacks, resolved_targets) for spec in _CAPABILITY_SPECS)
    )


def install_monkeypatches(agent_cls: type | None = None) -> bool:
    return bool(
        install_monkeypatches_report(
            current_hook_callbacks(), targets=PatchTargets(agent_cls=agent_cls)
        ).any_installed
    )


def uninstall_monkeypatches(agent_cls: type | None = None) -> bool:
    return any(
        (
            uninstall_agent_monkeypatches(agent_cls),
            uninstall_adapter_monkeypatches(),
            uninstall_delegate_monkeypatches(),
            uninstall_telegram_format_monkeypatch(),
            uninstall_telegram_topic_recovery_monkeypatch(),
            uninstall_command_menu_monkeypatch(),
            uninstall_gateway_interrupt_monkeypatch(),
            uninstall_gateway_display_suppression_monkeypatch(),
            uninstall_process_notification_monkeypatch(),
            uninstall_compression_status_monkeypatch(agent_cls),
            uninstall_compression_lifecycle_monkeypatch(agent_cls),
        )
    )
