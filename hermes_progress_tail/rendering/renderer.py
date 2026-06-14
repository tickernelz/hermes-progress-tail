from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..models.state import (
    AssistantEvent,
    AssistantLine,
    BackgroundJob,
    BackgroundJobEvent,
    DelegateEvent,
    ProgressEvent,
    ReasoningEvent,
    SessionContext,
    ToolEvent,
)
from ..settings.config import Settings
from .background_jobs import (
    apply_background_job_event,
    background_jobs_section,
    cancel_background_poll,
)
from .delegate import DelegateProgressRenderer
from .delegate import event_preview_args as event_preview_args
from .delivery import (
    _cancel_delayed_flush,
    _cancel_delete,
    _classify_edit_error,
    _downgrade_to_snapshot,
    _edit_backoff_seconds,
    _fit_message,
    _message_limit,
    _prepare_message,
    _prepare_telegram_rich_message,
    _render_live,
    _render_snapshot,
    _schedule_auto_delete,
    _schedule_delayed_live_flush,
    _send_live_message,
)
from .finalization import (
    finalize_progress_message,
    has_background_jobs,
    reset_turn,
    should_flush_before_reset,
)
from .reasoning import normalize_reasoning_text, render_reasoning_tail, split_reasoning_blocks
from .sections import (
    assistant_tail,
    compose_content,
    debug_section,
    format_tool_line_for_context,
    section,
    timestamp_text,
    todo_section,
)
from .session import (
    _same_source_message,
    find_context,
    migrate_context,
    purge,
    register_context,
)

logger = logging.getLogger(__name__)


class ProgressRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.delegate_renderer = DelegateProgressRenderer(settings)
        self.sessions: dict[str, SessionContext] = {}
        self.session_keys: dict[str, str] = {}

    async def handle_event(self, event: ProgressEvent, force: bool = False) -> None:
        ctx = self.find_context(event.session_id, event.session_key)
        if ctx is None:
            return
        async with ctx.lock:
            if ctx.disabled or ctx.strategy == "off":
                return
            if ctx.progress_state != "active":
                if (
                    isinstance(event, BackgroundJobEvent)
                    and ctx.progress_state == "background_active"
                ):
                    pass
                else:
                    return
            if isinstance(event, ToolEvent) and not ctx.tools_enabled:
                return
            if not isinstance(event, AssistantEvent):
                self._clear_transient_assistant(ctx)
            if isinstance(event, AssistantEvent) and not ctx.assistant_enabled:
                return
            if isinstance(event, DelegateEvent) and not ctx.delegates_enabled:
                return
            if isinstance(event, BackgroundJobEvent) and not self._background_jobs_enabled(ctx):
                return
            if isinstance(event, ReasoningEvent) and not ctx.reasoning_enabled:
                return
            ctx.last_event_at = time.monotonic()
            ctx.total_events += 1
            ctx.new_events_since_snapshot += 1
            if isinstance(event, ToolEvent):
                if event.tool_name == "todo" and event.todo_items:
                    if self.settings.todo.sticky:
                        ctx.todo_items = event.todo_items
                        ctx.todo_updated_at = event.created_at
                    if self.settings.todo.hide_tool_line:
                        await self._render_for_strategy(ctx, event, force=force)
                        return
                line = self._format_tool_line(ctx, event)
                if event.replace_existing:
                    previous = self._find_previous_tool_line(ctx, event, line)
                    terminal_status = self._record_tool_lifecycle(ctx, event, line)
                    if terminal_status:
                        self._clear_previous_tool_tracking(ctx, event, previous)
                    if previous in ctx.tool_lines:
                        items = list(ctx.tool_lines)
                        items[items.index(previous)] = line
                        ctx.tool_lines.clear()
                        ctx.tool_lines.extend(items)
                    else:
                        ctx.tool_lines.append(line)
                    if not terminal_status:
                        self._clear_previous_tool_tracking(ctx, event, previous)
                        if event.tool_call_id:
                            ctx.active_tool_lines[event.tool_call_id] = line
                        fingerprint = self._tool_line_fingerprint(line)
                        if fingerprint:
                            ctx.active_tool_fingerprints[fingerprint] = line
                    force = True
                else:
                    self._record_tool_lifecycle(ctx, event, line)
                    ctx.tool_lines.append(line)
                    if event.tool_call_id:
                        ctx.active_tool_lines[event.tool_call_id] = line
                    fingerprint = self._tool_line_fingerprint(line)
                    if fingerprint:
                        ctx.active_tool_fingerprints[fingerprint] = line
            elif isinstance(event, DelegateEvent):
                self._apply_delegate_event(ctx, event)
            elif isinstance(event, BackgroundJobEvent):
                self._apply_background_job_event(ctx, event)
                force = True
            elif isinstance(event, AssistantEvent):
                pending = self._append_assistant(ctx, event)
                if (
                    not force
                    and ctx.message_id
                    and time.monotonic() - ctx.last_render_at < ctx.edit_interval
                ):
                    return
                if not force and pending < self.settings.assistant.min_update_chars:
                    return
            else:
                pending = self._append_reasoning(ctx, event)
                if (
                    not force
                    and ctx.message_id
                    and time.monotonic() - ctx.last_render_at < ctx.edit_interval
                ):
                    return
                if not force and pending < self.settings.reasoning.min_update_chars:
                    return
            await self._render_for_strategy(ctx, event, force=force)

    async def _render_for_strategy(
        self, ctx: SessionContext, event: ProgressEvent, force: bool = False
    ) -> None:
        if ctx.strategy == "summary_only":
            return
        if ctx.strategy == "live_tail":
            await self._render_live(ctx, force=force)
            return
        if ctx.strategy == "snapshot":
            if (
                isinstance(event, ReasoningEvent)
                and self.settings.reasoning.no_edit_strategy == "off"
            ):
                return
            await self._render_snapshot(ctx, force=force)

    async def finalize(
        self,
        session_id: str = "",
        session_key: str = "",
        purge: bool = False,
        *,
        success: bool = True,
        generation: int | None = None,
    ) -> None:
        ctx = self.find_context(session_id, session_key)
        if ctx is None:
            return
        if generation is not None and ctx.generation != generation:
            return
        async with ctx.lock:
            if generation is not None and (
                self.sessions.get(ctx.session_id) is not ctx or ctx.generation != generation
            ):
                return
            if ctx.disabled:
                self._cancel_delayed_flush(ctx)
                return
            self._cancel_delayed_flush(ctx)
            progress_message_id = ctx.message_id
            if self._should_flush_before_reset(ctx):
                if ctx.strategy == "live_tail" and self._content(ctx):
                    await self._render_live(ctx, force=True, ignore_backoff=True)
                    progress_message_id = ctx.message_id or progress_message_id
                elif (
                    ctx.strategy == "snapshot"
                    and self.settings.no_edit.final_summary
                    and self._content(ctx)
                ):
                    await self._render_snapshot(ctx, force=True, final=True)
            self._reset_turn(ctx)
            ctx.message_id = progress_message_id
            await self._finalize_progress_message(ctx)
            self._schedule_auto_delete(ctx, success=success)
            if ctx.progress_state == "background_active" and self._content(ctx):
                if ctx.strategy == "live_tail":
                    await self._render_live(ctx, force=True, ignore_backoff=True)
                elif ctx.strategy == "snapshot" and self.settings.no_edit.final_summary:
                    await self._render_snapshot(ctx, force=True, final=True)
        if purge and (generation is None or self.sessions.get(ctx.session_id) is ctx):
            self.purge(session_id=ctx.session_id)

    def _append_assistant(self, ctx: SessionContext, event: AssistantEvent) -> int:
        text = str(event.text or "").strip()
        if not text:
            return 0
        previous = ctx.assistant_latest_text
        replace_latest = bool(previous and (text.startswith(previous) or previous.startswith(text)))
        if ctx.assistant_transient and not event.transient:
            ctx.assistant_lines.clear()
            previous = ""
            replace_latest = False
            ctx.assistant_transient = False
        if replace_latest and ctx.assistant_lines:
            ctx.assistant_lines[-1] = AssistantLine(text=text, created_at=event.created_at)
        else:
            ctx.assistant_lines.append(AssistantLine(text=text, created_at=event.created_at))
        max_lines = max(1, self.settings.assistant.max_lines)
        if ctx.assistant_lines.maxlen != max_lines:
            ctx.assistant_lines = type(ctx.assistant_lines)(
                list(ctx.assistant_lines)[-max_lines:], maxlen=max_lines
            )
        delta_chars = len(text) - len(previous) if replace_latest else len(text)
        ctx.assistant_pending_chars += max(1, delta_chars)
        ctx.assistant_latest_text = text
        ctx.last_assistant_chars = len(text)
        ctx.last_assistant_at = event.created_at
        ctx.assistant_transient = bool(event.transient)
        return ctx.assistant_pending_chars

    def _clear_transient_assistant(self, ctx: SessionContext) -> None:
        if not ctx.assistant_transient:
            return
        ctx.assistant_lines.clear()
        ctx.assistant_latest_text = ""
        ctx.assistant_pending_chars = 0
        ctx.last_assistant_chars = 0
        ctx.last_assistant_at = 0.0
        ctx.assistant_transient = False

    def _append_reasoning(self, ctx: SessionContext, event: ReasoningEvent) -> int:
        if not event.text:
            return 0
        merged = ctx.reasoning_text + str(event.text)
        merged = self._normalize_reasoning(merged)
        max_chars = self.settings.reasoning.max_chars
        if len(merged) > max_chars * 4:
            merged = self._trim_reasoning_buffer(merged, max_chars * 4)
        ctx.reasoning_text = merged
        ctx.reasoning_pending_chars += len(str(event.text))
        ctx.last_reasoning_source = event.source or "structured_reasoning"
        ctx.last_reasoning_chars = len(str(event.text))
        ctx.last_reasoning_at = event.created_at
        return ctx.reasoning_pending_chars

    def _record_tool_lifecycle(self, ctx: SessionContext, event: ToolEvent, line: str) -> bool:
        if event.tool_name == "todo":
            return False
        identity = self._tool_event_identity(event, line)
        if not event.replace_existing:
            self._complete_active_tools(ctx)
            ctx.tool_started_count += 1
            return False
        terminal_status = self._tool_line_terminal_status(line)
        if not terminal_status:
            return False
        if identity in ctx.completed_tool_ids:
            return True
        if terminal_status == "failed":
            ctx.tool_failed_count += 1
        else:
            ctx.tool_completed_count += 1
        ctx.completed_tool_ids.add(identity)
        if event.tool_call_id:
            ctx.active_tool_lines.pop(event.tool_call_id, None)
        fingerprint = self._tool_line_fingerprint(line)
        if fingerprint:
            ctx.active_tool_fingerprints.pop(fingerprint, None)
        return True

    @staticmethod
    def _tool_line_terminal_status(line: str) -> str:
        text = str(line or "").strip().lower()
        if text.startswith("❌") or " · failed" in text:
            return "failed"
        if text.startswith("✅") or " · done" in text:
            return "done"
        return ""

    def _complete_active_tools(self, ctx: SessionContext) -> None:
        identities: set[str] = set()
        active_lines = set(ctx.active_tool_lines.values())
        for tool_call_id in ctx.active_tool_lines:
            identities.add("id:" + tool_call_id)
        for fingerprint, line in ctx.active_tool_fingerprints.items():
            if line not in active_lines:
                identities.add("fp:" + fingerprint)
        new_completions = identities - ctx.completed_tool_ids
        if new_completions:
            ctx.tool_completed_count += len(new_completions)
            ctx.completed_tool_ids.update(new_completions)
        ctx.active_tool_lines.clear()
        ctx.active_tool_fingerprints.clear()

    def _clear_previous_tool_tracking(
        self, ctx: SessionContext, event: ToolEvent, previous: str
    ) -> None:
        if event.tool_call_id:
            ctx.active_tool_lines.pop(event.tool_call_id, None)
        fingerprint = self._tool_line_fingerprint(previous)
        if fingerprint:
            ctx.active_tool_fingerprints.pop(fingerprint, None)

    def _tool_event_identity(self, event: ToolEvent, line: str) -> str:
        if event.tool_call_id:
            return "id:" + event.tool_call_id
        fingerprint = self._tool_line_fingerprint(line)
        if fingerprint:
            return "fp:" + fingerprint
        return "line:" + line.strip()

    def _find_previous_tool_line(self, ctx: SessionContext, event: ToolEvent, line: str) -> str:
        if event.tool_call_id:
            previous = ctx.active_tool_lines.get(event.tool_call_id, "")
            if previous:
                return previous
        fingerprint = self._tool_line_fingerprint(line)
        if fingerprint:
            previous = ctx.active_tool_fingerprints.get(fingerprint, "")
            if previous:
                return previous
        return ""

    @staticmethod
    def _tool_line_fingerprint(line: str) -> str:
        text = line.strip()
        if "] " in text and text.startswith("["):
            text = text.split("] ", 1)[1]
        for prefix in (
            "✅ ",
            "❌ ",
            "🔎 ",
            "📖 ",
            "✍️ ",
            "🔧 ",
            "💻 ",
            "📋 ",
            "🧑‍💻 ",
            "🧰 ",
        ):
            if text.startswith(prefix):
                text = text[len(prefix) :]
        for suffix in (" · running", " · done", " · failed"):
            if suffix in text:
                text = text.split(suffix, 1)[0]
                break
        return text.strip()

    def _content(self, ctx: SessionContext) -> str:
        return compose_content(self, ctx)

    def _section(self, title: str, emoji: str, body: str) -> str:
        return section(title, emoji, body, style=self.settings.renderer.style)

    def _debug_section(self, ctx: SessionContext) -> str:
        return debug_section(ctx, section=self._section)

    def _format_tool_line(self, ctx: SessionContext, event: ToolEvent) -> str:
        return format_tool_line_for_context(
            ctx,
            event,
            timestamp_enabled=self.settings.tools.timestamp,
            timestamp_format=self.settings.tools.timestamp_format,
        )

    def _todo_section(self, ctx: SessionContext) -> str:
        return todo_section(ctx, settings=self.settings)

    @staticmethod
    def _timestamp(value: float, fmt: str) -> str:
        return timestamp_text(value, fmt)

    def _apply_background_job_event(self, ctx: SessionContext, event: BackgroundJobEvent) -> None:
        apply_background_job_event(
            ctx,
            event,
            settings=self.settings.background_jobs,
            cancel_poll=self._cancel_background_poll,
        )

    def _apply_delegate_event(self, ctx: SessionContext, event: DelegateEvent) -> None:
        self.delegate_renderer.apply_event(ctx, event)
        if self._delegate_event_is_terminal(event):
            key = event.subagent_id or f"task-{event.task_index}"
            branch = ctx.delegate_branches.get(key)
            if branch is not None:
                self._schedule_delegate_cleanup(ctx, key, branch)

    def _schedule_delegate_cleanup(
        self, ctx: SessionContext, subagent_id: str, branch: Any
    ) -> None:
        if not subagent_id or ctx.loop is None:
            return
        if branch.cleanup_task is not None and not branch.cleanup_task.done():
            return

        async def _cleanup() -> None:
            try:
                await asyncio.sleep(self.settings.delegates.completed_ttl_seconds)
                async with ctx.lock:
                    current = ctx.delegate_branches.get(subagent_id)
                    if current is not branch:
                        return
                    self.delegate_renderer.prune_completed(ctx)
                    if ctx.strategy == "live_tail":
                        await self._render_live(ctx, force=True)
                    elif ctx.strategy == "snapshot":
                        await self._render_snapshot(ctx, force=True)
            except asyncio.CancelledError:
                raise
            finally:
                if branch.cleanup_task is task:
                    branch.cleanup_task = None

        task = ctx.loop.create_task(_cleanup())
        branch.cleanup_task = task

    @staticmethod
    def _delegate_event_is_terminal(event: DelegateEvent) -> bool:
        return bool(
            event.event_type in {"subagent.complete", "subagent.failed"}
            or str(event.status or "").strip().lower()
            in {"completed", "done", "success", "failed", "error", "cancelled", "killed"}
        )

    def _background_jobs_section(self, ctx: SessionContext) -> str:
        return background_jobs_section(
            ctx,
            settings=self.settings,
            section=self._section,
            background_jobs_enabled=self._background_jobs_enabled,
            cancel_poll=self._cancel_background_poll,
        )

    @staticmethod
    def _cancel_background_poll(job: BackgroundJob) -> None:
        cancel_background_poll(job)

    def _background_jobs_enabled(self, ctx: SessionContext) -> bool:
        return bool(
            self.settings.background_jobs.enabled and getattr(ctx, "background_jobs_enabled", True)
        )

    @staticmethod
    def _trim_reasoning_buffer(text: str, max_chars: int) -> str:
        blocks = split_reasoning_blocks(text)
        if blocks and blocks[-1].heading:
            latest = blocks[-1]
            heading = latest.heading
            if latest.heading_style == "bold":
                heading = f"**{heading}**"
            elif latest.heading_style == "colon":
                heading = f"{heading}:"
            elif latest.heading_style == "markdown":
                heading = f"## {heading}"
            latest_block = (heading + "\n" + latest.body).strip()
            if len(latest_block) <= max_chars:
                return latest_block
            body_budget = max_chars - len(heading) - 1
            if body_budget > 0:
                return heading + "\n" + latest.body[-body_budget:].lstrip()
        return text[-max_chars:].lstrip()

    @staticmethod
    def _reset_turn(ctx: SessionContext) -> None:
        reset_turn(ctx)

    @staticmethod
    def _has_background_jobs(ctx: SessionContext) -> bool:
        return has_background_jobs(ctx)

    def _assistant_tail(self, ctx: SessionContext) -> str:
        return assistant_tail(
            tuple(ctx.assistant_lines),
            max_lines=self.settings.assistant.max_lines,
            max_chars=self.settings.assistant.max_chars,
        )

    def _reasoning_tail(self, ctx: SessionContext) -> str:
        return render_reasoning_tail(
            ctx.reasoning_text,
            max_lines=self.settings.reasoning.max_lines,
            max_chars=self.settings.reasoning.max_chars,
            redact=self.settings.renderer.redact_secrets,
        )

    @staticmethod
    def _normalize_reasoning(text: str) -> str:
        return normalize_reasoning_text(text)

    async def _finalize_progress_message(self, ctx: SessionContext) -> None:
        finalize_progress_message(ctx)

    @staticmethod
    def _should_flush_before_reset(ctx: SessionContext) -> bool:
        return should_flush_before_reset(ctx)


