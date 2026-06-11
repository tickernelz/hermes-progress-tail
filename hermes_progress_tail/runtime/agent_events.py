from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..gateway.compat import platform_name
from ..models.state import AssistantEvent, DelegateEvent, ReasoningEvent
from ..utils.redaction import redact_text

logger = logging.getLogger(__name__)


def _runtime_plugin():
    from . import plugin as runtime_plugin

    return runtime_plugin


def _record_assistant_capture(
    status: str,
    *,
    session_id: str = "",
    session_key: str = "",
    text: str = "",
    already_streamed: bool = False,
) -> None:
    runtime_plugin = _runtime_plugin()
    runtime_plugin._ASSISTANT_CAPTURE.update(
        {
            "status": status,
            "session_id": str(session_id or ""),
            "session_key_present": bool(session_key),
            "text_preview": redact_text(" ".join(str(text or "").split()))[:120],
            "already_streamed": bool(already_streamed),
            "updated_at": time.time(),
        }
    )


def on_reasoning_delta_from_agent(
    agent: Any, text: str, *, source: str = "structured_reasoning"
) -> None:
    if not text:
        return
    runtime_plugin = _runtime_plugin()
    renderer = runtime_plugin._get_renderer()
    session_id = runtime_plugin._agent_session_id(agent)
    session_key = runtime_plugin._agent_session_key(agent)
    ctx = runtime_plugin._context_for_non_background_thread(renderer, session_id, session_key)
    if ctx is None or not ctx.reasoning_enabled:
        return
    runtime_plugin._update_environment_from_agent(ctx, agent)
    runtime_plugin._schedule_render(
        ctx, ReasoningEvent(ctx.session_id, ctx.session_key, ctx.platform, text, source=source)
    )


