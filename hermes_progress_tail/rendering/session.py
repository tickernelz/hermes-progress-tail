from __future__ import annotations

import time

from ..gateway.compat import adapter_supports_edit
from ..models.state import SessionContext


def register_context(self, ctx: SessionContext) -> None:
    existing = self.sessions.get(ctx.session_id)
    if existing is not None:
        reuse_progress = existing.progress_state == "active" and self._same_source_message(
            existing, ctx
        )
        if reuse_progress:
            self._cancel_delete(existing)
            ctx.message_id = existing.message_id
            ctx.tool_lines = existing.tool_lines
            ctx.started_at = existing.started_at
            ctx.active_tool_lines = existing.active_tool_lines
            ctx.active_tool_fingerprints = existing.active_tool_fingerprints
            ctx.tool_started_count = existing.tool_started_count
            ctx.tool_completed_count = existing.tool_completed_count
            ctx.tool_failed_count = existing.tool_failed_count
            ctx.completed_tool_ids = existing.completed_tool_ids
            ctx.delegate_branches = existing.delegate_branches
            ctx.delegate_order = existing.delegate_order
            ctx.todo_items = existing.todo_items
            ctx.todo_updated_at = existing.todo_updated_at
            ctx.assistant_lines = existing.assistant_lines
            ctx.assistant_latest_text = existing.assistant_latest_text
            ctx.assistant_pending_chars = existing.assistant_pending_chars
            ctx.last_assistant_chars = existing.last_assistant_chars
            ctx.last_assistant_at = existing.last_assistant_at
            ctx.assistant_transient = existing.assistant_transient
            ctx.reasoning_text = existing.reasoning_text
            ctx.reasoning_pending_chars = existing.reasoning_pending_chars
            ctx.last_reasoning_source = existing.last_reasoning_source
            ctx.last_reasoning_chars = existing.last_reasoning_chars
            ctx.last_reasoning_at = existing.last_reasoning_at
        ctx.background_jobs = existing.background_jobs
        ctx.background_order = existing.background_order
        ctx.progress_state = "active"
        ctx.finalized_at = 0.0
        ctx.generation = existing.generation + 1
        ctx.total_events = existing.total_events if reuse_progress else 0
        ctx.snapshots_sent = existing.snapshots_sent if reuse_progress else 0
        ctx.last_error = existing.last_error
        ctx.downgrade_reason = existing.downgrade_reason
        ctx.downgrade_at = existing.downgrade_at
        ctx.last_render_at = existing.last_render_at if reuse_progress else 0.0
        ctx.edit_state = existing.edit_state if reuse_progress else "editable"
        ctx.edit_backoff_until = existing.edit_backoff_until if reuse_progress else 0.0
        ctx.edit_failure_count = existing.edit_failure_count if reuse_progress else 0
        ctx.edit_recovery_sends = existing.edit_recovery_sends if reuse_progress else 0
        if existing.delayed_flush_task is not None and not existing.delayed_flush_task.done():
            self._cancel_delayed_flush(existing)
            ctx.edit_backoff_until = 0.0
        ctx.fallback_send_count = existing.fallback_send_count if reuse_progress else 0
        ctx.new_events_since_snapshot = existing.new_events_since_snapshot if reuse_progress else 0
        ctx.lock = existing.lock
    ctx.resize(ctx.lines)
    if ctx.strategy == "auto":
        ctx.strategy = "live_tail" if adapter_supports_edit(ctx.adapter) else "snapshot"
    if ctx.strategy == "live_tail" and not adapter_supports_edit(ctx.adapter):
        ctx.strategy = "snapshot"
    self.sessions[ctx.session_id] = ctx
    if ctx.session_key:
        self.session_keys[ctx.session_key] = ctx.session_id


def _same_source_message(existing: SessionContext, incoming: SessionContext) -> bool:
    existing_source = str(existing.source_message_id or "")
    incoming_source = str(incoming.source_message_id or "")
    return not existing_source or not incoming_source or existing_source == incoming_source


def find_context(self, session_id: str = "", session_key: str = "") -> SessionContext | None:
    if session_id and session_id in self.sessions:
        return self.sessions[session_id]
    if session_key and session_key in self.session_keys:
        return self.sessions.get(self.session_keys[session_key])
    return None


def migrate_context(self, old_session_id: str, new_session_id: str, session_key: str = "") -> bool:
    old_session_id = str(old_session_id or "")
    new_session_id = str(new_session_id or "")
    session_key = str(session_key or "")
    if not old_session_id or not new_session_id or old_session_id == new_session_id:
        return False
    ctx = self.sessions.pop(old_session_id, None)
    if ctx is None:
        ctx = self.find_context("", session_key)
        if ctx is None:
            return False
        self.sessions.pop(ctx.session_id, None)
    if ctx.session_key:
        self.session_keys.pop(ctx.session_key, None)
    ctx.session_id = new_session_id
    if session_key:
        ctx.session_key = session_key
    self._cancel_delete(ctx)
    ctx.progress_state = "active"
    ctx.finalized_at = 0.0
    self.sessions[new_session_id] = ctx
    if ctx.session_key:
        self.session_keys[ctx.session_key] = new_session_id
    return True


def purge(self, session_id: str = "", platform: str = "") -> None:
    if session_id:
        ctx = self.sessions.pop(session_id, None)
        if ctx:
            self._cancel_delayed_flush(ctx)
        if ctx and ctx.session_key:
            self.session_keys.pop(ctx.session_key, None)
        return
    stale = []
    now = time.monotonic()
    for sid, ctx in self.sessions.items():
        if platform and ctx.platform != platform:
            continue
        if (
            now - ctx.last_event_at
            > ctx.lines * ctx.edit_interval + self.settings.renderer.stale_ttl_seconds
        ):
            stale.append(sid)
    for sid in stale:
        self.purge(sid)
