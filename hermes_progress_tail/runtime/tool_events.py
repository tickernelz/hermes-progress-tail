from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from ..models.state import (
    AssistantEvent,
    BackgroundJobEvent,
    DelegateEvent,
    ReasoningEvent,
    SessionContext,
    ToolEvent,
)
from ..rendering.formatter import extract_todo_items, format_tool_line
from .context import _context_for

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..rendering.renderer import ProgressRenderer


def _schedule_render(
    ctx: SessionContext,
    event: ToolEvent | ReasoningEvent | AssistantEvent | DelegateEvent | BackgroundJobEvent,
    *,
    force: bool = False,
) -> bool:
    from . import plugin as runtime_plugin

    renderer = runtime_plugin._get_renderer()
    if ctx.loop is None:
        logger.debug(
            "hermes-progress-tail render skipped: no event loop for session_id=%s session_key_present=%s event=%s",
            ctx.session_id,
            bool(ctx.session_key),
            type(event).__name__,
        )
        return False
    try:
        logger.debug(
            "hermes-progress-tail schedule render: event=%s session_id=%s session_key_present=%s force=%s",
            type(event).__name__,
            ctx.session_id,
            bool(ctx.session_key),
            force,
        )
        future = asyncio.run_coroutine_threadsafe(
            renderer.handle_event(event, force=force), ctx.loop
        )

        def _consume_done(fut):
            try:
                fut.result()
            except Exception as exc:
                logger.debug("hermes-progress-tail render failed: %s", exc)

        future.add_done_callback(_consume_done)
        if isinstance(event, BackgroundJobEvent) and _background_job_event_is_terminal(event):
            runtime_plugin._schedule_background_job_cleanup(ctx, event.process_id)
        return True
    except Exception as exc:
        logger.debug("hermes-progress-tail schedule failed: %s", exc)
        return False


def _background_job_event_is_terminal(event: BackgroundJobEvent) -> bool:
    return bool(event.exited or event.event_type in {"completed", "killed", "lost"})


def _compact_result_status(result: Any) -> str:
    if result is None or result == "":
        return "done"
    data = result
    if isinstance(result, str):
        try:
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
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _terminal_background_requested(args: dict | None) -> bool:
    return isinstance(args, dict) and bool(args.get("background"))


def _suppress_native_background_notify(process_id: str) -> None:
    if not process_id:
        return
    from . import plugin as runtime_plugin

    try:
        from tools.process_registry import process_registry

        session = process_registry.get(process_id)
        if session is not None:
            if runtime_plugin._get_renderer().settings.background_jobs.suppress_native_notify:
                session.notify_on_complete = False
                session.watcher_interval = 0
            if runtime_plugin._get_renderer().settings.background_jobs.suppress_watch_notifications:
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


def _schedule_background_job_cleanup(ctx: SessionContext, process_id: str) -> None:
    from . import plugin as runtime_plugin

    if (
        not process_id
        or ctx.loop is None
        or not runtime_plugin._get_renderer().settings.background_jobs.enabled
    ):
        return

    async def _cleanup() -> None:
        await asyncio.sleep(
            runtime_plugin._get_renderer().settings.background_jobs.completed_ttl_seconds
        )
        runtime_plugin._schedule_render(
            ctx,
            BackgroundJobEvent(
                ctx.session_id,
                ctx.session_key,
                ctx.platform,
                process_id,
                event_type="cleanup",
            ),
            force=True,
        )

    ctx.loop.create_task(_cleanup())


