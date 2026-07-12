from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.settings.platforms import resolve_platform_settings


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
    assert settings.assistant.min_update_chars == 160
    assert settings.reasoning.min_update_chars == 300
    assert settings.tools.show_completed is True
    assert settings.tools.show_duration is True
    assert settings.delegates.enabled is True
    assert settings.delegates.max_delegates == 4
    assert settings.delegates.lines_per_delegate == 2
    assert settings.delegates.completed_ttl_seconds == 5
    assert settings.delegates.thinking == "off"
    assert not hasattr(settings.reasoning, "capture_inline_think_tags")
    assert settings.renderer.style == "emoji"
    assert settings.renderer.density == "normal"
    assert settings.renderer.edit_interval == 5.0
    assert settings.renderer.agent_label == ""
    assert (
        load_settings({"progress_tail": {"renderer": {"density": "verbose"}}}).renderer.density
        == "verbose"
    )
    assert not hasattr(settings.renderer, "code_fence")
    assert not hasattr(settings.renderer, "code_fence_language")
    assert settings.background_jobs.enabled is True
    assert settings.background_jobs.suppress_native_notify is True
    assert settings.background_jobs.max_jobs == 4
    assert settings.background_jobs.completed_ttl_seconds == 5
    assert settings.background_jobs.update_interval_seconds == 10
    assert settings.cleanup.auto_delete is False
    assert settings.cleanup.delay_seconds == 5
    assert settings.cleanup.delete_on_success is True
    assert settings.cleanup.delete_on_failure is False
    assert settings.cleanup.delete_background_active is False
    assert settings.telegram.rich_messages is True
    assert settings.telegram.verification_table is True
    assert not hasattr(settings.telegram, "collapsible_details")
    assert settings.telegram.thinking_blocks is True
    assert settings.telegram.max_table_rows == 8
    assert not hasattr(settings.telegram, "details_open_on_failure")
    assert settings.telegram.compact_success is True
    assert settings.telegram.max_detail_items == 8
    assert not hasattr(settings, "finalization")


def test_load_settings_accepts_telegram_rich_config():
    settings = load_settings(
        {
            "progress_tail": {
                "telegram": {
                    "rich_messages": False,
                    "verification_table": False,
                    "collapsible_details": False,
                    "thinking_blocks": False,
                    "max_table_rows": 3,
                    "details_open_on_failure": False,
                    "compact_success": False,
                    "max_detail_items": 2,
                }
            }
        }
    )

    assert settings.telegram.rich_messages is False
    assert settings.telegram.verification_table is False
    assert not hasattr(settings.telegram, "collapsible_details")
    assert settings.telegram.thinking_blocks is False
    assert settings.telegram.max_table_rows == 3
    assert not hasattr(settings.telegram, "details_open_on_failure")
    assert settings.telegram.compact_success is False
    assert settings.telegram.max_detail_items == 2


def test_load_settings_accepts_cleanup_config():
    settings = load_settings(
        {
            "progress_tail": {
                "cleanup": {
                    "auto_delete": True,
                    "delay_seconds": 2,
                    "delete_on_success": False,
                    "delete_on_failure": True,
                    "delete_background_active": True,
                }
            }
        }
    )

    assert settings.cleanup.auto_delete is True
    assert settings.cleanup.delay_seconds == 2
    assert settings.cleanup.delete_on_success is False
    assert settings.cleanup.delete_on_failure is True
    assert settings.cleanup.delete_background_active is True


def test_load_settings_accepts_renderer_agent_label():
    settings = load_settings({"progress_tail": {"renderer": {"agent_label": "Akbar"}}})

    assert settings.renderer.agent_label == "Akbar"


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
                        "background_jobs": False,
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
    assert platform.background_jobs_enabled is False
    assert not hasattr(platform, "code_fence")
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
    assert settings.defaults.edit_interval == 5.0
    assert resolve_platform_settings(settings, "sms").strategy == "off"
