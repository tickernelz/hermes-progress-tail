from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

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
)


@dataclass(frozen=True)
class ToolSettings:
    enabled: bool = True
    lines: int = 3
    preview_length: int = 120
    show_completed: bool = True
    show_duration: bool = True
    timestamp: bool = True
    timestamp_format: str = "%H:%M"


@dataclass(frozen=True)
class DelegateSettings:
    enabled: bool = True
    max_delegates: int = 4
    lines_per_delegate: int = 2
    max_goal_chars: int = 48
    max_line_chars: int = 120
    show_model: bool = False
    show_tool_count: bool = True
    show_completion: bool = True
    completed_ttl_seconds: int = 5
    thinking: Literal["off", "summary"] = "off"


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
class AssistantSettings:
    enabled: bool = True
    max_lines: int = 3
    max_chars: int = 500
    min_update_chars: int = 160


@dataclass(frozen=True)
class ReasoningSettings:
    enabled: bool = True
    max_lines: int = 3
    max_chars: int = 600
    min_update_chars: int = 300
    no_edit_strategy: str = "off"


@dataclass(frozen=True)
class BackgroundJobSettings:
    enabled: bool = True
    list_running: bool = True
    show_completed: bool = True
    completed_ttl_seconds: int = 5
    max_jobs: int = 4
    head_lines: int = 2
    tail_lines: int = 3
    max_line_chars: int = 120
    update_interval_seconds: int = 10
    suppress_native_notify: bool = True
    suppress_watch_notifications: bool = True
    default_notify_on_complete: bool = False


@dataclass(frozen=True)
class NativeGatewaySettings:
    suppress: bool = True


@dataclass(frozen=True)
class CleanupSettings:
    auto_delete: bool = False
    delay_seconds: int = 5
    delete_on_success: bool = True
    delete_on_failure: bool = False
    delete_background_active: bool = False


@dataclass(frozen=True)
class FooterSettings:
    enabled: bool = True
    density: Literal["compact", "normal", "debug"] = "normal"
    max_path_chars: int = 56


@dataclass(frozen=True)
class TelegramSettings:
    rich_messages: bool = True
    verification_table: bool = True
    thinking_blocks: bool = True
    max_table_rows: int = 8
    compact_success: bool = True
    max_detail_items: int = 8


@dataclass(frozen=True)
class LegacyDefaultSettings:
    lines: int = 3
    preview_length: int = 120
    edit_interval: float = 5.0
    stale_ttl_seconds: int = 900
    redact_secrets: bool = True
    show_completed: bool = False


@dataclass(frozen=True)
class RendererSettings:
    strategy: str = "auto"
    edit_interval: float = 5.0
    message_rollover_minutes: int = 5
    stale_ttl_seconds: int = 900
    redact_secrets: bool = True
    mode: str = "sectioned"
    style: Literal["emoji", "plain"] = "emoji"
    density: Literal["compact", "normal", "verbose", "debug"] = "normal"
    agent_label: str = ""


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
    edit_interval: float = 5.0
    stale_ttl_seconds: int = 900
    redact_secrets: bool = True
    show_completed: bool = False
    tools_enabled: bool = True
    assistant_enabled: bool = True
    reasoning_enabled: bool = True
    delegates_enabled: bool = True
    background_jobs_enabled: bool = True
    timestamp: bool = True
    timestamp_format: str = "%H:%M"


@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    tools: ToolSettings = field(default_factory=ToolSettings)
    delegates: DelegateSettings = field(default_factory=DelegateSettings)
    todo: TodoSettings = field(default_factory=TodoSettings)
    patch: PatchSettings = field(default_factory=PatchSettings)
    assistant: AssistantSettings = field(default_factory=AssistantSettings)
    reasoning: ReasoningSettings = field(default_factory=ReasoningSettings)
    background_jobs: BackgroundJobSettings = field(default_factory=BackgroundJobSettings)
    native_gateway: NativeGatewaySettings = field(default_factory=NativeGatewaySettings)
    cleanup: CleanupSettings = field(default_factory=CleanupSettings)
    footer: FooterSettings = field(default_factory=FooterSettings)
    telegram: TelegramSettings = field(default_factory=TelegramSettings)
    renderer: RendererSettings = field(default_factory=RendererSettings)
    no_edit: NoEditSettings = field(default_factory=NoEditSettings)
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
