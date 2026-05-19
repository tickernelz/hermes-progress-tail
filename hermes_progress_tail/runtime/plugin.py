from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from ..gateway.compat import platform_name, source_chat_id, source_thread_id
from ..hooks.monkeypatches import install_monkeypatches
from ..models.state import (
    AssistantEvent,
    BackgroundJobEvent,
    DelegateEvent,
    ReasoningEvent,
    SessionContext,
    ToolEvent,
)
from ..rendering.formatter import extract_todo_items, format_tool_line
from ..rendering.renderer import ProgressRenderer
from ..settings.config import (
    find_retired_config_keys,
    find_unknown_config_keys,
    load_settings,
    resolve_platform_settings,
)
from ..utils.redaction import redact_text

logger = logging.getLogger(__name__)
_renderer: ProgressRenderer | None = None
VERSION = "0.1.51"
_ASSISTANT_CAPTURE: dict[str, Any] = {
    "status": "never",
    "session_id": "",
    "session_key_present": False,
    "text_preview": "",
    "already_streamed": False,
    "updated_at": 0.0,
}


def _agent_session_id(agent: Any) -> str:
    return str(getattr(agent, "session_id", "") or "")


def _agent_session_key(agent: Any) -> str:
    return str(
        getattr(agent, "gateway_session_key", None)
        or getattr(agent, "_gateway_session_key", None)
        or ""
    )


def _load_runtime_config() -> dict[str, Any]:
    config = {}
    try:
        from hermes_constants import get_hermes_home

        config_path = Path(get_hermes_home()) / "config.yaml"
    except Exception:
        config_path = Path.home() / ".hermes" / "config.yaml"
    try:
        if config_path.exists():
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config = loaded
    except Exception as exc:
        logger.debug("hermes-progress-tail config load failed: %s", exc)
    return config


def _load_runtime_settings():
    return load_settings(_load_runtime_config())


def _record_assistant_capture(
    status: str,
    *,
    session_id: str = "",
    session_key: str = "",
    text: str = "",
    already_streamed: bool = False,
) -> None:
    _ASSISTANT_CAPTURE.update(
        {
            "status": status,
            "session_id": str(session_id or ""),
            "session_key_present": bool(session_key),
            "text_preview": redact_text(" ".join(str(text or "").split()))[:120],
            "already_streamed": bool(already_streamed),
            "updated_at": time.time(),
        }
    )


def _context_for(renderer: ProgressRenderer, session_id: str = "", session_key: str = ""):
    ctx = renderer.find_context(session_id, session_key)
    if ctx is not None:
        return ctx
    if session_id:
        matches = [
            candidate
            for candidate in renderer.sessions.values()
            if candidate.session_id == session_id
        ]
        if len(matches) == 1:
            return matches[0]
    if session_key:
        mapped = renderer.session_keys.get(session_key)
        if mapped:
            return renderer.sessions.get(mapped)
    return None


def _progress_tail_enabled(config: dict[str, Any]) -> bool:
    progress_tail = config.get("progress_tail")
    return not (isinstance(progress_tail, dict) and progress_tail.get("enabled") is False)


def _feature_enabled(config: dict[str, Any], name: str, default: bool = True) -> bool:
    if not _progress_tail_enabled(config):
        return False
    progress_tail = config.get("progress_tail")
    feature = progress_tail.get(name) if isinstance(progress_tail, dict) else None
    if not isinstance(feature, dict):
        return default
    return feature.get("enabled") is not False


def _assistant_tail_enabled(config: dict[str, Any]) -> bool:
    return _feature_enabled(config, "assistant", True)


def _builtin_interim_conflict(config: dict[str, Any]) -> bool:
    display = config.get("display")
    if not isinstance(display, dict) or display.get("interim_assistant_messages") is False:
        return False
    return _assistant_tail_enabled(config)


def _builtin_reasoning_conflict(config: dict[str, Any]) -> bool:
    display = config.get("display")
    if not isinstance(display, dict) or display.get("show_reasoning") is not True:
        return False
    return _feature_enabled(config, "reasoning", True)


def _core_notifier_conflict(config: dict[str, Any]) -> bool:
    if not _progress_tail_enabled(config):
        return False
    agent = config.get("agent")
    if not isinstance(agent, dict):
        return True
    value = agent.get("gateway_notify_interval", 180)
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return True


