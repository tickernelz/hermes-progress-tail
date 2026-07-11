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
    RendererDelivery,
    _classify_edit_error,
    _edit_backoff_seconds,
    _fit_message,
    _message_limit,
)
from .finalization import (
    finalize_progress_message,
    has_background_jobs,
    reset_turn,
    should_flush_before_reset,
)
from .reasoning import (
    normalize_reasoning_text,
    render_reasoning_tail,
    split_reasoning_blocks,
    split_reasoning_stream_suffix,
    trim_reasoning_fenced_tail,
)
from .sections import (
    assistant_tail,
    compose_content,
    debug_section,
    format_tool_line_for_context,
    section,
    timestamp_text,
    todo_section,
)
from .session import SessionRegistry
from .tool_helpers import tool_line_fingerprint, tool_line_terminal_status

logger = logging.getLogger(__name__)


class ProgressRenderer:
    def __init__(
        self,
        settings: Settings,
        *,
        delivery=None,
        registry=None,
        reducer=None,
        delegate_renderer=None,
        footer_info_provider=None,
    ):
        self._settings = settings
        self.delegate_renderer = delegate_renderer or DelegateProgressRenderer(settings)
        self.delivery = delivery or RendererDelivery(settings, self._content)
        self.registry = (
            registry
            if registry is not None
            else SessionRegistry(
                settings, self.delivery.cancel_delete, self.delivery.cancel_delayed_flush
            )
        )
        self.reducer = reducer
        self.footer_info_provider = footer_info_provider

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def sessions(self) -> dict[str, SessionContext]:
        return self.registry.sessions

    @property
    def session_keys(self) -> dict[str, str]:
        return self.registry.session_keys

    def replace_settings(self, settings: Settings) -> None:
        self._settings = settings
        self.delegate_renderer.settings = settings
        self.delivery.replace_settings(settings)
        for collaborator in (self.registry, self.reducer):
            if collaborator is not None:
                if hasattr(collaborator, "replace_settings"):
                    collaborator.replace_settings(settings)
                elif hasattr(collaborator, "settings"):
                    collaborator.settings = settings

    async def _render_live(self, ctx, force=False, *, ignore_backoff=False):
        return await self.delivery.render_live(ctx, force, ignore_backoff=ignore_backoff)

    async def _send_live_message(self, ctx, content, *, recovery=False):
        return await self.delivery.send_live_message(ctx, content, recovery=recovery)

    async def _downgrade_to_snapshot(self, ctx, error, state):
        return await self.delivery.downgrade_to_snapshot(ctx, error, state)

    def _schedule_delayed_live_flush(self, ctx, delay):
        return self.delivery.schedule_delayed_live_flush(ctx, delay)

    def _cancel_delayed_flush(self, ctx):
        return self.delivery.cancel_delayed_flush(ctx)

    def _cancel_delete(self, ctx):
        return self.delivery.cancel_delete(ctx)

    def _schedule_auto_delete(self, ctx, *, success):
        return self.delivery.schedule_auto_delete(ctx, success=success)

    async def _render_snapshot(self, ctx, force=False, final=False):
        return await self.delivery.render_snapshot(ctx, force, final)

    def _prepare_message(self, ctx, content):
        return self.delivery.prepare_message(ctx, content)

    def _prepare_telegram_rich_message(self, ctx, content):
        return self.delivery.prepare_telegram_rich_message(ctx, content)

    @staticmethod
    def _fit_message(content, limit):
        return _fit_message(content, limit)

    @staticmethod
    def _message_limit(ctx):
        return _message_limit(ctx)

    @staticmethod
    def _classify_edit_error(error):
        return _classify_edit_error(error)

    @staticmethod
    def _edit_backoff_seconds(error, kind, failure_count):
        return _edit_backoff_seconds(error, kind, failure_count)

    def register_context(self, ctx):
        return self.registry.register_context(ctx)

    @staticmethod
    def _same_source_message(existing, incoming):
        return SessionRegistry.same_source_message(existing, incoming)

    def find_context(self, session_id="", session_key=""):
        return self.registry.find_context(session_id, session_key)

    def migrate_context(self, old_session_id, new_session_id, session_key=""):
        return self.registry.migrate_context(old_session_id, new_session_id, session_key)

    def purge(self, session_id="", platform=""):
        return self.registry.purge(session_id, platform)

    def _record_tool_lifecycle(self, ctx, event, line):
        if event.tool_name == "todo":
            return False
        identity = self._tool_event_identity(event, line)
        if not event.replace_existing:
            self._complete_active_tools(ctx)
            ctx.tool_started_count += 1
            return False
        status = self._tool_line_terminal_status(line)
        if not status or identity in ctx.completed_tool_ids:
            return bool(status)
        if status == "failed":
            ctx.tool_failed_count += 1
        else:
            ctx.tool_completed_count += 1
        ctx.completed_tool_ids.add(identity)
        self._clear_previous_tool_tracking(ctx, event, line)
        return True

    def _complete_active_tools(self, ctx):
        active_lines = set(ctx.active_tool_lines.values())
        identities = {"id:" + key for key in ctx.active_tool_lines}
        identities.update(
            "fp:" + key
            for key, line in ctx.active_tool_fingerprints.items()
            if line not in active_lines
        )
        new = identities - ctx.completed_tool_ids
        ctx.tool_completed_count += len(new)
        ctx.completed_tool_ids.update(new)
        ctx.active_tool_lines.clear()
        ctx.active_tool_fingerprints.clear()

    def _clear_previous_tool_tracking(self, ctx, event, previous):
        if event.tool_call_id:
            ctx.active_tool_lines.pop(event.tool_call_id, None)
        fingerprint = self._tool_line_fingerprint(previous)
        if fingerprint:
            ctx.active_tool_fingerprints.pop(fingerprint, None)

    def _tool_event_identity(self, event, line):
        if event.tool_call_id:
            return "id:" + event.tool_call_id
        fingerprint = self._tool_line_fingerprint(line)
        return "fp:" + fingerprint if fingerprint else "line:" + line.strip()

    def _find_previous_tool_line(self, ctx, event, line):
        if event.tool_call_id and (previous := ctx.active_tool_lines.get(event.tool_call_id, "")):
            return previous
        fingerprint = self._tool_line_fingerprint(line)
        return ctx.active_tool_fingerprints.get(fingerprint, "") if fingerprint else ""

    _tool_line_terminal_status = staticmethod(tool_line_terminal_status)
    _tool_line_fingerprint = staticmethod(tool_line_fingerprint)

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
                if self._delegate_event_is_terminal(event):
                    force = True
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
        max_chars = self.settings.reasoning.max_chars
        buffer_limit = max(0, max_chars * 4)
        if len(merged) > buffer_limit:
            core, stream_suffix = split_reasoning_stream_suffix(
                merged,
                max_suffix_chars=max(0, buffer_limit - 1),
            )
            normalized = self._normalize_reasoning(core)
            trim_limit = buffer_limit - len(stream_suffix)
            trimmed = self._trim_reasoning_buffer(normalized, trim_limit) if trim_limit > 0 else ""
            merged = trimmed + stream_suffix
        ctx.reasoning_text = merged
        ctx.reasoning_pending_chars += len(str(event.text))
        ctx.last_reasoning_source = event.source or "structured_reasoning"
        ctx.last_reasoning_chars = len(str(event.text))
        ctx.last_reasoning_at = event.created_at
        return ctx.reasoning_pending_chars

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
        fenced_tail = trim_reasoning_fenced_tail(text, max_chars)
        if fenced_tail is not None:
            return fenced_tail
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
