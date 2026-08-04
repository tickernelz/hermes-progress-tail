from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..models.decision import decision_is_waiting
from ..models.state import SessionContext


def retiring_message_ids(ctx: SessionContext) -> set[str]:
    return {
        str(message_id)
        for message_id in (
            *ctx.decision.protected_message_ids,
            getattr(ctx.decision.pending_freeze, "message_id", ""),
            getattr(ctx.decision.active_checkpoint, "message_id", ""),
        )
        if message_id
    }


def retire_checkpoint_state(
    ctx: SessionContext, delivery: Any, extra_message_ids: set[str] | None = None
) -> None:
    """Fence retiring checkpoint IDs before their state can be forgotten."""
    ids = retiring_message_ids(ctx) | set(extra_message_ids or ())
    ctx.decision.cleanup_epoch += 1
    delivery.cancel_delayed_flush(ctx)
    delivery.cancel_delete(ctx)
    if str(ctx.delivery.message_id or "") in ids:
        ctx.delivery.message_id = None
        ctx.delivery.message_started_at = 0.0
    ctx.delivery.progress_message_ids[:] = [
        message_id for message_id in ctx.delivery.progress_message_ids if str(message_id) not in ids
    ]


class RendererLifecycle:
    """Own renderer finalization without retaining the composition root."""

    def __init__(
        self,
        settings: Any,
        registry: Any,
        delivery: Any,
        checkpoints: Any,
        *,
        content: Callable[[SessionContext], str],
        reset_turn: Callable[[SessionContext], None],
        finalize_progress_message: Callable[[SessionContext], None],
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.delivery = delivery
        self.checkpoints = checkpoints
        self._content = content
        self._reset_turn = reset_turn
        self._finalize_progress_message = finalize_progress_message

    def replace_settings(self, settings: Any) -> None:
        self.settings = settings

    def cleanup_pending_locked(self, ctx: SessionContext, pending_ids: set[str]) -> None:
        """Clear an invalidated reservation without touching an adapter."""
        retire_checkpoint_state(ctx, self.delivery, pending_ids)
        ctx.decision.pending_freeze = None
        ctx.decision.reconcile_protected_message_ids()

    async def finalize(
        self,
        session_id: str = "",
        session_key: str = "",
        purge: bool = False,
        *,
        success: bool = True,
        generation: int | None = None,
    ) -> None:
        ctx = self.registry.find_context(session_id, session_key)
        if ctx is None or (generation is not None and ctx.generation != generation):
            return
        async with ctx.lock:
            if generation is not None and (
                self.registry.sessions.get(ctx.session_id) is not ctx
                or ctx.generation != generation
            ):
                return
            waiting = decision_is_waiting(ctx.decision)
            if ctx.delivery.disabled and not waiting:
                self.delivery.cancel_delayed_flush(ctx)
                return
            retiring_ids = retiring_message_ids(ctx) if waiting else set()
            self.delivery.cancel_delayed_flush(ctx)
            if waiting:
                await self.checkpoints.interrupt_locked(ctx)
                retire_checkpoint_state(ctx, self.delivery, retiring_ids)
                self._reset_turn(ctx)
                self._finalize_progress_message(ctx)
            else:
                progress_message_id = ctx.delivery.message_id
                if ctx.delivery.message_id and ctx.delivery.progress_state == "active":
                    if ctx.routing.strategy == "live_tail" and self._content(ctx):
                        await self.delivery.render_live(ctx, force=True, ignore_backoff=True)
                        progress_message_id = ctx.delivery.message_id or progress_message_id
                    elif (
                        ctx.routing.strategy == "snapshot"
                        and self.settings.no_edit.final_summary
                        and self._content(ctx)
                    ):
                        await self.delivery.render_snapshot(ctx, force=True, final=True)
                self._reset_turn(ctx)
                ctx.delivery.message_id = progress_message_id
                self._finalize_progress_message(ctx)
                self.delivery.schedule_auto_delete(ctx, success=success)
                if ctx.delivery.progress_state == "background_active" and self._content(ctx):
                    if ctx.routing.strategy == "live_tail":
                        await self.delivery.render_live(ctx, force=True, ignore_backoff=True)
                    elif ctx.routing.strategy == "snapshot" and self.settings.no_edit.final_summary:
                        await self.delivery.render_snapshot(ctx, force=True, final=True)
        if purge and (generation is None or self.registry.sessions.get(ctx.session_id) is ctx):
            self.registry.purge(
                session_id=ctx.session_id,
                session_key=ctx.session_key,
                preserve_cleanup=True,
            )