def _interim_conflict_warning() -> str:
    return (
        "warning: display.interim_assistant_messages=true while "
        "progress_tail.assistant.enabled=true; duplicate mid-turn assistant progress may occur. "
        "Set display.interim_assistant_messages=false."
    )


def _reasoning_conflict_warning() -> str:
    return (
        "warning: display.show_reasoning=true while progress_tail.reasoning.enabled=true; "
        "duplicate reasoning/final output may occur. Set display.show_reasoning=false."
    )


def _core_notifier_conflict_warning() -> str:
    return (
        "warning: agent.gateway_notify_interval is enabled while progress_tail.enabled=true; "
        "Hermes core Still working notifications use send() and can duplicate progress. "
        "Set agent.gateway_notify_interval=0."
    )


def _background_job_config_warnings(settings: Any) -> list[str]:
    background = settings.background_jobs
    if not background.enabled:
        return []
    warnings = []
    if not background.suppress_native_notify:
        warnings.append(
            "warning: background_jobs.enabled=true but suppress_native_notify=false; "
            "native process notifications may duplicate progress-tail output"
        )
    if not background.suppress_watch_notifications:
        warnings.append(
            "warning: background_jobs.enabled=true but suppress_watch_notifications=false; "
            "watch pattern notifications may duplicate progress-tail output"
        )
    if not background.list_running:
        warnings.append(
            "warning: background_jobs.enabled=true but list_running=false; "
            "running jobs will be hidden from /progresstail jobs"
        )
    return warnings


def _get_renderer() -> ProgressRenderer:
    global _renderer
    settings = _load_runtime_settings()
    if _renderer is None:
        _renderer = ProgressRenderer(settings)
    else:
        _renderer.settings = settings
    return _renderer


def _get_session_entry(session_store: Any, source: Any):
    try:
        return session_store.get_or_create_session(source)
    except Exception as exc:
        logger.debug("hermes-progress-tail failed to resolve session entry: %s", exc)
        return None


def _session_key(entry: Any, source: Any, gateway: Any) -> str:
    direct = getattr(entry, "session_key", "")
    if direct:
        return str(direct)
    try:
        from gateway.session import build_session_key

        cfg = getattr(gateway, "config", None)
        return build_session_key(
            source,
            group_sessions_per_user=getattr(cfg, "group_sessions_per_user", True),
            thread_sessions_per_user=getattr(cfg, "thread_sessions_per_user", False),
        )
    except Exception:
        return ""


def _adapter_for(gateway: Any, source: Any):
    adapters = getattr(gateway, "adapters", {}) or {}
    platform = getattr(source, "platform", None)
    return adapters.get(platform) or adapters.get(platform_name(source))


def _register_context(
    *,
    renderer: ProgressRenderer,
    source: Any,
    adapter: Any,
    session_id: str,
    session_key: str,
) -> None:
    platform = platform_name(source)
    settings = resolve_platform_settings(renderer.settings, platform)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    ctx = SessionContext(
        session_id=session_id,
        session_key=session_key,
        platform=platform,
        chat_id=source_chat_id(source),
        thread_id=source_thread_id(source),
        adapter=adapter,
        loop=loop,
        strategy=settings.strategy,
        lines=settings.lines,
        preview_length=settings.preview_length,
        edit_interval=settings.edit_interval,
        tools_enabled=settings.tools_enabled,
        assistant_enabled=settings.assistant_enabled,
        reasoning_enabled=settings.reasoning_enabled,
        delegates_enabled=settings.delegates_enabled,
        background_jobs_enabled=settings.background_jobs_enabled,
        timestamp=settings.timestamp,
        timestamp_format=settings.timestamp_format,
        agent_label=renderer.settings.renderer.agent_label,
    )
    renderer.register_context(ctx)


def _on_pre_gateway_dispatch(event: Any, gateway: Any, session_store: Any, **_: Any):
    renderer = _get_renderer()
    source = getattr(event, "source", None)
    if source is None:
        return None
    platform = platform_name(source)
    if not platform:
        return None
    settings = resolve_platform_settings(renderer.settings, platform)
    if not settings.enabled or settings.strategy == "off":
        return None
    entry = _get_session_entry(session_store, source)
    session_id = str(getattr(entry, "session_id", "") or "")
    if not session_id:
        return None
    adapter = _adapter_for(gateway, source)
    if adapter is None:
        logger.debug("hermes-progress-tail adapter not found for platform %s", platform)
        return None
    _register_context(
        renderer=renderer,
        source=source,
        adapter=adapter,
        session_id=session_id,
        session_key=_session_key(entry, source, gateway),
    )
    return None


