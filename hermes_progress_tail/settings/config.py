from __future__ import annotations

from dataclasses import replace
from typing import Any

from .coercion import (
    as_bool,
    as_delegate_thinking,
    as_float,
    as_footer_density,
    as_int,
    as_patch_detail,
    as_strategy,
    as_style,
    renderer_mode_and_density,
)
from .schema import (
    BATCH_DEFAULT_OFF,
    PLATFORM_CONFIG_CONTRACT,
    PROGRESS_TAIL_CONFIG_CONTRACT,
    RETIRED_CONFIG_KEYS,
    SNAPSHOT_DEFAULTS,
)
from .types import (
    AssistantSettings,
    BackgroundJobSettings,
    CleanupSettings,
    DelegateSettings,
    FooterSettings,
    LegacyDefaultSettings,
    NativeGatewaySettings,
    NoEditSettings,
    PatchSettings,
    PlatformSettings,
    ReasoningSettings,
    RendererSettings,
    Settings,
    TelegramSettings,
    TodoSettings,
    ToolSettings,
)

__all__ = (
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
    "load_settings",
    "resolve_platform_settings",
    "find_unknown_config_keys",
    "find_retired_config_keys",
)


def _as_dict(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    section = config.get("progress_tail")
    if isinstance(section, dict):
        return section
    legacy = config.get("tool_progress_tail")
    if isinstance(legacy, dict):
        return _legacy_to_progress_tail(legacy)
    return config


def _config_section(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    section = config.get("progress_tail")
    if isinstance(section, dict):
        return section
    legacy = config.get("tool_progress_tail")
    if isinstance(legacy, dict):
        return _legacy_to_progress_tail(legacy)
    return config


def _walk_unknown_keys(raw: dict[str, Any], contract: dict[str, Any], prefix: str) -> list[str]:
    if not isinstance(raw, dict):
        return []
    unknown: list[str] = []
    for key, value in raw.items():
        path = f"{prefix}.{key}"
        if path in RETIRED_CONFIG_KEYS:
            continue
        expected = contract.get(key, "__missing__")
        if expected == "__missing__":
            unknown.append(path)
            continue
        if expected == "legacy_map":
            continue
        if expected == "platform_map":
            if not isinstance(value, dict):
                continue
            for platform, platform_raw in value.items():
                platform_path = f"{path}.{platform}"
                if not isinstance(platform_raw, dict):
                    continue
                for platform_key in platform_raw:
                    nested_path = f"{platform_path}.{platform_key}"
                    if nested_path in RETIRED_CONFIG_KEYS:
                        continue
                    if platform_key not in PLATFORM_CONFIG_CONTRACT:
                        unknown.append(nested_path)
            continue
        if isinstance(expected, dict) and isinstance(value, dict):
            unknown.extend(_walk_unknown_keys(value, expected, path))
    return unknown


def find_unknown_config_keys(config: dict[str, Any] | None) -> list[str]:
    section = _config_section(config)
    return sorted(_walk_unknown_keys(section, PROGRESS_TAIL_CONFIG_CONTRACT, "progress_tail"))


def find_retired_config_keys(config: dict[str, Any] | None) -> list[str]:
    section = _config_section(config)
    retired = []
    if isinstance(section.get("finalization"), dict):
        retired.append("progress_tail.finalization")
    background_jobs = section.get("background_jobs")
    if isinstance(background_jobs, dict) and "default_notify_on_complete" in background_jobs:
        retired.append("progress_tail.background_jobs.default_notify_on_complete")
    telegram = section.get("telegram")
    if isinstance(telegram, dict):
        if "collapsible_details" in telegram:
            retired.append("progress_tail.telegram.collapsible_details")
        if "details_open_on_failure" in telegram:
            retired.append("progress_tail.telegram.details_open_on_failure")
    return retired


def _legacy_to_progress_tail(section: dict[str, Any]) -> dict[str, Any]:
    defaults = section.get("defaults") if isinstance(section.get("defaults"), dict) else {}
    migrated = {
        "enabled": section.get("enabled", True),
        "tools": {
            "enabled": True,
            "lines": defaults.get("lines", 3),
            "preview_length": defaults.get("preview_length", 120),
            "show_completed": defaults.get("show_completed", True),
            "show_duration": defaults.get("show_duration", True),
            "timestamp": defaults.get("timestamp", True),
            "timestamp_format": defaults.get("timestamp_format", "%H:%M"),
        },
        "delegates": section.get("delegates", {}),
        "assistant": section.get("assistant", {}),
        "renderer": {
            "strategy": "auto",
            "edit_interval": defaults.get("edit_interval", 5.0),
            "stale_ttl_seconds": defaults.get("stale_ttl_seconds", 900),
            "redact_secrets": defaults.get("redact_secrets", True),
        },
        "no_edit": section.get("no_edit", {}),
        "platforms": section.get("platforms", {}),
    }
    return migrated


def load_settings(config: dict[str, Any] | None) -> Settings:
    defaults = Settings()
    section = _as_dict(config)
    legacy_defaults = section.get("defaults") if isinstance(section.get("defaults"), dict) else {}
    tools_raw = section.get("tools") if isinstance(section.get("tools"), dict) else legacy_defaults
    renderer_raw = (
        section.get("renderer") if isinstance(section.get("renderer"), dict) else legacy_defaults
    )
    delegates_raw = section.get("delegates") if isinstance(section.get("delegates"), dict) else {}
    assistant_raw = section.get("assistant") if isinstance(section.get("assistant"), dict) else {}
    reasoning_raw = section.get("reasoning") if isinstance(section.get("reasoning"), dict) else {}
    no_edit_raw = section.get("no_edit") if isinstance(section.get("no_edit"), dict) else {}
    todo_raw = section.get("todo") if isinstance(section.get("todo"), dict) else {}
    patch_raw = section.get("patch") if isinstance(section.get("patch"), dict) else {}
    background_raw = (
        section.get("background_jobs") if isinstance(section.get("background_jobs"), dict) else {}
    )
    native_gateway_raw = (
        section.get("native_gateway") if isinstance(section.get("native_gateway"), dict) else {}
    )
    cleanup_raw = section.get("cleanup") if isinstance(section.get("cleanup"), dict) else {}
    footer_raw = section.get("footer") if isinstance(section.get("footer"), dict) else {}
    telegram_raw = section.get("telegram") if isinstance(section.get("telegram"), dict) else {}
    tools = ToolSettings(
        enabled=as_bool(tools_raw.get("enabled"), defaults.tools.enabled),
        lines=as_int(tools_raw.get("lines"), defaults.tools.lines),
        preview_length=as_int(tools_raw.get("preview_length"), defaults.tools.preview_length),
        show_completed=as_bool(tools_raw.get("show_completed"), defaults.tools.show_completed),
        show_duration=as_bool(tools_raw.get("show_duration"), defaults.tools.show_duration),
        timestamp=as_bool(tools_raw.get("timestamp"), defaults.tools.timestamp),
        timestamp_format=str(tools_raw.get("timestamp_format") or defaults.tools.timestamp_format),
    )
    delegates = DelegateSettings(
        enabled=as_bool(delegates_raw.get("enabled"), defaults.delegates.enabled),
        max_delegates=as_int(delegates_raw.get("max_delegates"), defaults.delegates.max_delegates),
        lines_per_delegate=as_int(
            delegates_raw.get("lines_per_delegate"), defaults.delegates.lines_per_delegate
        ),
        max_goal_chars=as_int(
            delegates_raw.get("max_goal_chars"), defaults.delegates.max_goal_chars, min_value=12
        ),
        max_line_chars=as_int(
            delegates_raw.get("max_line_chars"), defaults.delegates.max_line_chars, min_value=24
        ),
        show_model=as_bool(delegates_raw.get("show_model"), defaults.delegates.show_model),
        show_tool_count=as_bool(
            delegates_raw.get("show_tool_count"), defaults.delegates.show_tool_count
        ),
        show_completion=as_bool(
            delegates_raw.get("show_completion"), defaults.delegates.show_completion
        ),
        completed_ttl_seconds=as_int(
            delegates_raw.get("completed_ttl_seconds"), defaults.delegates.completed_ttl_seconds
        ),
        thinking=as_delegate_thinking(delegates_raw.get("thinking"), defaults.delegates.thinking),
    )
    todo = TodoSettings(
        sticky=as_bool(todo_raw.get("sticky"), defaults.todo.sticky),
        hide_tool_line=as_bool(todo_raw.get("hide_tool_line"), defaults.todo.hide_tool_line),
        max_pending=as_int(todo_raw.get("max_pending"), defaults.todo.max_pending),
        max_completed=as_int(todo_raw.get("max_completed"), defaults.todo.max_completed),
        max_cancelled=as_int(todo_raw.get("max_cancelled"), defaults.todo.max_cancelled),
        max_item_chars=as_int(
            todo_raw.get("max_item_chars"), defaults.todo.max_item_chars, min_value=10
        ),
    )
    patch = PatchSettings(
        detail=as_patch_detail(patch_raw.get("detail"), defaults.patch.detail),
        preview_chars=as_int(
            patch_raw.get("preview_chars"), defaults.patch.preview_chars, min_value=10
        ),
        max_files=as_int(patch_raw.get("max_files"), defaults.patch.max_files),
    )
    assistant = AssistantSettings(
        enabled=as_bool(assistant_raw.get("enabled"), defaults.assistant.enabled),
        max_lines=as_int(assistant_raw.get("max_lines"), defaults.assistant.max_lines),
        max_chars=as_int(assistant_raw.get("max_chars"), defaults.assistant.max_chars),
        min_update_chars=as_int(
            assistant_raw.get("min_update_chars"), defaults.assistant.min_update_chars
        ),
    )
    reasoning = ReasoningSettings(
        enabled=as_bool(reasoning_raw.get("enabled"), defaults.reasoning.enabled),
        max_lines=as_int(reasoning_raw.get("max_lines"), defaults.reasoning.max_lines),
        max_chars=as_int(reasoning_raw.get("max_chars"), defaults.reasoning.max_chars),
        min_update_chars=as_int(
            reasoning_raw.get("min_update_chars"), defaults.reasoning.min_update_chars
        ),
        no_edit_strategy=as_strategy(
            reasoning_raw.get("no_edit_strategy"), defaults.reasoning.no_edit_strategy
        ),
    )
    background_jobs = BackgroundJobSettings(
        enabled=as_bool(background_raw.get("enabled"), defaults.background_jobs.enabled),
        list_running=as_bool(
            background_raw.get("list_running"), defaults.background_jobs.list_running
        ),
        show_completed=as_bool(
            background_raw.get("show_completed"), defaults.background_jobs.show_completed
        ),
        completed_ttl_seconds=as_int(
            background_raw.get("completed_ttl_seconds"),
            defaults.background_jobs.completed_ttl_seconds,
        ),
        max_jobs=as_int(background_raw.get("max_jobs"), defaults.background_jobs.max_jobs),
        head_lines=as_int(background_raw.get("head_lines"), defaults.background_jobs.head_lines),
        tail_lines=as_int(background_raw.get("tail_lines"), defaults.background_jobs.tail_lines),
        max_line_chars=as_int(
            background_raw.get("max_line_chars"),
            defaults.background_jobs.max_line_chars,
            min_value=24,
        ),
        update_interval_seconds=as_int(
            background_raw.get("update_interval_seconds"),
            defaults.background_jobs.update_interval_seconds,
        ),
        suppress_native_notify=as_bool(
            background_raw.get("suppress_native_notify"),
            defaults.background_jobs.suppress_native_notify,
        ),
        suppress_watch_notifications=as_bool(
            background_raw.get("suppress_watch_notifications"),
            defaults.background_jobs.suppress_watch_notifications,
        ),
        default_notify_on_complete=as_bool(
            background_raw.get("default_notify_on_complete"),
            defaults.background_jobs.default_notify_on_complete,
        ),
    )
    native_gateway = NativeGatewaySettings(
        suppress=as_bool(native_gateway_raw.get("suppress"), defaults.native_gateway.suppress)
    )
    cleanup = CleanupSettings(
        auto_delete=as_bool(cleanup_raw.get("auto_delete"), defaults.cleanup.auto_delete),
        delay_seconds=as_int(
            cleanup_raw.get("delay_seconds"), defaults.cleanup.delay_seconds, min_value=0
        ),
        delete_on_success=as_bool(
            cleanup_raw.get("delete_on_success"), defaults.cleanup.delete_on_success
        ),
        delete_on_failure=as_bool(
            cleanup_raw.get("delete_on_failure"), defaults.cleanup.delete_on_failure
        ),
        delete_background_active=as_bool(
            cleanup_raw.get("delete_background_active"), defaults.cleanup.delete_background_active
        ),
    )
    footer = FooterSettings(
        enabled=as_bool(footer_raw.get("enabled"), defaults.footer.enabled),
        density=as_footer_density(footer_raw.get("density"), defaults.footer.density),
        max_path_chars=as_int(
            footer_raw.get("max_path_chars"), defaults.footer.max_path_chars, min_value=16
        ),
    )
    telegram = TelegramSettings(
        rich_messages=as_bool(telegram_raw.get("rich_messages"), defaults.telegram.rich_messages),
        verification_table=as_bool(
            telegram_raw.get("verification_table"), defaults.telegram.verification_table
        ),
        thinking_blocks=as_bool(
            telegram_raw.get("thinking_blocks"), defaults.telegram.thinking_blocks
        ),
        max_table_rows=as_int(telegram_raw.get("max_table_rows"), defaults.telegram.max_table_rows),
        compact_success=as_bool(
            telegram_raw.get("compact_success"), defaults.telegram.compact_success
        ),
        max_detail_items=as_int(
            telegram_raw.get("max_detail_items"), defaults.telegram.max_detail_items, min_value=0
        ),
    )
    renderer_mode, renderer_density = renderer_mode_and_density(
        renderer_raw, defaults.renderer.mode, defaults.renderer.density
    )
    renderer = RendererSettings(
        strategy=as_strategy(renderer_raw.get("strategy"), defaults.renderer.strategy),
        edit_interval=as_float(renderer_raw.get("edit_interval"), defaults.renderer.edit_interval),
        stale_ttl_seconds=as_int(
            renderer_raw.get("stale_ttl_seconds"), defaults.renderer.stale_ttl_seconds
        ),
        redact_secrets=as_bool(
            renderer_raw.get("redact_secrets"), defaults.renderer.redact_secrets
        ),
        mode=renderer_mode,
        style=as_style(renderer_raw.get("style"), defaults.renderer.style),
        density=renderer_density,
        agent_label=str(renderer_raw.get("agent_label") or defaults.renderer.agent_label).strip(),
    )
    no_edit = NoEditSettings(
        interval_seconds=as_int(
            no_edit_raw.get("interval_seconds"), defaults.no_edit.interval_seconds
        ),
        min_new_events=as_int(no_edit_raw.get("min_new_events"), defaults.no_edit.min_new_events),
        final_summary=as_bool(no_edit_raw.get("final_summary"), defaults.no_edit.final_summary),
        max_snapshots_per_turn=as_int(
            no_edit_raw.get("max_snapshots_per_turn"), defaults.no_edit.max_snapshots_per_turn
        ),
    )
    platforms = section.get("platforms") if isinstance(section.get("platforms"), dict) else {}
    return Settings(
        enabled=as_bool(section.get("enabled"), defaults.enabled),
        tools=tools,
        delegates=delegates,
        todo=todo,
        patch=patch,
        assistant=assistant,
        reasoning=reasoning,
        background_jobs=background_jobs,
        native_gateway=native_gateway,
        cleanup=cleanup,
        footer=footer,
        telegram=telegram,
        renderer=renderer,
        no_edit=no_edit,
        platforms=platforms,
    )


def resolve_platform_settings(settings: Settings, platform: str) -> PlatformSettings:
    default_strategy = (
        "off"
        if platform in BATCH_DEFAULT_OFF
        else ("snapshot" if platform in SNAPSHOT_DEFAULTS else settings.renderer.strategy)
    )
    base = PlatformSettings(
        enabled=settings.enabled and platform not in BATCH_DEFAULT_OFF,
        strategy=default_strategy,
        lines=settings.tools.lines,
        preview_length=settings.tools.preview_length,
        edit_interval=settings.renderer.edit_interval,
        stale_ttl_seconds=settings.renderer.stale_ttl_seconds,
        redact_secrets=settings.renderer.redact_secrets,
        show_completed=settings.tools.show_completed,
        tools_enabled=settings.tools.enabled,
        assistant_enabled=settings.assistant.enabled,
        reasoning_enabled=settings.reasoning.enabled,
        delegates_enabled=settings.delegates.enabled,
        background_jobs_enabled=settings.background_jobs.enabled,
        timestamp=settings.tools.timestamp,
        timestamp_format=settings.tools.timestamp_format,
    )
    raw = (settings.platforms or {}).get(platform, {})
    if not isinstance(raw, dict):
        return base
    return replace(
        base,
        enabled=as_bool(raw.get("enabled"), base.enabled),
        strategy=as_strategy(raw.get("strategy"), base.strategy),
        lines=as_int(raw.get("lines"), base.lines),
        preview_length=as_int(raw.get("preview_length"), base.preview_length),
        edit_interval=as_float(raw.get("edit_interval"), base.edit_interval),
        stale_ttl_seconds=as_int(raw.get("stale_ttl_seconds"), base.stale_ttl_seconds),
        redact_secrets=as_bool(raw.get("redact_secrets"), base.redact_secrets),
        show_completed=as_bool(raw.get("show_completed"), base.show_completed),
        tools_enabled=as_bool(raw.get("tools", raw.get("tools_enabled")), base.tools_enabled),
        assistant_enabled=as_bool(
            raw.get("assistant", raw.get("assistant_enabled")), base.assistant_enabled
        ),
        reasoning_enabled=as_bool(
            raw.get("reasoning", raw.get("reasoning_enabled")), base.reasoning_enabled
        ),
        delegates_enabled=as_bool(
            raw.get("delegates", raw.get("delegates_enabled")), base.delegates_enabled
        ),
        background_jobs_enabled=as_bool(
            raw.get("background_jobs", raw.get("background_jobs_enabled")),
            base.background_jobs_enabled,
        ),
        timestamp=as_bool(raw.get("timestamp"), base.timestamp),
        timestamp_format=str(raw.get("timestamp_format") or base.timestamp_format),
    )