def on_compression_status_from_agent(agent: Any, text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return False
    runtime_plugin = _runtime_plugin()
    renderer = runtime_plugin._get_renderer()
    session_id = runtime_plugin._agent_session_id(agent)
    session_key = runtime_plugin._agent_session_key(agent)
    ctx = runtime_plugin._context_for_non_background_thread(renderer, session_id, session_key)
    if ctx is None:
        return False
    if not ctx.assistant_enabled or not renderer.settings.assistant.enabled:
        return False
    runtime_plugin._update_environment_from_agent(ctx, agent)
    return runtime_plugin._schedule_render(
        ctx,
        AssistantEvent(
            ctx.session_id,
            ctx.session_key,
            ctx.platform,
            _compression_status_tail_text(clean),
            already_streamed=False,
            transient=True,
        ),
    )


def _compression_status_tail_text(text: str) -> str:
    value = str(text or "").strip()
    lower = value.lower()
    if "preflight compression" in lower:
        return "Preflight compression — preparing compact context"
    return "Compacting context — summarizing earlier conversation"


def on_compression_lifecycle_from_agent(agent: Any, phase: str, **data: Any) -> bool:
    runtime_plugin = _runtime_plugin()
    renderer = runtime_plugin._get_renderer()
    old_session_id = str(data.get("old_session_id") or "")
    new_session_id = str(
        data.get("new_session_id") or runtime_plugin._agent_session_id(agent) or old_session_id
    )
    session_key = runtime_plugin._agent_session_key(agent)
    if old_session_id and new_session_id and old_session_id != new_session_id:
        candidate = runtime_plugin._context_for(renderer, old_session_id, session_key)
        if candidate is not None:
            renderer.migrate_context(old_session_id, new_session_id, session_key=session_key)
    ctx = runtime_plugin._context_for_non_background_thread(
        renderer, new_session_id or old_session_id, session_key
    )
    if ctx is None or not ctx.assistant_enabled or not renderer.settings.assistant.enabled:
        return False
    runtime_plugin._update_environment_from_agent(ctx, agent)
    if phase == "started":
        text = "Compacting context — summarizing earlier conversation"
    elif phase == "completed":
        text = _compression_lifecycle_completed_text(data)
    elif phase == "failed":
        text = "Context compaction failed — continuing unchanged"
    else:
        return False
    return runtime_plugin._schedule_render(
        ctx,
        AssistantEvent(
            ctx.session_id,
            ctx.session_key,
            ctx.platform,
            text,
            already_streamed=False,
            transient=True,
        ),
        force=True,
    )


def _compression_lifecycle_completed_text(data: dict[str, Any]) -> str:
    before_count = _positive_int_kw(data.get("before_count"), 0)
    after_count = _positive_int_kw(data.get("after_count"), 0)
    before_tokens = _positive_int_kw(data.get("before_tokens"), 0)
    after_tokens = _positive_int_kw(data.get("after_tokens"), 0)
    after_tokens_kind = str(data.get("after_tokens_kind") or "").strip().lower()
    if before_count and after_count and after_count < before_count:
        text = f"Context compacted · {before_count} → {after_count} messages"
    else:
        text = "Context compaction checked"
    if before_tokens and after_tokens:
        label = "rough " if after_tokens_kind == "rough" else ""
        text += f" · {label}{_compact_count(before_tokens)} → {_compact_count(after_tokens)} tokens"
    return text


def _positive_int_kw(value: Any, default: int) -> int:
    parsed = _int_kw(value, default)
    return parsed if parsed > 0 else default


def _compact_count(value: int) -> str:
    if value >= 1000:
        return f"{round(value / 1000):.0f}k"
    return str(value)


def on_assistant_progress_from_agent(
    agent: Any, text: str, *, already_streamed: bool = False
) -> bool:
    clean = str(text or "").strip()
    runtime_plugin = _runtime_plugin()
    if not clean:
        runtime_plugin._record_assistant_capture("empty", already_streamed=already_streamed)
        return False
    renderer = runtime_plugin._get_renderer()
    session_id = runtime_plugin._agent_session_id(agent)
    session_key = runtime_plugin._agent_session_key(agent)
    ctx = runtime_plugin._context_for_non_background_thread(renderer, session_id, session_key)
    if ctx is None:
        runtime_plugin._record_assistant_capture(
            "no_context",
            session_id=session_id,
            session_key=session_key,
            text=clean,
            already_streamed=already_streamed,
        )
        return False
    if not ctx.assistant_enabled or not renderer.settings.assistant.enabled:
        runtime_plugin._record_assistant_capture(
            "disabled",
            session_id=ctx.session_id,
            session_key=ctx.session_key,
            text=clean,
            already_streamed=already_streamed,
        )
        return False
    runtime_plugin._update_environment_from_agent(ctx, agent)
    scheduled = runtime_plugin._schedule_render(
        ctx,
        AssistantEvent(
            ctx.session_id,
            ctx.session_key,
            ctx.platform,
            clean,
            already_streamed=already_streamed,
        ),
    )
    runtime_plugin._record_assistant_capture(
        "scheduled" if scheduled else "schedule_failed",
        session_id=ctx.session_id,
        session_key=ctx.session_key,
        text=clean,
        already_streamed=already_streamed,
    )
    return scheduled and not already_streamed


def on_delegate_progress_from_agent(
    parent_agent: Any,
    event_type: str,
    tool_name: str | None = None,
    preview: str | None = None,
    args: dict | None = None,
    **kwargs: Any,
) -> None:
    _ = args
    runtime_plugin = _runtime_plugin()
    renderer = runtime_plugin._get_renderer()
    session_id = runtime_plugin._agent_session_id(parent_agent)
    session_key = runtime_plugin._agent_session_key(parent_agent)
    ctx = runtime_plugin._context_for_non_background_thread(renderer, session_id, session_key)
    if ctx is None or not ctx.delegates_enabled:
        return
    subagent_id = str(kwargs.get("subagent_id") or f"task-{kwargs.get('task_index', 0)}")
    event = DelegateEvent(
        session_id=ctx.session_id,
        session_key=ctx.session_key,
        platform=ctx.platform,
        subagent_id=subagent_id,
        task_index=_int_kw(kwargs.get("task_index"), 0),
        task_count=_int_kw(kwargs.get("task_count"), 1),
        goal=str(kwargs.get("goal") or ""),
        event_type=str(event_type or ""),
        tool_name=str(tool_name or ""),
        preview=str(preview or ""),
        args=dict(args) if isinstance(args, dict) else {},
        status=str(kwargs.get("status") or ""),
        model=str(kwargs.get("model") or ""),
        tool_count=_int_kw(kwargs.get("tool_count"), 0),
        duration_seconds=_float_kw(kwargs.get("duration_seconds"), 0.0),
        summary=str(kwargs.get("summary") or ""),
    )
    runtime_plugin._schedule_render(ctx, event)


def _int_kw(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_kw(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _finalize_target_context(
    renderer: Any,
    session_id: str = "",
    platform: str = "",
    session_key: str = "",
):
    runtime_plugin = _runtime_plugin()
    if runtime_plugin._is_background_review_thread():
        return None
    ctx = renderer.find_context(session_id, session_key)
    if ctx is not None:
        return ctx
    if session_id and not session_key:
        return None
    active = [
        candidate
        for candidate in renderer.sessions.values()
        if (not platform or candidate.platform == platform) and candidate.progress_state == "active"
    ]
    return active[0] if len(active) == 1 else None


def _schedule_finalize(
    session_id: str = "",
    platform: str = "",
    session_key: str = "",
    *,
    purge: bool = False,
    success: bool = True,
) -> None:
    runtime_plugin = _runtime_plugin()
    renderer = runtime_plugin._get_renderer()
    ctx = runtime_plugin._finalize_target_context(renderer, session_id, platform, session_key)
    if ctx is None or ctx.loop is None:
        return
    generation = ctx.generation
    try:
        future = asyncio.run_coroutine_threadsafe(
            renderer.finalize(
                session_id=ctx.session_id,
                purge=purge,
                generation=generation,
                success=success,
            ),
            ctx.loop,
        )

        def _consume_done(fut):
            try:
                fut.result()
            except Exception as exc:
                logger.debug("hermes-progress-tail finalize failed: %s", exc)

        future.add_done_callback(_consume_done)
    except Exception as exc:
        logger.debug("hermes-progress-tail finalize schedule failed: %s", exc)


def on_gateway_stop_from_runner(
    gateway: Any = None, *, session_key: str = "", source: Any = None
) -> bool:
    _ = gateway
    runtime_plugin = _runtime_plugin()
    platform = platform_name(source) if source is not None else ""
    renderer = runtime_plugin._get_renderer()
    ctx = runtime_plugin._finalize_target_context(
        renderer, session_key=session_key, platform=platform
    )
    if ctx is None:
        return False
    runtime_plugin._schedule_finalize(
        session_id=ctx.session_id,
        session_key=ctx.session_key,
        platform=ctx.platform,
        success=False,
    )
    return True


def _reset_inline_reasoning(agent: Any) -> None:
    if agent is None:
        return
    try:
        from ..hooks.monkeypatches import _reset_inline_reasoning_state

        _reset_inline_reasoning_state(agent)
    except Exception:
        logger.debug("hermes-progress-tail inline think reset failed", exc_info=True)


def _on_post_llm_call(session_id: str = "", agent: Any = None, **_: Any):
    runtime_plugin = _runtime_plugin()
    _reset_inline_reasoning(agent)
    runtime_plugin._schedule_finalize(
        session_id=session_id, session_key=runtime_plugin._agent_session_key(agent)
    )
    return None


def _on_session_reset(session_id: str = "", platform: str = "", agent: Any = None, **_: Any):
    _reset_inline_reasoning(agent)
    runtime_plugin = _runtime_plugin()
    runtime_plugin._get_renderer().purge(session_id=session_id, platform=platform)
    return None


def _on_session_finalize(session_id: str = "", platform: str = "", agent: Any = None, **_: Any):
    _reset_inline_reasoning(agent)
    runtime_plugin = _runtime_plugin()
    runtime_plugin._schedule_finalize(
        session_id=session_id,
        platform=platform,
        session_key=runtime_plugin._agent_session_key(agent),
        purge=True,
    )
    return None