def register_context_from_adapter_event(adapter: Any, event: Any) -> None:
    renderer = _get_renderer()
    source = getattr(event, "source", None)
    if source is None:
        return
    platform = platform_name(source)
    if not platform:
        return
    settings = resolve_platform_settings(renderer.settings, platform)
    if not settings.enabled or settings.strategy == "off":
        return
    session_store = getattr(adapter, "_session_store", None)
    if session_store is None:
        return
    entry = _get_session_entry(session_store, source)
    session_id = str(getattr(entry, "session_id", "") or "")
    if not session_id:
        return
    _register_context(
        renderer=renderer,
        source=source,
        adapter=adapter,
        session_id=session_id,
        session_key=_session_key(entry, source, getattr(adapter, "gateway", None) or adapter),
    )
    return


def _schedule_render(
    ctx: SessionContext,
    event: ToolEvent | ReasoningEvent | AssistantEvent | DelegateEvent | BackgroundJobEvent,
    *,
    force: bool = False,
) -> bool:
    renderer = _get_renderer()
    if ctx.loop is None:
        return False
    try:
        future = asyncio.run_coroutine_threadsafe(
            renderer.handle_event(event, force=force), ctx.loop
        )

        def _consume_done(fut):
            try:
                fut.result()
            except Exception as exc:
                logger.debug("hermes-progress-tail render failed: %s", exc)

        future.add_done_callback(_consume_done)
        return True
    except Exception as exc:
        logger.debug("hermes-progress-tail schedule failed: %s", exc)
        return False


def _compact_result_status(result: Any) -> str:
    if result is None or result == "":
        return "done"
    data = result
    if isinstance(result, str):
        try:
            import json

            data = json.loads(result)
        except Exception:
            data = None
    if isinstance(data, dict):
        success = data.get("success")
        if success is False:
            return "failed"
        exit_code = data.get("exit_code")
        if isinstance(exit_code, int) and exit_code != 0:
            return "failed"
        error = data.get("error")
        if error not in {None, "", False} and success is not True:
            return "failed"
        return "done"
    text = str(result).lower()
    if "traceback" in text or "exception" in text:
        return "failed"
    return "done"


def _duration_text(duration_ms: int | float | None) -> str:
    if not isinstance(duration_ms, (int, float)) or duration_ms < 0:
        return ""
    seconds = duration_ms / 1000
    if seconds < 10:
        return f"{seconds:.1f}s"
    return f"{seconds:.0f}s"


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        import json

        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _terminal_background_requested(args: dict | None) -> bool:
    return isinstance(args, dict) and bool(args.get("background"))


def _suppress_native_background_notify(process_id: str) -> None:
    if not process_id:
        return
    try:
        from tools.process_registry import process_registry

        session = process_registry.get(process_id)
        if session is not None:
            if _get_renderer().settings.background_jobs.suppress_native_notify:
                session.notify_on_complete = False
                session.watcher_interval = 0
            if _get_renderer().settings.background_jobs.suppress_watch_notifications:
                session.watch_patterns = []
        process_registry.pending_watchers[:] = [
            watcher
            for watcher in process_registry.pending_watchers
            if watcher.get("session_id") != process_id
        ]
    except Exception:
        logger.debug(
            "hermes-progress-tail failed to suppress native background notify", exc_info=True
        )


