from copy import deepcopy
from dataclasses import asdict, fields
from importlib import import_module

import pytest

import hermes_progress_tail.config as config_facade
import hermes_progress_tail.settings.config as settings_config
from hermes_progress_tail.config import (
    Settings,
    find_retired_config_keys,
    find_unknown_config_keys,
    load_settings,
)

EXPECTED_SETTINGS_DEFAULTS = {
    "enabled": True,
    "tools": {
        "enabled": True,
        "lines": 3,
        "preview_length": 120,
        "show_completed": True,
        "show_duration": True,
        "timestamp": True,
        "timestamp_format": "%H:%M",
    },
    "delegates": {
        "enabled": True,
        "max_delegates": 4,
        "lines_per_delegate": 2,
        "max_goal_chars": 48,
        "max_line_chars": 120,
        "show_model": False,
        "show_tool_count": True,
        "show_completion": True,
        "completed_ttl_seconds": 5,
        "thinking": "off",
    },
    "todo": {
        "sticky": True,
        "hide_tool_line": True,
        "max_pending": 3,
        "max_completed": 3,
        "max_cancelled": 2,
        "max_item_chars": 40,
    },
    "patch": {"detail": "smart", "preview_chars": 48, "max_files": 3},
    "assistant": {"enabled": True, "max_lines": 3, "max_chars": 500, "min_update_chars": 160},
    "reasoning": {
        "enabled": True,
        "max_lines": 3,
        "max_chars": 600,
        "min_update_chars": 300,
        "no_edit_strategy": "off",
    },
    "background_jobs": {
        "enabled": True,
        "list_running": True,
        "show_completed": True,
        "completed_ttl_seconds": 5,
        "max_jobs": 4,
        "head_lines": 2,
        "tail_lines": 3,
        "max_line_chars": 120,
        "update_interval_seconds": 10,
        "suppress_native_notify": True,
        "suppress_watch_notifications": True,
        "default_notify_on_complete": False,
    },
    "native_gateway": {"suppress": True},
    "cleanup": {
        "auto_delete": False,
        "delay_seconds": 5,
        "delete_on_success": True,
        "delete_on_failure": False,
        "delete_background_active": False,
    },
    "footer": {"enabled": True, "density": "normal", "max_path_chars": 56},
    "telegram": {
        "rich_messages": True,
        "verification_table": True,
        "thinking_blocks": True,
        "max_table_rows": 8,
        "compact_success": True,
        "max_detail_items": 8,
    },
    "renderer": {
        "strategy": "auto",
        "edit_interval": 5.0,
        "stale_ttl_seconds": 900,
        "redact_secrets": True,
        "mode": "sectioned",
        "style": "emoji",
        "density": "normal",
        "agent_label": "",
    },
    "no_edit": {
        "interval_seconds": 30,
        "min_new_events": 3,
        "final_summary": True,
        "max_snapshots_per_turn": 5,
    },
    "platforms": None,
}

EXPECTED_SETTINGS_TYPE_ALL = (
    "ToolSettings",
    "DelegateSettings",
    "TodoSettings",
    "PatchSettings",
    "AssistantSettings",
    "ReasoningSettings",
    "BackgroundJobSettings",
    "NativeGatewaySettings",
    "CleanupSettings",
    "FooterSettings",
    "TelegramSettings",
    "LegacyDefaultSettings",
    "RendererSettings",
    "NoEditSettings",
    "PlatformSettings",
    "Settings",
)

EXPECTED_SETTINGS_CONFIG_ALL = EXPECTED_SETTINGS_TYPE_ALL + (
    "load_settings",
    "resolve_platform_settings",
    "find_unknown_config_keys",
    "find_retired_config_keys",
)

EXPECTED_SETTINGS_FIELDS = (
    "enabled",
    "tools",
    "delegates",
    "todo",
    "patch",
    "assistant",
    "reasoning",
    "background_jobs",
    "native_gateway",
    "cleanup",
    "footer",
    "telegram",
    "renderer",
    "no_edit",
    "platforms",
)

NESTED_SETTINGS_FIELDS = EXPECTED_SETTINGS_FIELDS[1:-1]

