from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

VALID_STRATEGIES = {"auto", "live_tail", "snapshot", "summary_only", "off"}
BATCH_DEFAULT_OFF = {"email", "sms", "webhook", "homeassistant"}
SNAPSHOT_DEFAULTS = {
    "slack",
    "signal",
    "bluebubbles",
    "weixin",
    "wecom",
    "wecom_callback",
    "dingtalk",
    "irc",
}


@dataclass(frozen=True)
class ToolSettings:
    enabled: bool = True
    lines: int = 3
    preview_length: int = 120
    show_completed: bool = False
    show_duration: bool = True
    timestamp: bool = True
    timestamp_format: str = "%H:%M"


@dataclass(frozen=True)
class TodoSettings:
    sticky: bool = True
    hide_tool_line: bool = True
    max_pending: int = 3
    max_completed: int = 3
    max_cancelled: int = 2
    max_item_chars: int = 40


@dataclass(frozen=True)
class PatchSettings:
    detail: str = "smart"
    preview_chars: int = 48
    max_files: int = 3


@dataclass(frozen=True)
class ReasoningSettings:
    enabled: bool = True
    max_lines: int = 3
    max_chars: int = 600
    min_update_chars: int = 80
    no_edit_strategy: str = "off"
    capture_inline_think_tags: bool = False


@dataclass(frozen=True)
class LegacyDefaultSettings:
    lines: int = 3
    preview_length: int = 120
    edit_interval: float = 1.5
    stale_ttl_seconds: int = 900
    redact_secrets: bool = True
    show_completed: bool = False


@dataclass(frozen=True)
class RendererSettings:
    strategy: str = "auto"
    edit_interval: float = 1.5
    stale_ttl_seconds: int = 900
    redact_secrets: bool = True
    mode: str = "sectioned"
    style: Literal["emoji", "plain"] = "emoji"
    density: Literal["compact", "normal", "debug"] = "normal"


@dataclass(frozen=True)
class NoEditSettings:
    interval_seconds: int = 30
    min_new_events: int = 3
    final_summary: bool = True
    max_snapshots_per_turn: int = 5


@dataclass(frozen=True)
class PlatformSettings:
    enabled: bool = True
    strategy: str = "auto"
    lines: int = 3
    preview_length: int = 120
    edit_interval: float = 1.5
    stale_ttl_seconds: int = 900
    redact_secrets: bool = True
    show_completed: bool = False
    tools_enabled: bool = True
    reasoning_enabled: bool = True
    timestamp: bool = True
    timestamp_format: str = "%H:%M"


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    tools: ToolSettings = ToolSettings()
    todo: TodoSettings = TodoSettings()
    patch: PatchSettings = PatchSettings()
    reasoning: ReasoningSettings = ReasoningSettings()
    renderer: RendererSettings = RendererSettings()
    no_edit: NoEditSettings = NoEditSettings()
    platforms: dict[str, dict[str, Any]] | None = None

    @property
    def defaults(self) -> LegacyDefaultSettings:
        return LegacyDefaultSettings(
            lines=self.tools.lines,
            preview_length=self.tools.preview_length,
            edit_interval=self.renderer.edit_interval,
            stale_ttl_seconds=self.renderer.stale_ttl_seconds,
            redact_secrets=self.renderer.redact_secrets,
            show_completed=self.tools.show_completed,
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


def _legacy_to_progress_tail(section: dict[str, Any]) -> dict[str, Any]:
    defaults = section.get("defaults") if isinstance(section.get("defaults"), dict) else {}
    migrated = {
        "enabled": section.get("enabled", True),
        "tools": {
            "enabled": True,
            "lines": defaults.get("lines", 3),
            "preview_length": defaults.get("preview_length", 120),
            "show_completed": defaults.get("show_completed", False),
            "show_duration": defaults.get("show_duration", True),
            "timestamp": defaults.get("timestamp", True),
            "timestamp_format": defaults.get("timestamp_format", "%H:%M"),
        },
        "renderer": {
            "strategy": "auto",
            "edit_interval": defaults.get("edit_interval", 1.5),
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


def _density(value: Any, default: str = "normal") -> Literal["compact", "normal", "debug"]:
    val = str(value or default).strip().lower()
    return val if val in {"compact", "normal", "debug"} else "normal"


def _patch_detail(value: Any, default: str = "smart") -> str:
    val = str(value or default).strip().lower()
    return val if val in {"off", "path", "smart", "stats"} else default


def load_settings(config: dict[str, Any] | None) -> Settings:
    section = _as_dict(config)
    legacy_defaults = section.get("defaults") if isinstance(section.get("defaults"), dict) else {}
    tools_raw = section.get("tools") if isinstance(section.get("tools"), dict) else legacy_defaults
    renderer_raw = (
        section.get("renderer") if isinstance(section.get("renderer"), dict) else legacy_defaults
    )
    reasoning_raw = section.get("reasoning") if isinstance(section.get("reasoning"), dict) else {}
    no_edit_raw = section.get("no_edit") if isinstance(section.get("no_edit"), dict) else {}
    todo_raw = section.get("todo") if isinstance(section.get("todo"), dict) else {}
    patch_raw = section.get("patch") if isinstance(section.get("patch"), dict) else {}
    tools = ToolSettings(
        enabled=_bool(tools_raw.get("enabled"), True),
        lines=_int(tools_raw.get("lines"), 3),
        preview_length=_int(tools_raw.get("preview_length"), 120),
        show_completed=_bool(tools_raw.get("show_completed"), False),
        show_duration=_bool(tools_raw.get("show_duration"), True),
        timestamp=_bool(tools_raw.get("timestamp"), True),
        timestamp_format=str(tools_raw.get("timestamp_format") or "%H:%M"),
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
    reasoning = ReasoningSettings(
        enabled=_bool(reasoning_raw.get("enabled"), True),
        max_lines=_int(reasoning_raw.get("max_lines"), 3),
        max_chars=_int(reasoning_raw.get("max_chars"), 600),
        min_update_chars=_int(reasoning_raw.get("min_update_chars"), 80),
        no_edit_strategy=_strategy(reasoning_raw.get("no_edit_strategy"), "off"),
        capture_inline_think_tags=_bool(reasoning_raw.get("capture_inline_think_tags"), False),
    )
    renderer = RendererSettings(
        strategy=_strategy(renderer_raw.get("strategy"), "auto"),
        edit_interval=_float(renderer_raw.get("edit_interval"), 1.5),
        stale_ttl_seconds=_int(renderer_raw.get("stale_ttl_seconds"), 900),
        redact_secrets=_bool(renderer_raw.get("redact_secrets"), True),
        mode=str(renderer_raw.get("mode") or "sectioned").strip().lower() or "sectioned",
        style=_style(renderer_raw.get("style"), "emoji"),
        density=_density(renderer_raw.get("density"), "normal"),
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
        todo=todo,
        patch=patch,
        reasoning=reasoning,
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
        reasoning_enabled=settings.reasoning.enabled,
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
        reasoning_enabled=_bool(
            raw.get("reasoning", raw.get("reasoning_enabled")), base.reasoning_enabled
        ),
        timestamp=_bool(raw.get("timestamp"), base.timestamp),
        timestamp_format=str(raw.get("timestamp_format") or base.timestamp_format),
    )