def _schedule_background_job_poll(ctx: SessionContext, process_id: str) -> None:
    if not process_id or ctx.loop is None or not _get_renderer().settings.background_jobs.enabled:
        return
    job = ctx.background_jobs.get(process_id)
    if job is not None and job.poll_task is not None and not job.poll_task.done():
        return

    async def _poll() -> None:
        try:
            while True:
                await asyncio.sleep(
                    _get_renderer().settings.background_jobs.update_interval_seconds
                )
                try:
                    from tools.process_registry import process_registry

                    session = process_registry.get(process_id)
                except Exception:
                    session = None
                if session is None:
                    _schedule_render(
                        ctx,
                        BackgroundJobEvent(
                            ctx.session_id,
                            ctx.session_key,
                            ctx.platform,
                            process_id,
                            event_type="lost",
                            exited=True,
                        ),
                    )
                    return
                output = str(getattr(session, "output_buffer", "") or "")
                exited = bool(getattr(session, "exited", False))
                existing = ctx.background_jobs.get(process_id)
                if existing is not None and not exited and output == existing.last_output:
                    continue
                _schedule_render(
                    ctx,
                    BackgroundJobEvent(
                        ctx.session_id,
                        ctx.session_key,
                        ctx.platform,
                        process_id,
                        event_type="completed" if exited else "output",
                        command=str(getattr(session, "command", "") or ""),
                        cwd=str(getattr(session, "cwd", "") or ""),
                        pid=getattr(session, "pid", None),
                        output=output,
                        exited=exited,
                        exit_code=getattr(session, "exit_code", None),
                    ),
                )
                if exited:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("hermes-progress-tail background job poll failed", exc_info=True)

    task = ctx.loop.create_task(_poll())
    job = ctx.background_jobs.get(process_id)
    if job is not None:
        job.poll_task = task


def _is_background_review_thread() -> bool:
    thread_name = str(getattr(threading.current_thread(), "name", "") or "").lower()
    return thread_name == "bg-review" or thread_name.startswith("bg-review:")


def _on_pre_tool_call(
    tool_name: str,
    args: dict | None = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    preview: str | None = None,
    **_: Any,
):
    if _is_background_review_thread():
        return None
    renderer = _get_renderer()
    ctx = _context_for(renderer, session_id or task_id, task_id)
    if ctx is None:
        return None
    if not ctx.tools_enabled:
        return None
    line = format_tool_line(
        tool_name,
        args or {},
        preview=preview,
        preview_length=ctx.preview_length,
        patch_detail=renderer.settings.patch.detail,
        patch_preview_chars=renderer.settings.patch.preview_chars,
        patch_max_files=renderer.settings.patch.max_files,
    )
    if renderer.settings.tools.show_completed:
        line = f"{line} · {'background' if _terminal_background_requested(args) else 'running'}"
    event = ToolEvent(
        session_id=ctx.session_id,
        session_key=ctx.session_key,
        platform=ctx.platform,
        line=line,
        tool_call_id=tool_call_id or "",
        tool_name=tool_name,
        todo_items=extract_todo_items(args or {}) if tool_name == "todo" else (),
    )
    _schedule_render(ctx, event)
    return None


def _on_post_tool_call(
    tool_name: str,
    args: dict | None = None,
    result: str = "",
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: int | None = None,
    **_: Any,
):
    if _is_background_review_thread():
        return None
    renderer = _get_renderer()
    ctx = _context_for(renderer, session_id or task_id, task_id)
    if ctx is None or not ctx.tools_enabled:
        return None
    result_obj = _json_obj(result)
    if tool_name == "terminal" and _terminal_background_requested(args):
        process_id = str(result_obj.get("session_id") or "")
        if process_id and renderer.settings.background_jobs.enabled and ctx.background_jobs_enabled:
            _suppress_native_background_notify(process_id)
            _schedule_render(
                ctx,
                BackgroundJobEvent(
                    ctx.session_id,
                    ctx.session_key,
                    ctx.platform,
                    process_id,
                    event_type="started",
                    command=str((args or {}).get("command") or ""),
                    cwd=str((args or {}).get("workdir") or ""),
                    pid=result_obj.get("pid"),
                ),
            )
            _schedule_background_job_poll(ctx, process_id)
    if not renderer.settings.tools.show_completed:
        return None
    base = format_tool_line(
        tool_name,
        args or {},
        preview_length=ctx.preview_length,
        patch_detail=renderer.settings.patch.detail,
        patch_preview_chars=renderer.settings.patch.preview_chars,
        patch_max_files=renderer.settings.patch.max_files,
    )
    status = _compact_result_status(result)
    marker = "✅" if status == "done" else "❌"
    suffix = f" · {status}"
    duration = _duration_text(duration_ms) if renderer.settings.tools.show_duration else ""
    if duration:
        suffix += f" · {duration}"
    line = f"{marker} {base} {suffix}"
    _schedule_render(
        ctx,
        ToolEvent(
            ctx.session_id,
            ctx.session_key,
            ctx.platform,
            line,
            tool_call_id=tool_call_id or "",
            tool_name=tool_name,
            replace_existing=True,
        ),
    )
    return None


