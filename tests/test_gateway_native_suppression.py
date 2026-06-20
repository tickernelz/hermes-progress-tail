from __future__ import annotations

from types import SimpleNamespace

from hermes_progress_tail.hooks.platform import (
    _should_suppress_native_process_notification,
    install_gateway_display_suppression_monkeypatch,
    uninstall_gateway_display_suppression_monkeypatch,
)


def _display_module():
    def resolve_display_setting(config, platform_key, key, default=None, **kwargs):
        display = config.get("display") if isinstance(config.get("display"), dict) else {}
        platform_values = (
            display.get("platforms") if isinstance(display.get("platforms"), dict) else {}
        )
        platform_display = (
            platform_values.get(platform_key)
            if isinstance(platform_values.get(platform_key), dict)
            else {}
        )
        if key in platform_display:
            return platform_display[key]
        return display.get(key, default)

    return SimpleNamespace(resolve_display_setting=resolve_display_setting)


def test_gateway_display_monkeypatch_suppresses_native_gateway_progress_only_when_owned():
    module = _display_module()
    original = module.resolve_display_setting
    config = {
        "display": {
            "tool_progress": "all",
            "show_reasoning": True,
            "streaming": True,
            "interim_assistant_messages": True,
            "thinking_progress": True,
            "long_running_notifications": True,
            "timestamps": True,
        },
        "progress_tail": {
            "enabled": True,
            "native_gateway": {"suppress": True},
        },
    }

    assert install_gateway_display_suppression_monkeypatch(module) is True
    try:
        assert module.resolve_display_setting(config, "telegram", "tool_progress") == "off"
        assert module.resolve_display_setting(config, "telegram", "show_reasoning") is False
        assert module.resolve_display_setting(config, "telegram", "streaming") is False
        assert (
            module.resolve_display_setting(config, "telegram", "interim_assistant_messages")
            is False
        )
        assert module.resolve_display_setting(config, "telegram", "thinking_progress") is False
        assert (
            module.resolve_display_setting(config, "telegram", "long_running_notifications")
            is False
        )
        assert module.resolve_display_setting(config, "telegram", "timestamps") is True
    finally:
        assert uninstall_gateway_display_suppression_monkeypatch(module) is True

    assert module.resolve_display_setting is original


def test_gateway_display_monkeypatch_preserves_native_settings_when_not_owned():
    module = _display_module()
    config = {
        "display": {"tool_progress": "all", "show_reasoning": True, "streaming": True},
        "progress_tail": {
            "enabled": True,
            "native_gateway": {"suppress": True},
            "platforms": {"telegram": {"enabled": False}},
        },
    }

    assert install_gateway_display_suppression_monkeypatch(module) is True
    try:
        assert module.resolve_display_setting(config, "telegram", "tool_progress") == "all"
        assert module.resolve_display_setting(config, "telegram", "show_reasoning") is True
        assert module.resolve_display_setting(config, "telegram", "streaming") is True
    finally:
        uninstall_gateway_display_suppression_monkeypatch(module)


def test_gateway_display_monkeypatch_preserves_native_settings_when_suppression_disabled():
    module = _display_module()
    config = {
        "display": {"tool_progress": "all", "show_reasoning": True, "streaming": True},
        "progress_tail": {"enabled": True, "native_gateway": {"suppress": False}},
    }

    assert install_gateway_display_suppression_monkeypatch(module) is True
    try:
        assert module.resolve_display_setting(config, "telegram", "tool_progress") == "all"
        assert module.resolve_display_setting(config, "telegram", "show_reasoning") is True
        assert module.resolve_display_setting(config, "telegram", "streaming") is True
    finally:
        uninstall_gateway_display_suppression_monkeypatch(module)


def test_process_notification_suppression_requires_owned_gateway_event():
    config = {
        "progress_tail": {
            "enabled": True,
            "native_gateway": {"suppress": True},
            "background_jobs": {
                "suppress_native_notify": True,
                "suppress_watch_notifications": True,
            },
        }
    }

    assert _should_suppress_native_process_notification(
        {"type": "completion", "exit_code": 0, "platform": "telegram", "session_key": "s1"},
        config=config,
    )
    assert _should_suppress_native_process_notification(
        {"type": "watch_match", "platform": "telegram", "session_key": "s1"},
        config=config,
    )
    assert not _should_suppress_native_process_notification(
        {"type": "completion", "exit_code": 0},
        config=config,
    )
    assert not _should_suppress_native_process_notification(
        {"type": "watch_match"},
        config=config,
    )


def test_process_notification_suppression_respects_platform_and_feature_settings():
    config = {
        "progress_tail": {
            "enabled": True,
            "native_gateway": {"suppress": True},
            "platforms": {"telegram": {"enabled": False}},
            "background_jobs": {
                "suppress_native_notify": True,
                "suppress_watch_notifications": False,
            },
        }
    }

    assert not _should_suppress_native_process_notification(
        {"type": "completion", "exit_code": 0, "platform": "telegram", "session_key": "s1"},
        config=config,
    )
    assert not _should_suppress_native_process_notification(
        {"type": "watch_match", "platform": "discord", "session_key": "s2"},
        config=config,
    )


def test_strategy_off_platform_is_not_owned_for_display_or_process_notifications():
    module = _display_module()
    config = {
        "display": {"tool_progress": "all", "show_reasoning": True, "streaming": True},
        "progress_tail": {
            "enabled": True,
            "native_gateway": {"suppress": True},
            "platforms": {"telegram": {"strategy": "off"}},
            "background_jobs": {
                "suppress_native_notify": True,
                "suppress_watch_notifications": True,
            },
        },
    }

    assert install_gateway_display_suppression_monkeypatch(module) is True
    try:
        assert module.resolve_display_setting(config, "telegram", "tool_progress") == "all"
        assert module.resolve_display_setting(config, "telegram", "show_reasoning") is True
        assert module.resolve_display_setting(config, "telegram", "streaming") is True
    finally:
        uninstall_gateway_display_suppression_monkeypatch(module)

    assert not _should_suppress_native_process_notification(
        {"type": "completion", "exit_code": 0, "platform": "telegram", "session_key": "s1"},
        config=config,
    )
    assert not _should_suppress_native_process_notification(
        {"type": "watch_match", "platform": "telegram", "session_key": "s1"},
        config=config,
    )
