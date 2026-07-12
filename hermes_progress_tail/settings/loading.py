from __future__ import annotations

from typing import Any

from .coercion import (
    as_bool,
    as_delegate_thinking,
    as_float,
    as_footer_density,
    as_int,
    as_patch_detail,
    as_positive_minutes,
    as_strategy,
    as_style,
    renderer_mode_and_density,
)
from .migration import extract_progress_tail_section
from .types import (
    AssistantSettings,
    BackgroundJobSettings,
    CleanupSettings,
    DelegateSettings,
    FooterSettings,
    NativeGatewaySettings,
    NoEditSettings,
    PatchSettings,
    ReasoningSettings,
    RendererSettings,
    Settings,
    TelegramSettings,
    TodoSettings,
    ToolSettings,
)

__all__ = ("load_settings",)


def _build_tools(raw: dict[str, Any], defaults: ToolSettings) -> ToolSettings:
    return ToolSettings(
        enabled=as_bool(raw.get("enabled"), defaults.enabled),
        lines=as_int(raw.get("lines"), defaults.lines),
        preview_length=as_int(raw.get("preview_length"), defaults.preview_length),
        show_completed=as_bool(raw.get("show_completed"), defaults.show_completed),
        show_duration=as_bool(raw.get("show_duration"), defaults.show_duration),
        timestamp=as_bool(raw.get("timestamp"), defaults.timestamp),
        timestamp_format=str(raw.get("timestamp_format") or defaults.timestamp_format),
    )


def _build_delegates(raw: dict[str, Any], defaults: DelegateSettings) -> DelegateSettings:
    return DelegateSettings(
        enabled=as_bool(raw.get("enabled"), defaults.enabled),
        max_delegates=as_int(raw.get("max_delegates"), defaults.max_delegates),
        lines_per_delegate=as_int(raw.get("lines_per_delegate"), defaults.lines_per_delegate),
        max_goal_chars=as_int(raw.get("max_goal_chars"), defaults.max_goal_chars, min_value=12),
        max_line_chars=as_int(raw.get("max_line_chars"), defaults.max_line_chars, min_value=24),
        show_model=as_bool(raw.get("show_model"), defaults.show_model),
        show_tool_count=as_bool(raw.get("show_tool_count"), defaults.show_tool_count),
        show_completion=as_bool(raw.get("show_completion"), defaults.show_completion),
        completed_ttl_seconds=as_int(
            raw.get("completed_ttl_seconds"), defaults.completed_ttl_seconds
        ),
        thinking=as_delegate_thinking(raw.get("thinking"), defaults.thinking),
    )


def _build_todo(raw: dict[str, Any], defaults: TodoSettings) -> TodoSettings:
    return TodoSettings(
        sticky=as_bool(raw.get("sticky"), defaults.sticky),
        hide_tool_line=as_bool(raw.get("hide_tool_line"), defaults.hide_tool_line),
        max_pending=as_int(raw.get("max_pending"), defaults.max_pending),
        max_completed=as_int(raw.get("max_completed"), defaults.max_completed),
        max_cancelled=as_int(raw.get("max_cancelled"), defaults.max_cancelled),
        max_item_chars=as_int(raw.get("max_item_chars"), defaults.max_item_chars, min_value=10),
    )


def _build_patch(raw: dict[str, Any], defaults: PatchSettings) -> PatchSettings:
    return PatchSettings(
        detail=as_patch_detail(raw.get("detail"), defaults.detail),
        preview_chars=as_int(raw.get("preview_chars"), defaults.preview_chars, min_value=10),
        max_files=as_int(raw.get("max_files"), defaults.max_files),
    )


def _build_assistant(raw: dict[str, Any], defaults: AssistantSettings) -> AssistantSettings:
    return AssistantSettings(
        enabled=as_bool(raw.get("enabled"), defaults.enabled),
        max_lines=as_int(raw.get("max_lines"), defaults.max_lines),
        max_chars=as_int(raw.get("max_chars"), defaults.max_chars),
        min_update_chars=as_int(raw.get("min_update_chars"), defaults.min_update_chars),
    )


def _build_reasoning(raw: dict[str, Any], defaults: ReasoningSettings) -> ReasoningSettings:
    return ReasoningSettings(
        enabled=as_bool(raw.get("enabled"), defaults.enabled),
        max_lines=as_int(raw.get("max_lines"), defaults.max_lines),
        max_chars=as_int(raw.get("max_chars"), defaults.max_chars),
        min_update_chars=as_int(raw.get("min_update_chars"), defaults.min_update_chars),
        no_edit_strategy=as_strategy(raw.get("no_edit_strategy"), defaults.no_edit_strategy),
    )


def _build_background_jobs(
    raw: dict[str, Any], defaults: BackgroundJobSettings
) -> BackgroundJobSettings:
    return BackgroundJobSettings(
        enabled=as_bool(raw.get("enabled"), defaults.enabled),
        list_running=as_bool(raw.get("list_running"), defaults.list_running),
        show_completed=as_bool(raw.get("show_completed"), defaults.show_completed),
        completed_ttl_seconds=as_int(
            raw.get("completed_ttl_seconds"),
            defaults.completed_ttl_seconds,
        ),
        max_jobs=as_int(raw.get("max_jobs"), defaults.max_jobs),
        head_lines=as_int(raw.get("head_lines"), defaults.head_lines),
        tail_lines=as_int(raw.get("tail_lines"), defaults.tail_lines),
        max_line_chars=as_int(
            raw.get("max_line_chars"),
            defaults.max_line_chars,
            min_value=24,
        ),
        update_interval_seconds=as_int(
            raw.get("update_interval_seconds"),
            defaults.update_interval_seconds,
        ),
        suppress_native_notify=as_bool(
            raw.get("suppress_native_notify"),
            defaults.suppress_native_notify,
        ),
        suppress_watch_notifications=as_bool(
            raw.get("suppress_watch_notifications"),
            defaults.suppress_watch_notifications,
        ),
        default_notify_on_complete=as_bool(
            raw.get("default_notify_on_complete"),
            defaults.default_notify_on_complete,
        ),
    )


