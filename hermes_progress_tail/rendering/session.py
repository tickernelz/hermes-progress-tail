from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..gateway.compat import adapter_supports_edit
from ..models.state import SessionContext

CancelContext = Callable[[SessionContext], Any]


class SessionRegistry:
    """Owns session lookup, turn reuse, migration, and expiry."""

    def __init__(
        self,
        settings: Any,
        cancel_delete: CancelContext,
        cancel_delayed_flush: CancelContext,
    ) -> None:
        self.settings = settings
        self._cancel_delete = cancel_delete
        self._cancel_delayed_flush = cancel_delayed_flush
        self.sessions: dict[str, SessionContext] = {}
        self.session_keys: dict[str, str] = {}

    def replace_settings(self, settings: Any) -> None:
        self.settings = settings

    def register_context(self, ctx: SessionContext) -> None:
        existing = self.sessions.get(ctx.session_id)
        if existing is not None:
            reuse_progress = (
                existing.delivery.progress_state == "active"
                and self.same_source_message(existing, ctx)
            )
            if reuse_progress:
                self._cancel_delete(existing)
                self._reuse_progress(existing, ctx)
            self._reuse_session(existing, ctx, reuse_progress)
        ctx.resize(ctx.lines)
        if ctx.strategy == "auto":
            ctx.strategy = "live_tail" if adapter_supports_edit(ctx.adapter) else "snapshot"
        if ctx.strategy == "live_tail" and not adapter_supports_edit(ctx.adapter):
            ctx.strategy = "snapshot"
        self.sessions[ctx.session_id] = ctx
        if ctx.session_key:
            self.session_keys[ctx.session_key] = ctx.session_id

    @staticmethod
    def _reuse_progress(existing: SessionContext, ctx: SessionContext) -> None:
        ctx.tool = existing.tool
        fields = (
            "message_id",
            "started_at",
            "delegate_branches",
            "delegate_order",
            "assistant_lines",
            "assistant_latest_text",
            "assistant_pending_chars",
            "last_assistant_chars",
            "last_assistant_at",
            "assistant_transient",
            "reasoning_text",
            "reasoning_pending_chars",
            "last_reasoning_source",
            "last_reasoning_chars",
            "last_reasoning_at",
            "compaction_count",
        )
        for name in fields:
            setattr(ctx, name, getattr(existing, name))

    def _reuse_session(
        self, existing: SessionContext, ctx: SessionContext, reuse_progress: bool
    ) -> None:
        ctx.background = existing.background
        if (
            existing.delivery.delayed_flush_task is not None
            and not existing.delivery.delayed_flush_task.done()
        ):
            self._cancel_delayed_flush(existing)
        if reuse_progress:
            ctx.delivery = existing.delivery
            ctx.diagnostics = existing.diagnostics
        else:
            ctx.diagnostics.last_error = existing.diagnostics.last_error
            ctx.diagnostics.downgrade_reason = existing.diagnostics.downgrade_reason
            ctx.diagnostics.downgrade_at = existing.diagnostics.downgrade_at
        ctx.delivery.progress_state = "active"
        ctx.delivery.finalized_at = 0.0
        ctx.generation = existing.generation + 1
        ctx.lock = existing.lock

    @staticmethod
    def same_source_message(existing: SessionContext, incoming: SessionContext) -> bool:
        existing_source = str(existing.source_message_id or "")
        incoming_source = str(incoming.source_message_id or "")
        return not existing_source or not incoming_source or existing_source == incoming_source

    def find_context(self, session_id: str = "", session_key: str = "") -> SessionContext | None:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        if session_key and session_key in self.session_keys:
            return self.sessions.get(self.session_keys[session_key])
        return None

    def migrate_context(
        self, old_session_id: str, new_session_id: str, session_key: str = ""
    ) -> bool:
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
        ctx.delivery.progress_state = "active"
        ctx.delivery.finalized_at = 0.0
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
        now = time.monotonic()
        stale = [
            sid
            for sid, ctx in self.sessions.items()
            if (not platform or ctx.platform == platform)
            and now - ctx.diagnostics.last_event_at
            > ctx.lines * ctx.edit_interval + self.settings.renderer.stale_ttl_seconds
        ]
        for sid in stale:
            self.purge(sid)


# Compatibility helpers retained for callers that imported the old functions.
def _compat_registry(owner: Any) -> SessionRegistry:
    registry = getattr(owner, "registry", None)
    if registry is not None:
        return registry

    def noop_cancel(_ctx: SessionContext) -> None:
        return None

    registry = SessionRegistry(
        getattr(owner, "settings", None),
        getattr(owner, "_cancel_delete", noop_cancel),
        getattr(owner, "_cancel_delayed_flush", noop_cancel),
    )
    registry.sessions = owner.sessions
    registry.session_keys = owner.session_keys
    return registry


def register_context(owner: Any, ctx: SessionContext) -> None:
    _compat_registry(owner).register_context(ctx)


def _same_source_message(existing: SessionContext, incoming: SessionContext) -> bool:
    return SessionRegistry.same_source_message(existing, incoming)


def find_context(owner: Any, session_id: str = "", session_key: str = "") -> SessionContext | None:
    return _compat_registry(owner).find_context(session_id, session_key)


def migrate_context(
    owner: Any, old_session_id: str, new_session_id: str, session_key: str = ""
) -> bool:
    return _compat_registry(owner).migrate_context(old_session_id, new_session_id, session_key)


def purge(owner: Any, session_id: str = "", platform: str = "") -> None:
    _compat_registry(owner).purge(session_id, platform)
