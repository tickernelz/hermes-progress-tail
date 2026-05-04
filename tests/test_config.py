from hermes_progress_tail.config import load_settings, resolve_platform_settings


def test_load_settings_defaults():
    settings = load_settings({})

    assert settings.enabled is True
    assert settings.defaults.lines == 3
    assert settings.defaults.preview_length == 120
    assert settings.no_edit.interval_seconds == 30
    assert settings.tools.timestamp is True
    assert settings.tools.timestamp_format == "%H:%M"
    assert settings.todo.sticky is True
    assert settings.todo.hide_tool_line is True
    assert settings.patch.detail == "smart"
    assert settings.patch.preview_chars == 48
    assert settings.patch.max_files == 3
    assert settings.tools.show_duration is True
    assert settings.delegates.enabled is True
    assert settings.delegates.max_delegates == 4
    assert settings.delegates.lines_per_delegate == 2
    assert settings.delegates.thinking == "off"
    assert not hasattr(settings.reasoning, "capture_inline_think_tags")
    assert settings.renderer.style == "emoji"
    assert settings.renderer.density == "normal"


def test_resolve_platform_override():
    settings = load_settings(
        {
            "progress_tail": {
                "tools": {"lines": 4, "preview_length": 90, "timestamp": False},
                "delegates": {"max_delegates": 2, "lines_per_delegate": 1},
                "platforms": {
                    "discord": {
                        "enabled": True,
                        "strategy": "live_tail",
                        "lines": 2,
                        "delegates": False,
                    }
                },
            }
        }
    )

    platform = resolve_platform_settings(settings, "discord")

    assert platform.enabled is True
    assert platform.strategy == "live_tail"
    assert platform.lines == 2
    assert platform.preview_length == 90
    assert platform.timestamp is False
    assert platform.delegates_enabled is False
    assert settings.delegates.max_delegates == 2
    assert settings.delegates.lines_per_delegate == 1


def test_invalid_values_fall_back_safely():
    settings = load_settings(
        {
            "progress_tail": {
                "defaults": {"lines": 0, "preview_length": "bad", "edit_interval": -1},
                "platforms": {"sms": {"strategy": "nonsense"}},
            }
        }
    )

    assert settings.defaults.lines == 3
    assert settings.defaults.preview_length == 120
    assert settings.defaults.edit_interval == 1.5
    assert resolve_platform_settings(settings, "sms").strategy == "off"