EXPECTED_RUNTIME_PLUGIN_ALL = (
    "Path",
    "SessionContext",
    "VERSION",
    "_ASSISTANT_CAPTURE",
    "_SourceThreadOverride",
    "_adapter_for",
    "_agent_cwd",
    "_agent_session_id",
    "_agent_session_key",
    "_agent_string",
    "_agent_system_prompt",
    "_assistant_tail_enabled",
    "_background_job_config_warnings",
    "_background_job_event_is_terminal",
    "_binding_is_stale_for_entry",
    "_binding_session_id",
    "_bound_telegram_topic_session_id",
    "_branch_count",
    "_compact_count",
    "_compact_result_status",
    "_compression_lifecycle_completed_text",
    "_compression_status_tail_text",
    "_context_for",
    "_context_for_non_background_thread",
    "_context_owned_by_current_thread",
    "_context_tokens",
    "_demo_command",
    "_duration_text",
    "_estimate_request_tokens",
    "_feature_enabled",
    "_finalize_target_context",
    "_float_kw",
    "_get_renderer",
    "_get_session_entry",
    "_git_command",
    "_git_snapshot",
    "_int_kw",
    "_is_background_review_agent",
    "_is_background_review_thread",
    "_is_context_owner_thread",
    "_is_telegram_dm_source",
    "_json_obj",
    "_load_git_snapshot",
    "_load_runtime_config",
    "_load_runtime_settings",
    "_on_post_llm_call",
    "_on_post_tool_call",
    "_on_pre_gateway_dispatch",
    "_on_pre_tool_call",
    "_on_session_finalize",
    "_on_session_reset",
    "_positive_attr",
    "_positive_int",
    "_positive_int_kw",
    "_pre_gateway_session_context",
    "_progress_tail_enabled",
    "_reactivate_foreground_context",
    "_record_assistant_capture",
    "_register_context",
    "_replace_environment_cwd",
    "_resolve_tool_agent",
    "_runtime_profile_name",
    "_schedule_background_job_cleanup",
    "_schedule_background_job_poll",
    "_schedule_finalize",
    "_schedule_render",
    "_session_key",
    "_should_suppress_agent_progress",
    "_source_with_thread_id",
    "_suppress_native_background_notify",
    "_telegram_general_topic_ids",
    "_telegram_topic_binding",
    "_terminal_background_requested",
    "_terminal_live_cwd",
    "_timestamp_seconds",
    "_tool_agent_context",
    "_tool_context_lookup_ids",
    "_topic_recovered_source",
    "_update_environment_from_agent",
    "_update_environment_from_terminal",
    "install_monkeypatches",
    "on_assistant_progress_from_agent",
    "on_compression_lifecycle_from_agent",
    "on_compression_status_from_agent",
    "on_delegate_progress_from_agent",
    "on_gateway_stop_from_runner",
    "on_reasoning_delta_from_agent",
    "register",
    "register_context_from_adapter_event",
    "threading",
)


def test_settings_and_every_nested_default_are_frozen():
    settings = Settings()
    assert asdict(settings) == EXPECTED_SETTINGS_DEFAULTS
    assert asdict(settings.defaults) == {
        "lines": 3,
        "preview_length": 120,
        "edit_interval": 5.0,
        "stale_ttl_seconds": 900,
        "redact_secrets": True,
        "show_completed": True,
    }


@pytest.mark.parametrize("field_name", NESTED_SETTINGS_FIELDS)
def test_nested_settings_defaults_are_equal_but_independently_owned(field_name):
    first = Settings()
    second = Settings()

    assert getattr(first, field_name) == getattr(second, field_name)
    assert getattr(first, field_name) is not getattr(second, field_name)


def test_settings_field_surface_and_keyword_construction_are_stable():
    default = Settings()
    assert tuple(field.name for field in fields(Settings)) == EXPECTED_SETTINGS_FIELDS

    kwargs = {field.name: getattr(default, field.name) for field in fields(Settings)}
    assert Settings(**kwargs) == default


@pytest.mark.parametrize(
    ("path", "value", "expected"),
    [
        (("enabled",), None, True),
        (("enabled",), 0, False),
        (("enabled",), "YES", True),
        (("enabled",), "false", False),
        (("enabled",), "unexpected", False),
        (("tools", "lines"), True, 1),
        (("tools", "lines"), False, 3),
        (("tools", "lines"), "bad", 3),
        (("tools", "lines"), 0, 3),
        (("delegates", "max_goal_chars"), 11, 48),
        (("delegates", "max_goal_chars"), 12, 12),
        (("cleanup", "delay_seconds"), 0, 0),
        (("cleanup", "delay_seconds"), -1, 5),
        (("telegram", "max_detail_items"), 0, 0),
        (("renderer", "edit_interval"), "bad", 5.0),
        (("renderer", "edit_interval"), 0, 5.0),
        (("renderer", "edit_interval"), "0.1", 0.1),
        (("renderer", "style"), " PLAIN ", "plain"),
        (("renderer", "style"), "unknown", "emoji"),
        (("renderer", "density"), " VERBOSE ", "verbose"),
    ],
)
def test_current_coercion_boundaries(path, value, expected):
    raw = value
    for key in reversed(path):
        raw = {key: raw}
    settings = load_settings({"progress_tail": raw})
    actual = settings
    for key in path:
        actual = getattr(actual, key)
    assert actual == expected


