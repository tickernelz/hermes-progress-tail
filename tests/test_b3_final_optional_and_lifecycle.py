"""Final B3 behavioral closure: optional saved originals and all leaf lifecycles."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_progress_tail.hooks import (
    agent,
    command_menus,
    compression,
    delegate,
    platform,
    telegram,
)
from hermes_progress_tail.hooks.install_report import PatchFailureCategory, PatchStatus


def _fn(*args, **kwargs):
    return None


OPTIONALS = (
    (
        "agent_emit",
        agent._agent_patch_status,
        agent.install_agent_monkeypatches,
        agent._PATCH_MARKER,
        agent._ORIGINALS,
        "_emit_interim_assistant_message",
        "agent",
    ),
    (
        "agent_tool",
        agent._agent_patch_status,
        agent.install_agent_monkeypatches,
        agent._PATCH_MARKER,
        agent._ORIGINALS,
        "_invoke_tool",
        "agent",
    ),
    (
        "telegram_send",
        telegram._telegram_format_patch_status,
        telegram.install_telegram_format_monkeypatch,
        telegram._TELEGRAM_PATCH_MARKER,
        (telegram._TELEGRAM_ORIGINALS, telegram._TELEGRAM_SEND_ORIGINALS),
        "send",
        "telegram",
    ),
    (
        "command_slack",
        command_menus._command_menu_patch_status,
        command_menus.install_command_menu_monkeypatch,
        command_menus._COMMAND_MENU_PATCH_MARKER,
        command_menus._COMMAND_MENU_ORIGINALS,
        "slack_native_slashes",
        "command",
    ),
)


def _optional_target(kind: str, optional: str, present: bool = True):
    required = {
        "agent": {"__init__": _fn, "_fire_reasoning_delta": _fn},
        "telegram": {"edit_message": _fn},
        "command": {"telegram_menu_commands": _fn},
    }[kind]
    if present:
        required[optional] = _fn
    return (
        type("OptionalTarget", (), required) if kind != "command" else SimpleNamespace(**required)
    )


def _seed_optional_registry(target, kind, optional, saved):
    if kind == "agent":
        agent._ORIGINALS[target] = {"__init__": _fn, "_fire_reasoning_delta": _fn, optional: saved}
    elif kind == "telegram":
        telegram._TELEGRAM_ORIGINALS[target] = _fn
        if saved != "missing":
            telegram._TELEGRAM_SEND_ORIGINALS[target] = saved
    else:
        values = {"telegram_menu_commands": _fn}
        if saved != "missing":
            values[optional] = saved
        command_menus._COMMAND_MENU_ORIGINALS[id(target)] = (target, values)


def _clear_optional(target, kind):
    if kind == "agent":
        agent._ORIGINALS.pop(target, None)
    elif kind == "telegram":
        telegram._TELEGRAM_ORIGINALS.pop(target, None)
        telegram._TELEGRAM_SEND_ORIGINALS.pop(target, None)
    else:
        command_menus._COMMAND_MENU_ORIGINALS.pop(id(target), None)


@pytest.mark.parametrize(
    "name,status_fn,public,marker,registry,optional,kind",
    OPTIONALS,
    ids=lambda x: x if isinstance(x, str) else None,
)
@pytest.mark.parametrize("saved", ["missing", None], ids=["missing", "noncallable"])
def test_optional_present_requires_callable_saved_original(
    monkeypatch, name, status_fn, public, marker, registry, optional, kind, saved
):
    target = _optional_target(kind, optional)
    monkeypatch.setattr(target, marker, True, raising=False)
    _seed_optional_registry(target, kind, optional, saved)
    try:
        status = status_fn(target)
        assert isinstance(status, PatchStatus)
        assert status.installed is False
        assert status.failure_category is PatchFailureCategory.INSTALL_FAILED
        assert public(target) is False
    finally:
        _clear_optional(target, kind)


@pytest.mark.parametrize(
    "name,status_fn,public,marker,registry,optional,kind",
    OPTIONALS,
    ids=lambda x: x if isinstance(x, str) else None,
)
def test_optional_present_exact_callable_original_is_installed(
    monkeypatch, name, status_fn, public, marker, registry, optional, kind
):
    target = _optional_target(kind, optional)
    monkeypatch.setattr(target, marker, True, raising=False)
    _seed_optional_registry(target, kind, optional, _fn)
    try:
        assert status_fn(target).installed is True
        assert public(target) is True
    finally:
        _clear_optional(target, kind)


@pytest.mark.parametrize(
    "name,status_fn,public,marker,registry,optional,kind",
    OPTIONALS,
    ids=lambda x: x if isinstance(x, str) else None,
)
def test_optional_absent_remains_success(
    monkeypatch, name, status_fn, public, marker, registry, optional, kind
):
    target = _optional_target(kind, optional, present=False)
    monkeypatch.setattr(target, marker, True, raising=False)
    _seed_optional_registry(target, kind, optional, "missing")
    try:
        assert status_fn(target).installed is True
        assert public(target) is True
    finally:
        _clear_optional(target, kind)


LIFECYCLES = (
    (
        "agent_callbacks",
        agent.install_agent_monkeypatches,
        agent.uninstall_agent_monkeypatches,
        agent._PATCH_MARKER,
        agent._ORIGINALS,
        ("__init__", "_fire_reasoning_delta"),
    ),
    (
        "adapter_context",
        platform.install_adapter_monkeypatches,
        platform.uninstall_adapter_monkeypatches,
        "_hermes_progress_tail_adapter_patched",
        platform._ADAPTER_ORIGINALS,
        ("set_message_handler", "handle_message"),
    ),
    (
        "delegate_progress",
        delegate.install_delegate_monkeypatches,
        delegate.uninstall_delegate_monkeypatches,
        delegate._DELEGATE_PATCH_MARKER,
        delegate._DELEGATE_ORIGINALS,
        ("_build_child_progress_callback",),
    ),
    (
        "telegram_format",
        telegram.install_telegram_format_monkeypatch,
        telegram.uninstall_telegram_format_monkeypatch,
        telegram._TELEGRAM_PATCH_MARKER,
        telegram._TELEGRAM_ORIGINALS,
        ("edit_message", "send"),
    ),
    (
        "telegram_topic_recovery",
        telegram.install_telegram_topic_recovery_monkeypatch,
        telegram.uninstall_telegram_topic_recovery_monkeypatch,
        telegram._TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER,
        telegram._TELEGRAM_TOPIC_RECOVERY_ORIGINALS,
        ("_recover_telegram_topic_thread_id",),
    ),
    (
        "command_menu",
        command_menus.install_command_menu_monkeypatch,
        command_menus.uninstall_command_menu_monkeypatch,
        command_menus._COMMAND_MENU_PATCH_MARKER,
        command_menus._COMMAND_MENU_ORIGINALS,
        ("telegram_menu_commands", "slack_native_slashes"),
    ),
    (
        "gateway_interrupt",
        platform.install_gateway_interrupt_monkeypatch,
        platform.uninstall_gateway_interrupt_monkeypatch,
        platform._GATEWAY_INTERRUPT_PATCH_MARKER,
        platform._GATEWAY_INTERRUPT_ORIGINALS,
        ("_interrupt_and_clear_session",),
    ),
    (
        "gateway_display",
        platform.install_gateway_display_suppression_monkeypatch,
        platform.uninstall_gateway_display_suppression_monkeypatch,
        platform._GATEWAY_DISPLAY_PATCH_MARKER,
        platform._GATEWAY_DISPLAY_ORIGINALS,
        ("resolve_display_setting",),
    ),
    (
        "process_notifications",
        platform.install_process_notification_monkeypatch,
        platform.uninstall_process_notification_monkeypatch,
        platform._PROCESS_NOTIFICATION_PATCH_MARKER,
        platform._PROCESS_NOTIFICATION_ORIGINALS,
        ("format_process_notification",),
    ),
    (
        "compression_status",
        compression.install_compression_status_monkeypatch,
        compression.uninstall_compression_status_monkeypatch,
        compression._COMPRESSION_STATUS_PATCH_MARKER,
        compression._COMPRESSION_STATUS_ORIGINALS,
        ("_emit_status",),
    ),
    (
        "compression_lifecycle",
        compression.install_compression_lifecycle_monkeypatch,
        compression.uninstall_compression_lifecycle_monkeypatch,
        compression._COMPRESSION_LIFECYCLE_PATCH_MARKER,
        compression._COMPRESSION_LIFECYCLE_ORIGINALS,
        ("_compress_context",),
    ),
)


@pytest.mark.parametrize(
    "name,install,uninstall,marker,registry,members", LIFECYCLES, ids=[row[0] for row in LIFECYCLES]
)
def test_all_real_leaf_literal_bool_identity_lifecycle(
    name, install, uninstall, marker, registry, members
):
    target = type("LifecycleTarget", (), {member: _fn for member in members})
    originals = {member: getattr(target, member) for member in members}
    try:
        first = install(target)
        assert first is True
        patched = {member: getattr(target, member) for member in members}
        assert install(target) is True
        assert all(getattr(target, member) is patched[member] for member in members)
        assert uninstall(target) is True
        assert all(getattr(target, member) is originals[member] for member in members)
        assert not getattr(target, marker, False)
        assert target not in registry and id(target) not in registry
        assert uninstall(target) is False
    finally:
        uninstall(target)
        registry.pop(target, None)
        registry.pop(id(target), None)