# Delivery methods live in rendering.delivery to keep this orchestration class small.
ProgressRenderer._render_live = _render_live
ProgressRenderer._send_live_message = _send_live_message
ProgressRenderer._downgrade_to_snapshot = _downgrade_to_snapshot
ProgressRenderer._schedule_delayed_live_flush = _schedule_delayed_live_flush
ProgressRenderer._cancel_delayed_flush = staticmethod(_cancel_delayed_flush)
ProgressRenderer._cancel_delete = staticmethod(_cancel_delete)
ProgressRenderer._schedule_auto_delete = _schedule_auto_delete
ProgressRenderer._render_snapshot = _render_snapshot
ProgressRenderer._prepare_message = _prepare_message
ProgressRenderer._prepare_telegram_rich_message = _prepare_telegram_rich_message
ProgressRenderer._fit_message = staticmethod(_fit_message)
ProgressRenderer._message_limit = staticmethod(_message_limit)
ProgressRenderer._classify_edit_error = staticmethod(_classify_edit_error)
ProgressRenderer._edit_backoff_seconds = staticmethod(_edit_backoff_seconds)
ProgressRenderer.register_context = register_context
ProgressRenderer._same_source_message = staticmethod(_same_source_message)
ProgressRenderer.find_context = find_context
ProgressRenderer.migrate_context = migrate_context
ProgressRenderer.purge = purge