def _schedule_background_job_poll(ctx: SessionContext, process_id: str) -> None:
    from . import plugin as runtime_plugin

    if (
        not process_id
        or ctx.loop is None
        or not runtime_plugin._get_renderer().settings.background_jobs.enabled
    ):
        return
    job = ctx.background_jobs.get(process_id)
    if job is not None and job.poll_task is not None and not job.poll_task.done():
        return

    async def _poll() -> None:
        try:
            while True:
                await asyncio.sleep(
                    runtime_plugin._get_renderer().settings.background_jobs.update_interval_seconds
                )
                try:
                    from tools.process_registry import process_registry

                    session = process_registry.get(process_id)
                except Exception:
                    session = None
                if session is None:
                    runtime_plugin._schedule_render(
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
                runtime_plugin._schedule_render(
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
    thread_name = threading.current_thread().name
    return thread_name == "bg-review" or thread_name.startswith("bg-review:")


def _is_context_owner_thread(ctx: SessionContext) -> bool:
    owner_thread_id = int(getattr(ctx, "owner_thread_id", 0) or 0)
    return not owner_thread_id or owner_thread_id == threading.get_ident()


def _context_for_non_background_thread(
    renderer: ProgressRenderer, session_id: str = "", session_key: str = ""
) -> SessionContext | None:
    if _is_background_review_thread():
        logger.debug(
            "hermes-progress-tail ignored background-review event: thread=%s session_id=%s "
            "session_key_present=%s",
            threading.current_thread().name,
            session_id,
            bool(session_key),
        )
        return None
    ctx = _context_for(renderer, session_id, session_key)
    if ctx is None:
        logger.debug(
            "hermes-progress-tail context lookup missed: thread=%s session_id=%s session_key_present=%s",
            threading.current_thread().name,
            session_id,
            bool(session_key),
        )
    return ctx


def _context_owned_by_current_thread(
    renderer: ProgressRenderer, session_id: str = "", session_key: str = ""
) -> SessionContext | None:
    ctx = _context_for(renderer, session_id, session_key)
    if ctx is None:
        return None
    return ctx if _is_context_owner_thread(ctx) else None


def _tool_agent_context(
    tool_name: str, session_id: str = "", task_id: str = "", tool_call_id: str = ""
) -> dict[str, Any]:
    try:
        from ..hooks.monkeypatches import current_tool_agent_context

        return current_tool_agent_context(
            tool_name=tool_name,
            session_id=session_id,
            task_id=task_id,
            tool_call_id=tool_call_id,
        )
    except Exception:
        return {}


def _resolve_tool_agent(
    agent: Any, tool_name: str, session_id: str = "", task_id: str = "", tool_call_id: str = ""
) -> tuple[Any, list[dict[str, Any]] | None]:
    if agent is not None:
        return agent, None
    context = _tool_agent_context(tool_name, session_id, task_id, tool_call_id)
    return context.get("agent"), context.get("messages")


def _on_pre_tool_call(
    tool_name: str,
    args: dict | None = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    preview: str | None = None,
    agent: Any = None,
    **_: Any,
):
    from . import plugin as runtime_plugin

    renderer = runtime_plugin._get_renderer()
    ctx = runtime_plugin._context_for_non_background_thread(
        renderer, session_id or task_id, task_id
    )
    if ctx is None:
        return None
    if not ctx.tools_enabled:
        logger.debug(
            "hermes-progress-tail ignored tool event because tools disabled: tool=%s", tool_name
        )
        return None
    logger.debug(
        "hermes-progress-tail pre_tool_call: tool=%s session_id=%s session_key_present=%s "
        "thread=%s tool_call_id=%s",
        tool_name,
        ctx.session_id,
        bool(ctx.session_key),
        threading.current_thread().name,
        tool_call_id,
    )
    agent, messages = runtime_plugin._resolve_tool_agent(
        agent, tool_name, session_id, task_id, tool_call_id
    )
    runtime_plugin._update_environment_from_agent(ctx, agent, messages=messages)
    if tool_name == "terminal":
        runtime_plugin._update_environment_from_terminal(ctx, args, task_id)
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
    runtime_plugin._schedule_render(ctx, event)
    return None


def _on_post_tool_call(
    tool_name: str,
    args: dict | None = None,
    result: str = "",
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: int | None = None,
    agent: Any = None,
    **_: Any,
):
    from . import plugin as runtime_plugin

    renderer = runtime_plugin._get_renderer()
    ctx = runtime_plugin._context_for_non_background_thread(
        renderer, session_id or task_id, task_id
    )
    if ctx is None:
        return None
    if not ctx.tools_enabled:
        logger.debug(
            "hermes-progress-tail ignored post-tool event because tools disabled: tool=%s",
            tool_name,
        )
        return None
    logger.debug(
        "hermes-progress-tail post_tool_call: tool=%s session_id=%s session_key_present=%s "
        "thread=%s status=%s duration_ms=%s tool_call_id=%s",
        tool_name,
        ctx.session_id,
        bool(ctx.session_key),
        threading.current_thread().name,
        _compact_result_status(result),
        duration_ms,
        tool_call_id,
    )
    agent, messages = runtime_plugin._resolve_tool_agent(
        agent, tool_name, session_id, task_id, tool_call_id
    )
    runtime_plugin._update_environment_from_agent(ctx, agent, messages=messages)
    if tool_name == "terminal":
        runtime_plugin._update_environment_from_terminal(ctx, args, task_id)
    result_obj = _json_obj(result)
    if tool_name == "terminal" and _terminal_background_requested(args):
        process_id = str(result_obj.get("session_id") or "")
        if process_id and renderer.settings.background_jobs.enabled and ctx.background_jobs_enabled:
            runtime_plugin._suppress_native_background_notify(process_id)
            runtime_plugin._schedule_render(
                ctx,
                BackgroundJobEvent(
                    ctx.session_id,
                    ctx.session_key,
                    ctx.platform,
                    process_id,
                    event_type="completed" if result_obj.get("exited") else "started",
                    command=str((args or {}).get("command") or ""),
                    cwd=str((args or {}).get("workdir") or ""),
                    pid=result_obj.get("pid"),
                    output=str(result_obj.get("output") or ""),
                    exited=bool(result_obj.get("exited", False)),
                    exit_code=result_obj.get("exit_code"),
                ),
            )
            if not result_obj.get("exited"):
                runtime_plugin._schedule_background_job_poll(ctx, process_id)
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
    runtime_plugin._schedule_render(
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
