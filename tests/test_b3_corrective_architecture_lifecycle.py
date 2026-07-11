"""B3 corrective contract: real leaf state, lifecycle, and aggregate architecture.

The adapter below is deliberately behavioural: it does not prescribe private helper
names, locations, or signatures.  A production capability spec must expose a
structured callable returning PatchStatus when invoked by the aggregate.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from types import ModuleType, SimpleNamespace

import pytest

from hermes_progress_tail.hooks import (
    agent,
    command_menus,
    compression,
    delegate,
    platform,
    telegram,
)
from hermes_progress_tail.hooks import monkeypatches as mp
from hermes_progress_tail.hooks.contracts import current_hook_callbacks
from hermes_progress_tail.hooks.install_report import PatchStatus

ROWS = (
    "agent_callbacks",
    "adapter_context",
    "delegate_progress",
    "telegram_format",
    "telegram_topic_recovery",
    "command_menu",
    "gateway_interrupt",
    "gateway_display",
    "process_notifications",
    "compression_status",
    "compression_lifecycle",
)

LEAF_STATUS_MODULES = (
    agent.__name__,
    platform.__name__,
    delegate.__name__,
    telegram.__name__,
    telegram.__name__,
    command_menus.__name__,
    platform.__name__,
    platform.__name__,
    platform.__name__,
    compression.__name__,
    compression.__name__,
)


def _fn(*args, **kwargs):
    return None


def _target(*members):
    return type("Target", (), {name: _fn for name in members})


# Independently enumerated owning seams. registry_kind is class-key, id tuple, or command id tuple.
CASES = (
    (
        "agent_callbacks",
        agent,
        agent.install_agent_monkeypatches,
        agent._PATCH_MARKER,
        agent._ORIGINALS,
        "dict",
        ("__init__", "_fire_reasoning_delta"),
    ),
    (
        "adapter_context",
        platform,
        platform.install_adapter_monkeypatches,
        "_hermes_progress_tail_adapter_patched",
        platform._ADAPTER_ORIGINALS,
        "dict",
        ("set_message_handler", "handle_message"),
    ),
    (
        "delegate_progress",
        delegate,
        delegate.install_delegate_monkeypatches,
        delegate._DELEGATE_PATCH_MARKER,
        delegate._DELEGATE_ORIGINALS,
        "id-one",
        ("_build_child_progress_callback",),
    ),
    (
        "telegram_format",
        telegram,
        telegram.install_telegram_format_monkeypatch,
        telegram._TELEGRAM_PATCH_MARKER,
        telegram._TELEGRAM_ORIGINALS,
        "one",
        ("edit_message",),
    ),
    (
        "telegram_topic_recovery",
        telegram,
        telegram.install_telegram_topic_recovery_monkeypatch,
        telegram._TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER,
        telegram._TELEGRAM_TOPIC_RECOVERY_ORIGINALS,
        "one",
        ("_recover_telegram_topic_thread_id",),
    ),
    (
        "command_menu",
        command_menus,
        command_menus.install_command_menu_monkeypatch,
        command_menus._COMMAND_MENU_PATCH_MARKER,
        command_menus._COMMAND_MENU_ORIGINALS,
        "id-dict",
        ("telegram_menu_commands",),
    ),
    (
        "gateway_interrupt",
        platform,
        platform.install_gateway_interrupt_monkeypatch,
        platform._GATEWAY_INTERRUPT_PATCH_MARKER,
        platform._GATEWAY_INTERRUPT_ORIGINALS,
        "one",
        ("_interrupt_and_clear_session",),
    ),
    (
        "gateway_display",
        platform,
        platform.install_gateway_display_suppression_monkeypatch,
        platform._GATEWAY_DISPLAY_PATCH_MARKER,
        platform._GATEWAY_DISPLAY_ORIGINALS,
        "id-one",
        ("resolve_display_setting",),
    ),
    (
        "process_notifications",
        platform,
        platform.install_process_notification_monkeypatch,
        platform._PROCESS_NOTIFICATION_PATCH_MARKER,
        platform._PROCESS_NOTIFICATION_ORIGINALS,
        "id-one",
        ("format_process_notification",),
    ),
    (
        "compression_status",
        compression,
        compression.install_compression_status_monkeypatch,
        compression._COMPRESSION_STATUS_PATCH_MARKER,
        compression._COMPRESSION_STATUS_ORIGINALS,
        "one",
        ("_emit_status",),
    ),
    (
        "compression_lifecycle",
        compression,
        compression.install_compression_lifecycle_monkeypatch,
        compression._COMPRESSION_LIFECYCLE_PATCH_MARKER,
        compression._COMPRESSION_LIFECYCLE_ORIGINALS,
        "one",
        ("_compress_context",),
    ),
)


def _registry_put(registry, kind, target, members, value=_fn):
    if kind == "dict":
        registry[target] = {m: value for m in members}
    elif kind == "id-dict":
        registry[id(target)] = (target, {m: value for m in members})
    elif kind == "id-one":
        registry[id(target)] = (target, value)
    else:
        registry[target] = value


@pytest.mark.parametrize("name,module,install,marker,registry,kind,members", CASES, ids=ROWS)
def test_real_leaf_valid_exact_state_and_stale_or_foreign_registry(
    monkeypatch, name, module, install, marker, registry, kind, members
):
    target = _target(*members)
    monkeypatch.setattr(target, marker, True, raising=False)
    registry.clear()
    assert install(target) is False, f"{name}: marker without registry is stale"
    foreign = _target(*members)
    _registry_put(registry, kind, foreign, members)
    assert install(target) is False, f"{name}: another target's originals are invalid"
    registry.clear()
    _registry_put(registry, kind, target, members, None)
    assert install(target) is False, f"{name}: originals must be callable"
    registry.clear()
    _registry_put(registry, kind, target, members)
    before = tuple(getattr(target, m) for m in members)
    assert install(target) is True
    assert tuple(getattr(target, m) for m in members) == before, (
        "valid installed state must not mutate"
    )
    registry.clear()


@pytest.mark.parametrize(
    "name,module,install,marker,registry,kind,members", CASES[:2], ids=ROWS[:2]
)
def test_required_multi_member_registry_must_be_complete(
    monkeypatch, name, module, install, marker, registry, kind, members
):
    target = _target(*members)
    monkeypatch.setattr(target, marker, True, raising=False)
    _registry_put(registry, kind, target, members[:1])
    assert install(target) is False
    registry.clear()


def test_specs_are_structured_status_callables_not_public_boolean_installers():
    public = {case[2] for case in CASES}
    assert [s.name for s in mp._CAPABILITY_SPECS] == list(ROWS)
    assert all(s.installer not in public for s in mp._CAPABILITY_SPECS)
    assert tuple(s.installer.__module__ for s in mp._CAPABILITY_SPECS) == LEAF_STATUS_MODULES
    # Documented aggregate adapter: resolved target first, callbacks only where declared.
    callbacks = current_hook_callbacks()
    for spec in mp._CAPABILITY_SPECS:
        target = SimpleNamespace(**{m: _fn for m in spec.members})
        result = (
            spec.installer(target, callbacks=callbacks)
            if spec.accepts_callbacks
            else spec.installer(target)
        )
        assert isinstance(result, PatchStatus), spec.name


def test_real_patchtargets_routing_and_exact_callback_signatures(monkeypatch):
    sentinels = {f: SimpleNamespace() for f in mp.PatchTargets.__dataclass_fields__}
    calls = []
    specs = []
    callback_rows = {
        "agent_callbacks",
        "adapter_context",
        "delegate_progress",
        "telegram_format",
        "gateway_interrupt",
        "compression_status",
        "compression_lifecycle",
    }
    for spec in mp._CAPABILITY_SPECS:
        for member in spec.members:
            setattr(sentinels[spec.field], member, _fn)

        def invoke(target, *args, _name=spec.name, **kwargs):
            calls.append((_name, target, args, kwargs))
            return PatchStatus(_name, True, "x")

        specs.append(
            replace(
                spec, resolver=lambda: pytest.fail("injected target resolved"), installer=invoke
            )
        )
    monkeypatch.setattr(mp, "_CAPABILITY_SPECS", tuple(specs))
    callbacks = current_hook_callbacks()
    mp.install_monkeypatches_report(callbacks, targets=mp.PatchTargets(**sentinels))
    for name, target, args, kwargs in calls:
        spec = next(s for s in specs if s.name == name)
        assert target is sentinels[spec.field] and args == ()
        assert kwargs == ({"callbacks": callbacks} if name in callback_rows else {})


def _commands(tag, slack=True):
    obj = SimpleNamespace(telegram_menu_commands=lambda max_commands=100: ([(tag, tag)], 0))
    if slack:
        obj.slack_native_slashes = lambda: [(tag, tag, "")]
    return obj


def test_command_menu_complete_identity_lifecycle():
    first, second = _commands("a"), _commands("b", slack=False)
    originals = (
        first.telegram_menu_commands,
        first.slack_native_slashes,
        second.telegram_menu_commands,
    )
    assert command_menus.install_command_menu_monkeypatch(first) is True
    assert command_menus.install_command_menu_monkeypatch(first) is True
    assert command_menus.install_command_menu_monkeypatch(second) is True
    assert command_menus.uninstall_command_menu_monkeypatch(first) is True
    assert (
        first.telegram_menu_commands is originals[0] and first.slack_native_slashes is originals[1]
    )
    assert getattr(first, command_menus._COMMAND_MENU_PATCH_MARKER) is False
    assert command_menus.uninstall_command_menu_monkeypatch(first) is False
    assert command_menus.uninstall_command_menu_monkeypatch(second) is True
    assert second.telegram_menu_commands is originals[2]


def test_command_menu_no_argument_production_resolution(monkeypatch):
    package, commands = ModuleType("hermes_cli"), ModuleType("hermes_cli.commands")
    commands.telegram_menu_commands = _fn
    package.commands = commands
    monkeypatch.setitem(sys.modules, "hermes_cli", package)
    monkeypatch.setitem(sys.modules, "hermes_cli.commands", commands)
    original = commands.telegram_menu_commands
    assert command_menus.install_command_menu_monkeypatch() is True
    assert command_menus.uninstall_command_menu_monkeypatch() is True
    assert commands.telegram_menu_commands is original
