from __future__ import annotations

import builtins
from types import SimpleNamespace

import pytest

from hermes_progress_tail.hooks import platform

OWNED = {"progress_tail": {"platforms": {"telegram": {"enabled": True}}}}


@pytest.fixture
def display():
    module = SimpleNamespace(resolve_display_setting=lambda *args, **kwargs: "native")
    yield module
    platform.uninstall_gateway_display_suppression_monkeypatch(module)


@pytest.fixture
def process(monkeypatch):
    module = SimpleNamespace(format_process_notification=lambda evt: f"native:{evt!r}")
    monkeypatch.setattr(platform, "_process_notification_config", lambda _config: OWNED)
    yield module
    platform.uninstall_process_notification_monkeypatch(module)


def test_display_install_suppresses_owned_settings_and_restores(display):
    original = display.resolve_display_setting
    assert platform.install_gateway_display_suppression_monkeypatch(display)
    assert platform.install_gateway_display_suppression_monkeypatch(display)
    assert display.resolve_display_setting(OWNED, "telegram", "show_reasoning") is False
    assert display.resolve_display_setting(OWNED, "telegram", "tool_progress") == "off"
    assert display.resolve_display_setting(OWNED, "telegram", "unrelated") == "native"
    assert platform.uninstall_gateway_display_suppression_monkeypatch(display)
    assert display.resolve_display_setting is original
    assert not hasattr(display, "_hermes_progress_tail_gateway_display_patched")
    assert platform.uninstall_gateway_display_suppression_monkeypatch(display) is False
    assert platform.install_gateway_display_suppression_monkeypatch(display) is True
    assert platform.uninstall_gateway_display_suppression_monkeypatch(display) is True
    assert display.resolve_display_setting is original
    assert not hasattr(display, "_hermes_progress_tail_gateway_display_patched")


def test_display_keyword_arguments_and_non_ownership_passthrough(display):
    assert platform.install_gateway_display_suppression_monkeypatch(display)
    disabled = {"progress_tail": {"enabled": False}}
    assert (
        display.resolve_display_setting(config=disabled, platform="telegram", key="streaming")
        == "native"
    )
    assert (
        display.resolve_display_setting(user_config=OWNED, platform_key="", setting="streaming")
        == "native"
    )
    assert (
        display.resolve_display_setting(
            user_config=OWNED, platform_key="telegram", setting="streaming"
        )
        is False
    )


def test_display_ownership_error_uses_safe_platform_fallback(monkeypatch, display):
    monkeypatch.setattr(
        "hermes_progress_tail.settings.config.load_settings",
        lambda _config: (_ for _ in ()).throw(ValueError("bad config")),
    )
    assert platform.install_gateway_display_suppression_monkeypatch(display)
    assert display.resolve_display_setting(OWNED, "telegram", "streaming") is False
    off = {"progress_tail": {"platforms": {"telegram": {"strategy": "off"}}}}
    assert display.resolve_display_setting(off, "telegram", "streaming") == "native"
    disabled = {"progress_tail": {"platforms": {"telegram": {"enabled": False}}}}
    assert display.resolve_display_setting(disabled, "telegram", "streaming") == "native"


def test_display_unavailable_api_and_scoped_import_failure(monkeypatch):
    assert platform.install_gateway_display_suppression_monkeypatch(SimpleNamespace()) is False
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "gateway":
            raise ImportError("isolated missing display")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "__import__", blocked)
        assert platform.install_gateway_display_suppression_monkeypatch() is False
        assert platform.uninstall_gateway_display_suppression_monkeypatch() is False


def test_process_install_suppresses_owned_success_and_watch_then_restores(process):
    original = process.format_process_notification
    assert platform.install_process_notification_monkeypatch(process)
    assert platform.install_process_notification_monkeypatch(process)
    route = {"platform": "telegram", "chat_id": "1"}
    assert (
        process.format_process_notification({**route, "type": "completion", "exit_code": 0}) is None
    )
    assert process.format_process_notification({**route, "type": "watch_match"}) is None
    assert platform.uninstall_process_notification_monkeypatch(process)
    assert process.format_process_notification is original
    assert not hasattr(process, "_hermes_progress_tail_process_notification_patched")
    assert platform.uninstall_process_notification_monkeypatch(process) is False
    assert platform.install_process_notification_monkeypatch(process) is True
    assert platform.uninstall_process_notification_monkeypatch(process) is True
    assert process.format_process_notification is original
    assert not hasattr(process, "_hermes_progress_tail_process_notification_patched")


def test_process_passthrough_for_unowned_invalid_and_unsuppressed_events(process):
    assert platform.install_process_notification_monkeypatch(process)
    events = [
        "invalid",
        {"type": "notice"},
        {"type": "completion", "chat_id": "1"},
    ]
    for event in events:
        assert process.format_process_notification(event) == f"native:{event!r}"


def test_process_config_controls_watch_and_completion_suppression(monkeypatch, process):
    config = {
        "progress_tail": {
            "platforms": {"telegram": {"enabled": True}},
            "background_jobs": {
                "suppress_watch_notifications": False,
                "suppress_native_notify": False,
            },
        }
    }
    monkeypatch.setattr(platform, "_process_notification_config", lambda _config: config)
    assert platform.install_process_notification_monkeypatch(process)
    for event_type in ("watch_disabled", "completion"):
        event = {"platform": "telegram", "type": event_type, "exit_code": 0}
        assert process.format_process_notification(event) == f"native:{event!r}"


def test_failed_process_notification_is_compact_and_bounds_output(process):
    assert platform.install_process_notification_monkeypatch(process)
    event = {
        "type": "completion",
        "platform": "telegram",
        "exit_code": 7,
        "session_id": "job-2",
        "command": " python   worker.py ",
        "output": "ignored\n" + "x" * 900 + "\nlast\n",
    }
    rendered = process.format_process_notification(event)
    assert rendered.startswith(
        "[Background process job-2 failed with exit 7: python worker.py]\nOutput tail:\n"
    )
    tail = rendered.split("Output tail:\n", 1)[1]
    assert len(tail) == 800
    assert tail.endswith("\nlast")
    no_details = process.format_process_notification(
        {"type": "completion", "platform": "telegram", "exit_code": 2}
    )
    assert no_details == "[Background process process failed with exit 2]"


def test_formatting_failure_falls_back_to_original(monkeypatch, process):
    monkeypatch.setattr(
        platform,
        "_process_notification_config",
        lambda _config: {"progress_tail": {"enabled": False}},
    )
    assert platform.install_process_notification_monkeypatch(process)
    malformed = {"type": "completion", "platform": "telegram", "exit_code": object()}
    assert process.format_process_notification(malformed) == f"native:{malformed!r}"


def test_process_unavailable_api_and_scoped_import_failure(monkeypatch):
    assert platform.install_process_notification_monkeypatch(SimpleNamespace()) is False
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "tools":
            raise ImportError("isolated missing process registry")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "__import__", blocked)
        assert platform.install_process_notification_monkeypatch() is False
        assert platform.uninstall_process_notification_monkeypatch() is False
