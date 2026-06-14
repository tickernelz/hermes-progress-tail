from __future__ import annotations

import logging

from .agent import (
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
from .compression import (
    _looks_like_compression_status,
    install_compression_lifecycle_monkeypatch,
    install_compression_status_monkeypatch,
    uninstall_compression_lifecycle_monkeypatch,
    uninstall_compression_status_monkeypatch,
)
from .delegate import (
    _extract_delegate_builder_args,
    install_delegate_monkeypatches,
    uninstall_delegate_monkeypatches,
)
from .platform import (
    _compact_process_failure_notification,
    _extract_interrupt_reasons,
    _is_stop_interrupt,
    _process_output_tail,
    _should_suppress_native_process_notification,
    install_adapter_monkeypatches,
    install_gateway_interrupt_monkeypatch,
    install_process_notification_monkeypatch,
    uninstall_adapter_monkeypatches,
    uninstall_gateway_interrupt_monkeypatch,
    uninstall_process_notification_monkeypatch,
)
from .telegram import (
    _escape_telegram_mdv2,
    _replace_outside_code,
    _should_preserve_telegram_topic_thread,
    _telegram_edit_target_lost,
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
    "_looks_like_compression_status",
    "install_compression_lifecycle_monkeypatch",
    "install_compression_status_monkeypatch",
    "uninstall_compression_lifecycle_monkeypatch",
    "uninstall_compression_status_monkeypatch",
    "_extract_delegate_builder_args",
    "install_delegate_monkeypatches",
    "uninstall_delegate_monkeypatches",
    "_compact_process_failure_notification",
    "_extract_interrupt_reasons",
    "_is_stop_interrupt",
    "_process_output_tail",
    "_should_suppress_native_process_notification",
    "install_adapter_monkeypatches",
    "install_gateway_interrupt_monkeypatch",
    "install_process_notification_monkeypatch",
    "uninstall_adapter_monkeypatches",
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
]

logger = logging.getLogger(__name__)


def install_monkeypatches(agent_cls: type | None = None) -> bool:
    agent_ok = install_agent_monkeypatches(agent_cls)
    adapter_ok = install_adapter_monkeypatches(agent_cls)
    delegate_ok = install_delegate_monkeypatches()
    telegram_ok = install_telegram_format_monkeypatch()
    telegram_topic_ok = install_telegram_topic_recovery_monkeypatch()
    gateway_interrupt_ok = install_gateway_interrupt_monkeypatch()
    process_ok = install_process_notification_monkeypatch()
    compression_ok = install_compression_status_monkeypatch(agent_cls)
    compression_lifecycle_ok = install_compression_lifecycle_monkeypatch(agent_cls)
    installed = any(
        (
            agent_ok,
            adapter_ok,
            delegate_ok,
            telegram_ok,
            telegram_topic_ok,
            gateway_interrupt_ok,
            process_ok,
            compression_ok,
            compression_lifecycle_ok,
        )
    )
    logger.info(
        "hermes-progress-tail monkeypatches installed: agent=%s adapter=%s delegate=%s "
        "telegram_format=%s telegram_topic_recovery=%s gateway_interrupt=%s "
        "process_notifications=%s compression_status=%s compression_lifecycle=%s any=%s",
        agent_ok,
        adapter_ok,
        delegate_ok,
        telegram_ok,
        telegram_topic_ok,
        gateway_interrupt_ok,
        process_ok,
        compression_ok,
        compression_lifecycle_ok,
        installed,
    )
    return installed


def uninstall_monkeypatches(agent_cls: type | None = None) -> bool:
    return any(
        (
            uninstall_agent_monkeypatches(agent_cls),
            uninstall_adapter_monkeypatches(agent_cls),
            uninstall_delegate_monkeypatches(),
            uninstall_telegram_format_monkeypatch(),
            uninstall_telegram_topic_recovery_monkeypatch(),
            uninstall_gateway_interrupt_monkeypatch(),
            uninstall_process_notification_monkeypatch(),
            uninstall_compression_status_monkeypatch(agent_cls),
            uninstall_compression_lifecycle_monkeypatch(agent_cls),
        )
    )
