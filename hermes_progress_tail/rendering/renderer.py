from __future__ import annotations

import asyncio
import time
from typing import Any

from ..models.decision import decision_is_waiting
from ..models.release import no_footer_info
from ..models.state import (
    AssistantEvent,
    BackgroundJobEvent,
    DecisionEvent,
    ProgressEvent,
    ReasoningEvent,
    SessionContext,
    ToolEvent,
)
from ..settings.types import Settings
from . import finalization as finalization_helpers
from .background_jobs import background_jobs_section, cancel_background_poll
from .clarify_checkpoint import ClarifyCheckpointController
from .delegate import DelegateProgressRenderer
from .delivery import (
    RendererDelivery,
    _classify_edit_error,
    _edit_backoff_seconds,
    _fit_message,
    _message_limit,
)
from .event_reducer import EventReducer
from .lifecycle import RendererLifecycle
from .reasoning import normalize_reasoning_text, render_reasoning_tail
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
        self.checkpoints = ClarifyCheckpointController(self.delivery)
        self.reducer = (
            reducer
            if reducer is not None
            else EventReducer(
                settings,
                self.delegate_renderer,
                schedule_delegate_cleanup=self._schedule_delegate_cleanup,
            )
        )
        self.lifecycle = RendererLifecycle(
            settings,
            self.registry,
            self.delivery,
            self.checkpoints,
            content=self._content,
            reset_turn=finalization_helpers.reset_turn,
            finalize_progress_message=finalization_helpers.finalize_progress_message,
        )
        self.footer_info_provider = (
            no_footer_info if footer_info_provider is None else footer_info_provider
        )

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
        for collaborator in (self.registry, self.reducer, self.lifecycle):
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

    _fit_message = staticmethod(_fit_message)
    _message_limit = staticmethod(_message_limit)
    _classify_edit_error = staticmethod(_classify_edit_error)
    _edit_backoff_seconds = staticmethod(_edit_backoff_seconds)

    def register_context(self, ctx):
        return self.registry.register_context(ctx)

    _same_source_message = staticmethod(SessionRegistry.same_source_message)

    def find_context(self, session_id="", session_key=""):
        return self.registry.find_context(session_id, session_key)

    def migrate_context(self, old_session_id, new_session_id, session_key=""):
        return self.registry.migrate_context(old_session_id, new_session_id, session_key)

    def purge(
        self, session_id="", platform="", session_key="", *, generation=None, expected_context=None
    ):
        return self.registry.purge(
            session_id,
            platform,
            session_key,
            generation=generation,
            expected_context=expected_context,
        )

    def _record_tool_lifecycle(self, ctx, event, line):
        return self.reducer.record_tool_lifecycle(ctx, event, line)

    def _complete_active_tools(self, ctx):
        return self.reducer.complete_active_tools(ctx)

    def _clear_previous_tool_tracking(self, ctx, event, previous):
        return self.reducer.clear_previous_tool_tracking(ctx, event, previous)

    def _tool_event_identity(self, event, line):
        return self.reducer.tool_event_identity(event, line)

    def _find_previous_tool_line(self, ctx, event, line):
        return self.reducer.find_previous_tool_line(ctx, event, line)

    _tool_line_terminal_status = staticmethod(EventReducer.tool_line_terminal_status)
    _tool_line_fingerprint = staticmethod(EventReducer.tool_line_fingerprint)

    async def freeze_clarify(self, ctx, args, is_cancelled):
        generation = ctx.generation

        def cancelled() -> bool:
            return (
                is_cancelled()
                or ctx.generation != generation
                or self.registry.find_context(ctx.session_id, ctx.session_key) is not ctx
            )

        async with ctx.lock:
            if cancelled():
                return False
            return await self.checkpoints.freeze_locked(ctx, args, is_cancelled=cancelled)

    async def resolve_clarify(self, ctx, status, answer=""):
        async with ctx.lock:
            pending = ctx.decision.pending_freeze
            was_pending = ctx.decision.active_checkpoint is None and pending is not None
            pending_ids = {str(pending.message_id)} if pending and pending.message_id else set()
            result = await self.checkpoints.resolve_locked(ctx, status, answer)
            if was_pending and ctx.decision.pending_freeze is None:
                self.lifecycle.cleanup_pending_locked(ctx, pending_ids)
            return result

    async def handle_event(self, event: ProgressEvent, force: bool = False) -> None:
        ctx = self.find_context(event.session_id, event.session_key)
        if ctx is None:
            return
        async with ctx.lock:
            if isinstance(event, DecisionEvent):
                self.checkpoints.observe_record(ctx, event.record)
                return
            if ctx.delivery.disabled or ctx.routing.strategy == "off":
                return
            if ctx.delivery.progress_state != "active" and not (
                isinstance(event, BackgroundJobEvent)
                and ctx.delivery.progress_state == "background_active"
            ):
                return
            if isinstance(event, ToolEvent) and not self.reducer.accepts(ctx, event):
                return
            if not isinstance(event, AssistantEvent):
                self._clear_transient_assistant(ctx)
            if not self.reducer.accepts(ctx, event):
                return
            ctx.diagnostics.last_event_at = time.monotonic()
            ctx.diagnostics.total_events += 1
            ctx.diagnostics.new_events_since_snapshot += 1
            line = self._format_tool_line(ctx, event) if isinstance(event, ToolEvent) else ""
            result = self.reducer.reduce(ctx, event, tool_line=line)
            self.checkpoints.observe_progress(ctx, event)
            force = force or result.force
            for job in result.background_poll_cancellations:
                self._cancel_background_poll(job)
            if result.delegate_cleanup is not None:
                self._schedule_delegate_cleanup(ctx, *result.delegate_cleanup)
            if self.checkpoints.is_waiting(ctx):
                return
            if result.skip_render:
                await self._render_for_strategy(ctx, event, force=force)
                return
            if isinstance(event, AssistantEvent):
                if (
                    not force
                    and ctx.delivery.message_id
                    and (time.monotonic() - ctx.delivery.last_render_at < ctx.routing.edit_interval)
                ):
                    return
                if not force and result.pending_chars < self.settings.assistant.min_update_chars:
                    return
            elif isinstance(event, ReasoningEvent):
                if (
                    not force
                    and ctx.delivery.message_id
                    and (time.monotonic() - ctx.delivery.last_render_at < ctx.routing.edit_interval)
                ):
                    return
                if not force and result.pending_chars < self.settings.reasoning.min_update_chars:
                    return
            await self._render_for_strategy(ctx, event, force=force)

    async def _render_for_strategy(
        self, ctx: SessionContext, event: ProgressEvent, force=False
    ) -> None:
        if ctx.routing.strategy == "live_tail":
            await self._render_live(ctx, force=force)
        elif ctx.routing.strategy == "snapshot" and not (
            isinstance(event, ReasoningEvent) and self.settings.reasoning.no_edit_strategy == "off"
        ):
            await self._render_snapshot(ctx, force=force)

    async def finalize(
        self, session_id="", session_key="", purge=False, *, success=True, generation=None
    ) -> None:
        await self.lifecycle.finalize(
            session_id, session_key, purge, success=success, generation=generation
        )

    def _clear_transient_assistant(self, ctx):
        self.reducer.clear_transient_assistant(ctx)

    def _append_assistant(self, ctx, event):
        return self.reducer.append_assistant(ctx, event)

    def _append_reasoning(self, ctx, event):
        return self.reducer.append_reasoning(ctx, event)

    def _apply_background_job_event(self, ctx, event):
        result = self.reducer.reduce(ctx, event)
        for job in result.background_poll_cancellations:
            self._cancel_background_poll(job)

    def _apply_delegate_event(self, ctx, event):
        result = self.reducer.reduce(ctx, event)
        if result.delegate_cleanup is not None:
            self._schedule_delegate_cleanup(ctx, *result.delegate_cleanup)

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

    _timestamp = staticmethod(timestamp_text)

    def _schedule_delegate_cleanup(
        self, ctx: SessionContext, subagent_id: str, branch: Any
    ) -> None:
        if (
            not subagent_id
            or ctx.loop is None
            or (branch.cleanup_task is not None and not branch.cleanup_task.done())
        ):
            return

        async def cleanup() -> None:
            try:
                await asyncio.sleep(self.settings.delegates.completed_ttl_seconds)
                async with ctx.lock:
                    if ctx.delegate.branches.get(subagent_id) is not branch:
                        return
                    self.delegate_renderer.prune_completed(ctx)
                    if decision_is_waiting(ctx.decision):
                        return
                    if ctx.routing.strategy == "live_tail":
                        await self._render_live(ctx, force=True)
                    elif ctx.routing.strategy == "snapshot":
                        await self._render_snapshot(ctx, force=True)
            finally:
                if branch.cleanup_task is task:
                    branch.cleanup_task = None

        task = ctx.loop.create_task(cleanup())
        branch.cleanup_task = task

    def _background_jobs_section(self, ctx: SessionContext) -> str:
        return background_jobs_section(
            ctx,
            settings=self.settings,
            section=self._section,
            background_jobs_enabled=self._background_jobs_enabled,
            cancel_poll=self._cancel_background_poll,
        )

    _cancel_background_poll = staticmethod(cancel_background_poll)
    _delegate_event_is_terminal = staticmethod(EventReducer.delegate_event_is_terminal)
    _trim_reasoning_buffer = staticmethod(EventReducer.trim_reasoning_buffer)
    _should_flush_before_reset = staticmethod(finalization_helpers.should_flush_before_reset)

    def _background_jobs_enabled(self, ctx: SessionContext) -> bool:
        return bool(
            self.settings.background_jobs.enabled
            and getattr(ctx.routing, "background_jobs_enabled", True)
        )

    _reset_turn = staticmethod(finalization_helpers.reset_turn)
    _has_background_jobs = staticmethod(finalization_helpers.has_background_jobs)

    def _assistant_tail(self, ctx: SessionContext) -> str:
        return assistant_tail(
            tuple(ctx.assistant.lines),
            max_lines=self.settings.assistant.max_lines,
            max_chars=self.settings.assistant.max_chars,
        )

    def _reasoning_tail(self, ctx: SessionContext) -> str:
        return render_reasoning_tail(
            ctx.reasoning.text,
            max_lines=self.settings.reasoning.max_lines,
            max_chars=self.settings.reasoning.max_chars,
            redact=self.settings.renderer.redact_secrets,
        )

    @staticmethod
    def _normalize_reasoning(text):
        return normalize_reasoning_text(text)
