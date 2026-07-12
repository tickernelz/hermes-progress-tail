from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal

from .schema import (
    BATCH_DEFAULT_OFF,
    PLATFORM_CONFIG_CONTRACT,
    PROGRESS_TAIL_CONFIG_CONTRACT,
    RETIRED_CONFIG_KEYS,
    SNAPSHOT_DEFAULTS,
    VALID_STRATEGIES,
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


def _bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def _int(value: Any, default: int, min_value: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= min_value else default


def _float(value: Any, default: float, min_value: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > min_value else default


def _strategy(value: Any, default: str = "auto") -> str:
    val = str(value or default).strip().lower()
    return val if val in VALID_STRATEGIES else default


def _style(value: Any, default: str = "emoji") -> Literal["emoji", "plain"]:
    val = str(value or default).strip().lower()
    return "plain" if val == "plain" else "emoji"


def _density(
    value: Any, default: str = "normal"
) -> Literal["compact", "normal", "verbose", "debug"]:
    val = str(value or default).strip().lower()
    return val if val in {"compact", "normal", "verbose", "debug"} else "normal"


def _footer_density(value: Any, default: str = "normal") -> Literal["compact", "normal", "debug"]:
    val = str(value or default).strip().lower()
    return val if val in {"compact", "normal", "debug"} else "normal"


def _renderer_mode_and_density(
    raw: dict[str, Any],
) -> tuple[str, Literal["compact", "normal", "verbose", "debug"]]:
    mode = str(raw.get("mode") or "sectioned").strip().lower() or "sectioned"
    density = _density(raw.get("density"), "normal")
    if mode == "compact":
        return "sectioned", "compact"
    if mode not in {"focused", "sectioned"}:
        return "sectioned", density
    return mode, density


def _patch_detail(value: Any, default: str = "smart") -> str:
    val = str(value or default).strip().lower()
    return val if val in {"off", "path", "smart", "stats"} else default


def _delegate_thinking(value: Any, default: str = "off") -> Literal["off", "summary"]:
    val = str(value or default).strip().lower()
    return "summary" if val == "summary" else "off"


def load_settings(config: dict[str, Any] | None) -> Settings:
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
        enabled=_bool(tools_raw.get("enabled"), True),
        lines=_int(tools_raw.get("lines"), 3),
        preview_length=_int(tools_raw.get("preview_length"), 120),
        show_completed=_bool(tools_raw.get("show_completed"), True),
        show_duration=_bool(tools_raw.get("show_duration"), True),
        timestamp=_bool(tools_raw.get("timestamp"), True),
        timestamp_format=str(tools_raw.get("timestamp_format") or "%H:%M"),
    )
    delegates = DelegateSettings(
        enabled=_bool(delegates_raw.get("enabled"), True),
        max_delegates=_int(delegates_raw.get("max_delegates"), 4),
        lines_per_delegate=_int(delegates_raw.get("lines_per_delegate"), 2),
        max_goal_chars=_int(delegates_raw.get("max_goal_chars"), 48, min_value=12),
        max_line_chars=_int(delegates_raw.get("max_line_chars"), 120, min_value=24),
        show_model=_bool(delegates_raw.get("show_model"), False),
        show_tool_count=_bool(delegates_raw.get("show_tool_count"), True),
        show_completion=_bool(delegates_raw.get("show_completion"), True),
        completed_ttl_seconds=_int(delegates_raw.get("completed_ttl_seconds"), 5),
        thinking=_delegate_thinking(delegates_raw.get("thinking"), "off"),
    )
    todo = TodoSettings(
        sticky=_bool(todo_raw.get("sticky"), True),
        hide_tool_line=_bool(todo_raw.get("hide_tool_line"), True),
        max_pending=_int(todo_raw.get("max_pending"), 3),
        max_completed=_int(todo_raw.get("max_completed"), 3),
        max_cancelled=_int(todo_raw.get("max_cancelled"), 2),
        max_item_chars=_int(todo_raw.get("max_item_chars"), 40, min_value=10),
    )
    patch = PatchSettings(
        detail=_patch_detail(patch_raw.get("detail"), "smart"),
        preview_chars=_int(patch_raw.get("preview_chars"), 48, min_value=10),
        max_files=_int(patch_raw.get("max_files"), 3),
    )
    assistant = AssistantSettings(
        enabled=_bool(assistant_raw.get("enabled"), True),
        max_lines=_int(assistant_raw.get("max_lines"), 3),
        max_chars=_int(assistant_raw.get("max_chars"), 500),
        min_update_chars=_int(assistant_raw.get("min_update_chars"), 160),
    )
    reasoning = ReasoningSettings(
        enabled=_bool(reasoning_raw.get("enabled"), True),
        max_lines=_int(reasoning_raw.get("max_lines"), 3),
        max_chars=_int(reasoning_raw.get("max_chars"), 600),
        min_update_chars=_int(reasoning_raw.get("min_update_chars"), 300),
        no_edit_strategy=_strategy(reasoning_raw.get("no_edit_strategy"), "off"),
    )
    background_jobs = BackgroundJobSettings(
        enabled=_bool(background_raw.get("enabled"), True),
        list_running=_bool(background_raw.get("list_running"), True),
        show_completed=_bool(background_raw.get("show_completed"), True),
        completed_ttl_seconds=_int(background_raw.get("completed_ttl_seconds"), 5),
        max_jobs=_int(background_raw.get("max_jobs"), 4),
        head_lines=_int(background_raw.get("head_lines"), 2),
        tail_lines=_int(background_raw.get("tail_lines"), 3),
        max_line_chars=_int(background_raw.get("max_line_chars"), 120, min_value=24),
        update_interval_seconds=_int(background_raw.get("update_interval_seconds"), 10),
        suppress_native_notify=_bool(background_raw.get("suppress_native_notify"), True),
        suppress_watch_notifications=_bool(
            background_raw.get("suppress_watch_notifications"), True
        ),
        default_notify_on_complete=_bool(background_raw.get("default_notify_on_complete"), False),
    )
    native_gateway = NativeGatewaySettings(suppress=_bool(native_gateway_raw.get("suppress"), True))
    cleanup = CleanupSettings(
        auto_delete=_bool(cleanup_raw.get("auto_delete"), False),
        delay_seconds=_int(cleanup_raw.get("delay_seconds"), 5, min_value=0),
        delete_on_success=_bool(cleanup_raw.get("delete_on_success"), True),
        delete_on_failure=_bool(cleanup_raw.get("delete_on_failure"), False),
        delete_background_active=_bool(cleanup_raw.get("delete_background_active"), False),
    )
    footer = FooterSettings(
        enabled=_bool(footer_raw.get("enabled"), True),
        density=_footer_density(footer_raw.get("density"), "normal"),
        max_path_chars=_int(footer_raw.get("max_path_chars"), 56, min_value=16),
    )
    telegram = TelegramSettings(
        rich_messages=_bool(telegram_raw.get("rich_messages"), True),
        verification_table=_bool(telegram_raw.get("verification_table"), True),
        thinking_blocks=_bool(telegram_raw.get("thinking_blocks"), True),
        max_table_rows=_int(telegram_raw.get("max_table_rows"), 8),
        compact_success=_bool(telegram_raw.get("compact_success"), True),
        max_detail_items=_int(telegram_raw.get("max_detail_items"), 8, min_value=0),
    )
    renderer_mode, renderer_density = _renderer_mode_and_density(renderer_raw)
    renderer = RendererSettings(
        strategy=_strategy(renderer_raw.get("strategy"), "auto"),
        edit_interval=_float(renderer_raw.get("edit_interval"), 5.0),
        stale_ttl_seconds=_int(renderer_raw.get("stale_ttl_seconds"), 900),
        redact_secrets=_bool(renderer_raw.get("redact_secrets"), True),
        mode=renderer_mode,
        style=_style(renderer_raw.get("style"), "emoji"),
        density=renderer_density,
        agent_label=str(renderer_raw.get("agent_label") or "").strip(),
    )
    no_edit = NoEditSettings(
        interval_seconds=_int(no_edit_raw.get("interval_seconds"), 30),
        min_new_events=_int(no_edit_raw.get("min_new_events"), 3),
        final_summary=_bool(no_edit_raw.get("final_summary"), True),
        max_snapshots_per_turn=_int(no_edit_raw.get("max_snapshots_per_turn"), 5),
    )
    platforms = section.get("platforms") if isinstance(section.get("platforms"), dict) else {}
    return Settings(
        enabled=_bool(section.get("enabled"), True),
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
        enabled=_bool(raw.get("enabled"), base.enabled),
        strategy=_strategy(raw.get("strategy"), base.strategy),
        lines=_int(raw.get("lines"), base.lines),
        preview_length=_int(raw.get("preview_length"), base.preview_length),
        edit_interval=_float(raw.get("edit_interval"), base.edit_interval),
        stale_ttl_seconds=_int(raw.get("stale_ttl_seconds"), base.stale_ttl_seconds),
        redact_secrets=_bool(raw.get("redact_secrets"), base.redact_secrets),
        show_completed=_bool(raw.get("show_completed"), base.show_completed),
        tools_enabled=_bool(raw.get("tools", raw.get("tools_enabled")), base.tools_enabled),
        assistant_enabled=_bool(
            raw.get("assistant", raw.get("assistant_enabled")), base.assistant_enabled
        ),
        reasoning_enabled=_bool(
            raw.get("reasoning", raw.get("reasoning_enabled")), base.reasoning_enabled
        ),
        delegates_enabled=_bool(
            raw.get("delegates", raw.get("delegates_enabled")), base.delegates_enabled
        ),
        background_jobs_enabled=_bool(
            raw.get("background_jobs", raw.get("background_jobs_enabled")),
            base.background_jobs_enabled,
        ),
        timestamp=_bool(raw.get("timestamp"), base.timestamp),
        timestamp_format=str(raw.get("timestamp_format") or base.timestamp_format),
    )