def _build_native_gateway(
    raw: dict[str, Any], defaults: NativeGatewaySettings
) -> NativeGatewaySettings:
    return NativeGatewaySettings(suppress=as_bool(raw.get("suppress"), defaults.suppress))


def _build_cleanup(raw: dict[str, Any], defaults: CleanupSettings) -> CleanupSettings:
    return CleanupSettings(
        auto_delete=as_bool(raw.get("auto_delete"), defaults.auto_delete),
        delay_seconds=as_int(raw.get("delay_seconds"), defaults.delay_seconds, min_value=0),
        delete_on_success=as_bool(raw.get("delete_on_success"), defaults.delete_on_success),
        delete_on_failure=as_bool(raw.get("delete_on_failure"), defaults.delete_on_failure),
        delete_background_active=as_bool(
            raw.get("delete_background_active"), defaults.delete_background_active
        ),
    )


def _build_footer(raw: dict[str, Any], defaults: FooterSettings) -> FooterSettings:
    return FooterSettings(
        enabled=as_bool(raw.get("enabled"), defaults.enabled),
        density=as_footer_density(raw.get("density"), defaults.density),
        max_path_chars=as_int(raw.get("max_path_chars"), defaults.max_path_chars, min_value=16),
    )


def _build_telegram(raw: dict[str, Any], defaults: TelegramSettings) -> TelegramSettings:
    return TelegramSettings(
        rich_messages=as_bool(raw.get("rich_messages"), defaults.rich_messages),
        verification_table=as_bool(raw.get("verification_table"), defaults.verification_table),
        thinking_blocks=as_bool(raw.get("thinking_blocks"), defaults.thinking_blocks),
        max_table_rows=as_int(raw.get("max_table_rows"), defaults.max_table_rows),
        compact_success=as_bool(raw.get("compact_success"), defaults.compact_success),
        max_detail_items=as_int(
            raw.get("max_detail_items"), defaults.max_detail_items, min_value=0
        ),
    )


def _build_renderer(raw: dict[str, Any], defaults: RendererSettings) -> RendererSettings:
    renderer_mode, renderer_density = renderer_mode_and_density(
        raw, defaults.mode, defaults.density
    )
    return RendererSettings(
        strategy=as_strategy(raw.get("strategy"), defaults.strategy),
        edit_interval=as_float(raw.get("edit_interval"), defaults.edit_interval),
        message_rollover_minutes=as_positive_minutes(
            raw.get("message_rollover_minutes"), defaults.message_rollover_minutes
        ),
        stale_ttl_seconds=as_int(raw.get("stale_ttl_seconds"), defaults.stale_ttl_seconds),
        redact_secrets=as_bool(raw.get("redact_secrets"), defaults.redact_secrets),
        mode=renderer_mode,
        style=as_style(raw.get("style"), defaults.style),
        density=renderer_density,
        agent_label=str(raw.get("agent_label") or defaults.agent_label).strip(),
    )


def _build_no_edit(raw: dict[str, Any], defaults: NoEditSettings) -> NoEditSettings:
    return NoEditSettings(
        interval_seconds=as_int(raw.get("interval_seconds"), defaults.interval_seconds),
        min_new_events=as_int(raw.get("min_new_events"), defaults.min_new_events),
        final_summary=as_bool(raw.get("final_summary"), defaults.final_summary),
        max_snapshots_per_turn=as_int(
            raw.get("max_snapshots_per_turn"), defaults.max_snapshots_per_turn
        ),
    )


def load_settings(config: dict[str, Any] | None) -> Settings:
    defaults = Settings()
    section = extract_progress_tail_section(config)
    legacy = section.get("defaults") if isinstance(section.get("defaults"), dict) else {}

    def raw(name: str, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        value = section.get(name)
        return value if isinstance(value, dict) else (fallback or {})

    return Settings(
        enabled=as_bool(section.get("enabled"), defaults.enabled),
        tools=_build_tools(raw("tools", legacy), defaults.tools),
        delegates=_build_delegates(raw("delegates"), defaults.delegates),
        todo=_build_todo(raw("todo"), defaults.todo),
        patch=_build_patch(raw("patch"), defaults.patch),
        assistant=_build_assistant(raw("assistant"), defaults.assistant),
        reasoning=_build_reasoning(raw("reasoning"), defaults.reasoning),
        background_jobs=_build_background_jobs(raw("background_jobs"), defaults.background_jobs),
        native_gateway=_build_native_gateway(raw("native_gateway"), defaults.native_gateway),
        cleanup=_build_cleanup(raw("cleanup"), defaults.cleanup),
        footer=_build_footer(raw("footer"), defaults.footer),
        telegram=_build_telegram(raw("telegram"), defaults.telegram),
        renderer=_build_renderer(raw("renderer", legacy), defaults.renderer),
        no_edit=_build_no_edit(raw("no_edit"), defaults.no_edit),
        platforms=raw("platforms"),
    )