def on_reasoning_delta_from_agent(
    agent: Any, text: str, *, source: str = "structured_reasoning"
) -> None:
    if not text:
        return
    renderer = _get_renderer()
    session_id = _agent_session_id(agent)
    session_key = _agent_session_key(agent)
    ctx = _context_for(renderer, session_id, session_key)
    if ctx is None or not ctx.reasoning_enabled:
        return
    _schedule_render(
        ctx, ReasoningEvent(ctx.session_id, ctx.session_key, ctx.platform, text, source=source)
    )


def on_compression_status_from_agent(agent: Any, text: str) -> bool:
    clean = str(text or "").strip()
    if not clean:
        return False
    renderer = _get_renderer()
    session_id = _agent_session_id(agent)
    session_key = _agent_session_key(agent)
    ctx = _context_for(renderer, session_id, session_key)
    if ctx is None:
        return False
    if not ctx.assistant_enabled or not renderer.settings.assistant.enabled:
        return False
    return _schedule_render(
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
    renderer = _get_renderer()
    old_session_id = str(data.get("old_session_id") or "")
    new_session_id = str(data.get("new_session_id") or _agent_session_id(agent) or old_session_id)
    session_key = _agent_session_key(agent)
    if old_session_id and new_session_id and old_session_id != new_session_id:
        renderer.migrate_context(old_session_id, new_session_id, session_key=session_key)
    ctx = _context_for(renderer, new_session_id or old_session_id, session_key)
    if ctx is None or not ctx.assistant_enabled or not renderer.settings.assistant.enabled:
        return False
    if phase == "started":
        text = "Compacting context — summarizing earlier conversation"
    elif phase == "completed":
        text = _compression_lifecycle_completed_text(data)
    elif phase == "failed":
        text = "Context compaction failed — continuing unchanged"
    else:
        return False
    return _schedule_render(
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
    before_count = _int_kw(data.get("before_count"), 0)
    after_count = _int_kw(data.get("after_count"), 0)
    before_tokens = _int_kw(data.get("before_tokens"), 0)
    after_tokens = _int_kw(data.get("after_tokens"), 0)
    if before_count and after_count and after_count < before_count:
        text = f"Context compacted · {before_count} → {after_count} messages"
    else:
        text = "Context compaction checked"
    if before_tokens and after_tokens:
        text += f" · {_compact_count(before_tokens)} → {_compact_count(after_tokens)} tokens"
    return text


def _compact_count(value: int) -> str:
    if value >= 1000:
        return f"{round(value / 1000):.0f}k"
    return str(value)


def on_assistant_progress_from_agent(
    agent: Any, text: str, *, already_streamed: bool = False
) -> bool:
    clean = str(text or "").strip()
    if not clean:
        _record_assistant_capture("empty", already_streamed=already_streamed)
        return False
    renderer = _get_renderer()
    session_id = _agent_session_id(agent)
    session_key = _agent_session_key(agent)
    ctx = _context_for(renderer, session_id, session_key)
    if ctx is None:
        _record_assistant_capture(
            "no_context",
            session_id=session_id,
            session_key=session_key,
            text=clean,
            already_streamed=already_streamed,
        )
        return False
    if not ctx.assistant_enabled or not renderer.settings.assistant.enabled:
        _record_assistant_capture(
            "disabled",
            session_id=ctx.session_id,
            session_key=ctx.session_key,
            text=clean,
            already_streamed=already_streamed,
        )
        return False
    scheduled = _schedule_render(
        ctx,
        AssistantEvent(
            ctx.session_id,
            ctx.session_key,
            ctx.platform,
            clean,
            already_streamed=already_streamed,
        ),
    )
    _record_assistant_capture(
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
    renderer = _get_renderer()
    session_id = _agent_session_id(parent_agent)
    session_key = _agent_session_key(parent_agent)
    ctx = renderer.find_context(session_id, session_key)
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
    _schedule_render(ctx, event)


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
    renderer: ProgressRenderer,
    session_id: str = "",
    platform: str = "",
    session_key: str = "",
):
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
    session_id: str = "", platform: str = "", session_key: str = "", *, purge: bool = False
) -> None:
    renderer = _get_renderer()
    ctx = _finalize_target_context(renderer, session_id, platform, session_key)
    if ctx is None or ctx.loop is None:
        if purge:
            renderer.purge(session_id=session_id, platform=platform)
        return
    try:
        future = asyncio.run_coroutine_threadsafe(
            renderer.finalize(session_id=ctx.session_id, purge=purge), ctx.loop
        )

        def _consume_done(fut):
            try:
                fut.result()
            except Exception as exc:
                logger.debug("hermes-progress-tail finalize failed: %s", exc)

        future.add_done_callback(_consume_done)
    except Exception as exc:
        logger.debug("hermes-progress-tail finalize schedule failed: %s", exc)


def _on_post_llm_call(session_id: str = "", agent: Any = None, **_: Any):
    if agent is not None:
        try:
            from ..hooks.monkeypatches import _reset_inline_reasoning_state

            _reset_inline_reasoning_state(agent)
        except Exception:
            logger.debug("hermes-progress-tail inline think reset failed", exc_info=True)
    _schedule_finalize(session_id=session_id, session_key=_agent_session_key(agent))
    return None


def _on_session_reset(session_id: str = "", platform: str = "", agent: Any = None, **_: Any):
    if agent is not None:
        try:
            from ..hooks.monkeypatches import _reset_inline_reasoning_state

            _reset_inline_reasoning_state(agent)
        except Exception:
            logger.debug("hermes-progress-tail inline think reset failed", exc_info=True)
    _get_renderer().purge(session_id=session_id, platform=platform)
    return None


def _on_session_finalize(session_id: str = "", platform: str = "", agent: Any = None, **_: Any):
    if agent is not None:
        try:
            from ..hooks.monkeypatches import _reset_inline_reasoning_state

            _reset_inline_reasoning_state(agent)
        except Exception:
            logger.debug("hermes-progress-tail inline think reset failed", exc_info=True)
    _schedule_finalize(
        session_id=session_id, platform=platform, session_key=_agent_session_key(agent), purge=True
    )
    return None


def _command(raw_args: str = "") -> str:
    args = (raw_args or "").strip().lower()
    renderer = _get_renderer()
    if args in {"jobs", "jobs all"}:
        include_all = args == "jobs all"
        lines = [
            f"background_jobs={'enabled' if renderer.settings.background_jobs.enabled else 'disabled'}"
        ]
        for sid, ctx in renderer.sessions.items():
            for process_id in ctx.background_order:
                job = ctx.background_jobs.get(process_id)
                if job is None:
                    continue
                if not include_all and job.status != "running":
                    continue
                command = redact_text(job.command or process_id)
                lines.append(
                    f"{process_id} {job.status} exit={job.exit_code} session={ctx.session_key or sid} {command}"
                )
        return "\n".join(lines)
    if args in {"", "status", "doctor"}:
        active = len(renderer.sessions)
        monkeypatch_active = False
        delegate_monkeypatch_active = False
        try:
            from run_agent import AIAgent

            monkeypatch_active = bool(getattr(AIAgent, "_hermes_progress_tail_patched", False))
        except Exception:
            monkeypatch_active = False
        try:
            from tools import delegate_tool

            delegate_monkeypatch_active = bool(
                getattr(delegate_tool, "_hermes_progress_tail_delegate_patched", False)
            )
        except Exception:
            delegate_monkeypatch_active = False
        settings = renderer.settings
        runtime_config = _load_runtime_config()
        display = (
            runtime_config.get("display") if isinstance(runtime_config.get("display"), dict) else {}
        )
        plugins = (
            runtime_config.get("plugins") if isinstance(runtime_config.get("plugins"), dict) else {}
        )
        enabled_plugins = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
        agent_config = (
            runtime_config.get("agent") if isinstance(runtime_config.get("agent"), dict) else {}
        )
        capture_at = float(_ASSISTANT_CAPTURE.get("updated_at") or 0.0)
        capture_when = (
            time.strftime("%H:%M:%S", time.localtime(capture_at)) if capture_at else "never"
        )
        lines = [
            f"hermes-progress-tail {VERSION}",
            f"plugin={'enabled' if 'hermes-progress-tail' in enabled_plugins else 'not listed'}",
            f"sessions={active}",
            f"agent.gateway_notify_interval={agent_config.get('gateway_notify_interval', '<default:180>')}",
            f"tools={'enabled' if settings.tools.enabled else 'disabled'} lines={settings.tools.lines} completed={settings.tools.show_completed} duration={settings.tools.show_duration} timestamp={settings.tools.timestamp_format if settings.tools.timestamp else 'off'}",
            f"todo=sticky:{settings.todo.sticky} hide_tool_line:{settings.todo.hide_tool_line}",
            f"patch=detail:{settings.patch.detail} preview_chars:{settings.patch.preview_chars} max_files:{settings.patch.max_files}",
            f"assistant={'enabled' if settings.assistant.enabled else 'disabled'} max_lines={settings.assistant.max_lines} max_chars={settings.assistant.max_chars}",
            f"assistant_capture={_ASSISTANT_CAPTURE.get('status', 'never')} already_streamed={_ASSISTANT_CAPTURE.get('already_streamed', False)} session={_ASSISTANT_CAPTURE.get('session_id') or '-'} key_present={_ASSISTANT_CAPTURE.get('session_key_present', False)} at={capture_when}",
            f"reasoning={'enabled' if settings.reasoning.enabled else 'disabled'} max_lines={settings.reasoning.max_lines} max_chars={settings.reasoning.max_chars}",
            "reasoning_sources=structured_reasoning,inline_think,provider_delimiters",
            f"delegates={'enabled' if settings.delegates.enabled else 'disabled'} max={settings.delegates.max_delegates} lines={settings.delegates.lines_per_delegate} thinking={settings.delegates.thinking}",
            f"background_jobs={'enabled' if settings.background_jobs.enabled else 'disabled'} list_running={settings.background_jobs.list_running} show_completed={settings.background_jobs.show_completed} max={settings.background_jobs.max_jobs} ttl={settings.background_jobs.completed_ttl_seconds}s head={settings.background_jobs.head_lines} tail={settings.background_jobs.tail_lines} update={settings.background_jobs.update_interval_seconds}s suppress_native_notify={settings.background_jobs.suppress_native_notify} suppress_watch={settings.background_jobs.suppress_watch_notifications}",
            f"renderer=mode:{settings.renderer.mode} strategy:{settings.renderer.strategy} style:{settings.renderer.style} density:{settings.renderer.density} edit_interval:{settings.renderer.edit_interval} agent_label:{settings.renderer.agent_label or '-'}",
            f"display.tool_progress={display.get('tool_progress', '<unset>')}",
            f"display.show_reasoning={display.get('show_reasoning', '<unset>')}",
            f"monkeypatch={monkeypatch_active}",
            f"delegate_monkeypatch={delegate_monkeypatch_active}",
        ]
        if args == "doctor":
            if display.get("tool_progress") != "off":
                lines.append("warning: display.tool_progress is not off; progress may duplicate")
            if _builtin_interim_conflict(runtime_config):
                lines.append(_interim_conflict_warning())
            if _builtin_reasoning_conflict(runtime_config):
                lines.append(_reasoning_conflict_warning())
            if _core_notifier_conflict(runtime_config):
                lines.append(_core_notifier_conflict_warning())
            lines.extend(_background_job_config_warnings(settings))
            for key in find_retired_config_keys(runtime_config):
                lines.append(
                    f"warning: retired config key {key}; remove it from progress_tail config"
                )
            for key in find_unknown_config_keys(runtime_config):
                lines.append(f"warning: unknown config key {key}; check for typos or stale docs")
            for sid, ctx in renderer.sessions.items():
                label = ctx.session_key or sid
                lines.append(
                    f"session {label}: strategy={ctx.strategy} disabled={ctx.disabled} events={ctx.total_events}"
                )
                if ctx.downgrade_reason:
                    lines.append(f"session {label}: downgraded={redact_text(ctx.downgrade_reason)}")
                if ctx.last_error:
                    lines.append(f"session {label}: last_error={redact_text(ctx.last_error)}")
                if ctx.last_assistant_at:
                    when = time.strftime("%H:%M:%S", time.localtime(ctx.last_assistant_at))
                    lines.append(
                        f"session {label}: assistant chars={ctx.last_assistant_chars} at={when}"
                    )
                if ctx.last_reasoning_source:
                    when = time.strftime("%H:%M:%S", time.localtime(ctx.last_reasoning_at))
                    lines.append(
                        f"session {label}: last_reasoning source={ctx.last_reasoning_source} chars={ctx.last_reasoning_chars} at={when}"
                    )
        else:
            if _builtin_interim_conflict(runtime_config):
                lines.append(_interim_conflict_warning())
            if _builtin_reasoning_conflict(runtime_config):
                lines.append(_reasoning_conflict_warning())
            if _core_notifier_conflict(runtime_config):
                lines.append(_core_notifier_conflict_warning())
        return "\n".join(lines)
    if args in {"test", "demo", "demo plain", "demo failed"}:
        return _demo_command(plain=args == "demo plain", failed=args == "demo failed")
    return "Usage: /progresstail status | doctor | jobs [all] | demo [plain|failed]"


def _demo_command(*, plain: bool = False, failed: bool = False) -> str:
    from ..models.state import (
        DelegateEvent,
        SessionContext,
        TodoItem,
    )

    renderer = ProgressRenderer(
        load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False, "lines": 4},
                    "renderer": {"mode": "focused", "density": "verbose", "style": "emoji"},
                    "delegates": {"lines_per_delegate": 5, "max_line_chars": 180},
                }
            }
        )
    )
    platform = "sms" if plain else "telegram"
    ctx = SessionContext(
        "demo-session",
        "demo-session-key",
        platform,
        "demo-chat",
        None,
        None,
        None,
        "live_tail",
        timestamp=False,
    )
    ctx.agent_label = "Hermes"
    ctx.todo_items = (
        TodoItem("Inspect renderer", "completed"),
        TodoItem("Build deterministic demo", "in_progress"),
        TodoItem("Run tests", "pending"),
        TodoItem("Review release", "pending"),
    )
    ctx.tool_started_count = 5
    ctx.tool_completed_count = 4
    ctx.tool_failed_count = 1 if failed else 0
    renderer.delegate_renderer.apply_event(
        ctx,
        DelegateEvent(
            "demo-session",
            "demo-session-key",
            platform,
            "demo-agent",
            task_index=0,
            task_count=1,
            goal="demo UI review",
            event_type="subagent.start",
            status="running",
            created_at=1,
        ),
    )
    for index, (tool_name, preview, args) in enumerate(
        (
            ("read_file", "hermes_progress_tail/rendering/focused.py:1+120", {}),
            ("search_files", "focused_block", {"pattern": "focused_block"}),
            (
                "terminal",
                "python -m pytest tests/test_renderer.py -q",
                {"command": "python -m pytest tests/test_renderer.py -q"},
            ),
            ("read_file", "tests/test_focused_live_markdown.py:1+80", {}),
        ),
        start=2,
    ):
        renderer.delegate_renderer.apply_event(
            ctx,
            DelegateEvent(
                "demo-session",
                "demo-session-key",
                platform,
                "demo-agent",
                task_index=0,
                task_count=1,
                goal="demo UI review",
                event_type="subagent.tool",
                tool_name=tool_name,
                preview=preview,
                args=args,
                status="running",
                created_at=index,
            ),
        )
    renderer.delegate_renderer.apply_event(
        ctx,
        DelegateEvent(
            "demo-session",
            "demo-session-key",
            platform,
            "demo-agent",
            task_index=0,
            task_count=1,
            goal="demo UI review",
            event_type="subagent.complete",
            status="completed",
            duration_seconds=12,
            summary="demo smoke check passed",
            created_at=8,
        ),
    )
    ctx.tool_lines.extend(
        [
            "✅ read_file: rendering/focused.py:1+120 · done · 0.2s",
            "✅ search_files: focused_block · done · 0.1s",
            (
                "❌ terminal: pytest tests/test_renderer.py -q · failed · 2.1s"
                if failed
                else "✅ terminal: pytest tests/test_renderer.py -q · done · 2.1s"
            ),
            "terminal: git diff --check · running",
        ]
    )
    return renderer._content(ctx)


def register(ctx):
    runtime_config = _load_runtime_config()
    if _builtin_interim_conflict(runtime_config):
        logger.warning(_interim_conflict_warning())
    if _builtin_reasoning_conflict(runtime_config):
        logger.warning(_reasoning_conflict_warning())
    if _core_notifier_conflict(runtime_config):
        logger.warning(_core_notifier_conflict_warning())
    install_monkeypatches()
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("on_session_reset", _on_session_reset)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
    ctx.register_command(
        "progresstail",
        _command,
        description="Show hermes-progress-tail plugin status",
        args_hint="status|doctor|jobs|demo",
    )
