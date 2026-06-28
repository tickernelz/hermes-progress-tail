from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from ..hooks.monkeypatches import install_monkeypatches
from ..models.state import SessionContext
from ..rendering.renderer import ProgressRenderer
from ..settings.config import load_settings
from .agent_events import (
    _compact_count,
    _compression_lifecycle_completed_text,
    _compression_status_tail_text,
    _finalize_target_context,
    _float_kw,
    _int_kw,
    _on_post_llm_call,
    _on_session_finalize,
    _on_session_reset,
    _positive_int_kw,
    _record_assistant_capture,
    _schedule_finalize,
    on_assistant_progress_from_agent,
    on_compression_lifecycle_from_agent,
    on_compression_status_from_agent,
    on_delegate_progress_from_agent,
    on_gateway_stop_from_runner,
    on_reasoning_delta_from_agent,
)
from .commands import _command
from .config_runtime import (
    _assistant_tail_enabled,
    _background_job_config_warnings,
    _feature_enabled,
    _load_runtime_config,
    _load_runtime_settings,
    _progress_tail_enabled,
)
from .context import (
    _adapter_for,
    _binding_is_stale_for_entry,
    _binding_session_id,
    _bound_telegram_topic_session_id,
    _context_for,
    _get_session_entry,
    _is_telegram_dm_source,
    _on_pre_gateway_dispatch,
    _pre_gateway_session_context,
    _register_context,
    _session_key,
    _source_with_thread_id,
    _SourceThreadOverride,
    _telegram_general_topic_ids,
    _telegram_topic_binding,
    _timestamp_seconds,
    _topic_recovered_source,
    register_context_from_adapter_event,
)
from .demo import _demo_command
from .environment import (
    _agent_cwd,
    _agent_session_id,
    _agent_session_key,
    _agent_string,
    _agent_system_prompt,
    _branch_count,
    _context_tokens,
    _estimate_request_tokens,
    _git_command,
    _git_snapshot,
    _load_git_snapshot,
    _positive_attr,
    _positive_int,
    _replace_environment_cwd,
    _runtime_profile_name,
    _terminal_live_cwd,
    _update_environment_from_agent,
    _update_environment_from_terminal,
)
from .origin import (
    _is_background_review_agent,
    _is_background_review_thread,
    _should_suppress_agent_progress,
)
from .tool_events import (
    _background_job_event_is_terminal,
    _compact_result_status,
    _context_for_non_background_thread,
    _context_owned_by_current_thread,
    _duration_text,
    _is_context_owner_thread,
    _json_obj,
    _on_post_tool_call,
    _on_pre_tool_call,
    _reactivate_foreground_context,
    _resolve_tool_agent,
    _schedule_background_job_cleanup,
    _schedule_background_job_poll,
    _schedule_render,
    _suppress_native_background_notify,
    _terminal_background_requested,
    _tool_agent_context,
    _tool_context_lookup_ids,
)

__all__ = [
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
    "on_assistant_progress_from_agent",
    "on_compression_lifecycle_from_agent",
    "on_compression_status_from_agent",
    "on_delegate_progress_from_agent",
    "on_gateway_stop_from_runner",
    "on_reasoning_delta_from_agent",
    "register",
    "register_context_from_adapter_event",
    "threading",
]

logger = logging.getLogger(__name__)
_renderer: ProgressRenderer | None = None
VERSION = "0.1.98"
_ASSISTANT_CAPTURE: dict[str, Any] = {
    "status": "never",
    "session_id": "",
    "session_key_present": False,
    "text_preview": "",
    "already_streamed": False,
    "updated_at": 0.0,
}


def _get_renderer() -> ProgressRenderer:
    global _renderer
    settings = _load_runtime_settings()
    if _renderer is None:
        _renderer = ProgressRenderer(settings)
    else:
        _renderer.settings = settings
    return _renderer


def _progresstail_update_alias(raw_args: str = "") -> str:
    args = str(raw_args or "").strip()
    return _command(f"update {args}".strip() if args else "update --apply")


def _progresstail_cleanup_alias(raw_args: str = "") -> str:
    args = str(raw_args or "").strip()
    return _command(f"config cleanup {args}".strip() if args else "config cleanup --apply")


def _progresstail_jobs_alias(raw_args: str = "") -> str:
    args = str(raw_args or "").strip()
    return _command(f"jobs {args}".strip() if args else "jobs")


def _register_progress_tail_commands(ctx: Any) -> None:
    ctx.register_command(
        "progresstail",
        _command,
        description="Show hermes-progress-tail plugin status",
        args_hint="status|doctor|jobs|update --dry-run|update --apply|config cleanup --dry-run|config cleanup --apply|demo",
    )
    ctx.register_command(
        "progresstail-update",
        _progresstail_update_alias,
        description="Apply a hermes-progress-tail plugin update by default",
    )
    ctx.register_command(
        "progresstail-doctor",
        lambda raw_args="": _command("doctor"),
        description="Diagnose hermes-progress-tail config and hooks",
    )
    ctx.register_command(
        "progresstail-jobs",
        _progresstail_jobs_alias,
        description="Show hermes-progress-tail background jobs",
    )
    ctx.register_command(
        "progresstail-cleanup",
        _progresstail_cleanup_alias,
        description="Apply progress-tail legacy config cleanup by default",
    )
    ctx.register_command(
        "progresstail-demo",
        lambda raw_args="": _command("demo"),
        description="Show a hermes-progress-tail demo bubble",
    )


def register(ctx):
    runtime_config = _load_runtime_config()
    settings = load_settings(runtime_config)
    logger.info(
        "hermes-progress-tail plugin loaded: version=%s enabled=%s mode=%s density=%s style=%s "
        "strategy=%s tools=%s assistant=%s reasoning=%s delegates=%s background_jobs=%s "
        "telegram_rich=%s cleanup_auto_delete=%s",
        VERSION,
        settings.enabled,
        settings.renderer.mode,
        settings.renderer.density,
        settings.renderer.style,
        settings.renderer.strategy,
        settings.tools.enabled,
        settings.assistant.enabled,
        settings.reasoning.enabled,
        settings.delegates.enabled,
        settings.background_jobs.enabled,
        settings.telegram.rich_messages,
        settings.cleanup.auto_delete,
    )
    monkeypatch_ok = install_monkeypatches()
    logger.info("hermes-progress-tail plugin hooks registered: monkeypatches=%s", monkeypatch_ok)
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("on_session_reset", _on_session_reset)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
    _register_progress_tail_commands(ctx)
