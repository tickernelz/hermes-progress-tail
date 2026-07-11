from types import SimpleNamespace

from hermes_progress_tail.hooks.contracts import current_hook_callbacks
from hermes_progress_tail.hooks.monkeypatches import (
    _CAPABILITY_SPECS,
    PatchTargets,
    install_monkeypatches,
    install_monkeypatches_report,
    uninstall_monkeypatches,
)


def _fn(*args, **kwargs):
    return None


class Agent:
    __init__ = _fn
    _fire_reasoning_delta = _fn
    _emit_status = _fn
    _compress_context = _fn


class Adapter:
    set_message_handler = _fn
    handle_message = _fn


def test_capability_order_labels_and_independent_targets():
    delegate = SimpleNamespace(_build_child_progress_callback=_fn)
    telegram = type("Telegram", (), {"edit_message": _fn})
    runner = type(
        "Runner",
        (),
        {"_recover_telegram_topic_thread_id": _fn, "_interrupt_and_clear_session": _fn},
    )
    display = SimpleNamespace(resolve_display_setting=_fn)
    process = SimpleNamespace(format_process_notification=_fn)
    commands = SimpleNamespace(telegram_menu_commands=_fn)
    targets = PatchTargets(Agent, Adapter, delegate, telegram, runner, display, process, commands)
    report = install_monkeypatches_report(current_hook_callbacks(), targets=targets)
    assert [s.name for s in report.statuses] == [s.name for s in _CAPABILITY_SPECS]
    assert [s.target for s in report.statuses] == [s.target for s in _CAPABILITY_SPECS]
    assert [s.name for s in report.statuses if not s.installed] == []


def test_legacy_agent_is_never_routed_to_adapter(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hermes_progress_tail.hooks.monkeypatches.install_adapter_monkeypatches",
        lambda value=None, **kw: calls.append(value) or False,
    )
    install_monkeypatches(Agent)
    assert calls == []
    calls.clear()
    uninstall_monkeypatches(Agent)
    assert calls == []