def test_renderer_compact_mode_normalizes_mode_and_density():
    renderer = load_settings(
        {"progress_tail": {"renderer": {"mode": " COMPACT ", "density": "debug"}}}
    ).renderer
    assert (renderer.mode, renderer.density) == ("sectioned", "compact")


@pytest.mark.parametrize(
    ("config", "expected_lines"),
    [
        ({"progress_tail": {"tools": {"lines": 7}}}, 7),
        ({"tool_progress_tail": {"defaults": {"lines": 6}}}, 6),
        ({"tools": {"lines": 5}}, 5),
        ({"progress_tail": "malformed", "tools": {"lines": 4}}, 4),
        (
            {
                "progress_tail": {"tools": {"lines": 8}},
                "tool_progress_tail": {"defaults": {"lines": 9}},
            },
            8,
        ),
    ],
)
def test_settings_extraction_precedence(config, expected_lines):
    assert load_settings(config).tools.lines == expected_lines


@pytest.mark.parametrize(
    "config",
    [
        {
            "progress_tail": {
                "tools": {"lines": 5, "unknown": [1, {"nested": True}]},
                "finalization": {"delete_on_success": True},
            }
        },
        {
            "tool_progress_tail": {
                "defaults": {"lines": 6},
                "unknown": [1, {"nested": True}],
            }
        },
        {
            "tools": {"lines": 4, "unknown": [1, {"nested": True}]},
            "finalization": {"delete_on_success": True},
        },
        {
            "progress_tail": ["malformed", {"nested": True}],
            "tools": {"lines": 3, "unknown": {"nested": True}},
        },
        {
            "progress_tail": {"tools": {"lines": 8, "unknown": {"nested": True}}},
            "tool_progress_tail": {
                "defaults": {"lines": 9},
                "finalization": {"delete_on_success": True},
            },
        },
    ],
    ids=(
        "current-nested",
        "legacy-nested",
        "bare",
        "malformed-current-wrapper",
        "simultaneous-current-and-legacy",
    ),
)
def test_loading_and_diagnostics_do_not_mutate_inputs(config):
    before = deepcopy(config)
    load_settings(config)
    assert config == before
    find_unknown_config_keys(config)
    assert config == before
    find_retired_config_keys(config)
    assert config == before


def test_config_facade_is_the_settings_config_module_and_objects_are_identical():
    assert config_facade is settings_config
    for name in (
        "Settings",
        "load_settings",
        "resolve_platform_settings",
        "find_unknown_config_keys",
        "find_retired_config_keys",
    ):
        assert getattr(config_facade, name) is getattr(settings_config, name)


def test_settings_types_are_canonical_and_exports_are_exact():
    settings_types = import_module("hermes_progress_tail.settings.types")
    assert tuple(settings_types.__all__) == EXPECTED_SETTINGS_TYPE_ALL
    assert tuple(settings_config.__all__) == EXPECTED_SETTINGS_CONFIG_ALL
    for name in EXPECTED_SETTINGS_TYPE_ALL:
        canonical = getattr(settings_types, name)
        assert getattr(settings_config, name) is canonical
        assert getattr(config_facade, name) is canonical


@pytest.mark.parametrize(
    ("facade_name", "implementation_name", "primary_objects"),
    [
        ("plugin", "runtime.plugin", ("register", "VERSION")),
        ("renderer", "rendering.renderer", ("ProgressRenderer",)),
        ("formatter", "rendering.formatter", ("format_tool_line",)),
        ("monkeypatches", "hooks.monkeypatches", ("install_monkeypatches",)),
        ("state", "models.state", ("SessionContext",)),
        ("compat", "gateway.compat", ("adapter_supports_edit", "platform_name")),
        ("installer", "cli.installer", ("install", "InstallResult")),
        ("redaction", "utils.redaction", ("redact_text",)),
        ("text_utils", "utils.text", ("truncate_text", "truncate_tail_text")),
        ("delegate_renderer", "rendering.delegate", ("DelegateProgressRenderer",)),
    ],
)
def test_design_documented_compatibility_facades_preserve_primary_objects(
    facade_name, implementation_name, primary_objects
):
    facade = import_module(f"hermes_progress_tail.{facade_name}")
    implementation = import_module(f"hermes_progress_tail.{implementation_name}")
    for name in primary_objects:
        assert getattr(facade, name) is getattr(implementation, name)


def test_runtime_plugin_all_exact_pre_d_order_and_private_surface():
    plugin = import_module("hermes_progress_tail.runtime.plugin")
    actual = tuple(plugin.__all__)
    assert actual == EXPECTED_RUNTIME_PLUGIN_ALL
    assert not set(EXPECTED_RUNTIME_PLUGIN_ALL) - set(actual)
    assert {name for name in actual if name.startswith("_")} == {
        name for name in EXPECTED_RUNTIME_PLUGIN_ALL if name.startswith("_")
    }
