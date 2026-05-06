from __future__ import annotations

import asyncio
import time

from ..models.state import SessionContext


def has_preserved_background_jobs(ctx: SessionContext, preserve_background_jobs: bool) -> bool:
    return bool(preserve_background_jobs and ctx.background_jobs)


def reset_turn(ctx: SessionContext) -> None:
    ctx.tool_lines.clear()
    ctx.active_tool_lines.clear()
    ctx.active_tool_fingerprints.clear()
    ctx.delegate_branches.clear()
    ctx.delegate_order.clear()
    ctx.todo_items = ()
    ctx.todo_updated_at = 0.0
    ctx.reasoning_text = ""
    ctx.reasoning_pending_chars = 0
    ctx.last_reasoning_source = ""
    ctx.last_reasoning_chars = 0
    ctx.last_reasoning_at = 0.0
    ctx.generation += 1
    ctx.progress_state = "finalizing"
    ctx.finalized_at = time.time()
    ctx.delete_failed_reason = ""
    ctx.message_id = None
    ctx.can_edit = True
    ctx.edit_state = "editable"
    ctx.edit_backoff_until = 0.0
    ctx.edit_failure_count = 0
    ctx.edit_recovery_sends = 0
    ctx.fallback_send_count = 0
    ctx.new_events_since_snapshot = 0
    ctx.snapshots_sent = 0
    ctx.total_events = 0


def resolve_finalization_policy(policy: str, platform: str) -> str:
    if policy != "auto":
        return policy
    if platform == "telegram":
        return "delete"
    return "keep"


def should_flush_before_reset(
    ctx: SessionContext,
    *,
    policy: str,
    delete_on_success: bool,
    delete_on_failure: bool,
    preserve_background_jobs: bool,
    success: bool,
) -> bool:
    if has_preserved_background_jobs(ctx, preserve_background_jobs):
        return True
    if policy == "delete":
        return not (delete_on_success if success else delete_on_failure)
    return policy == "keep"


async def finalize_progress_message(renderer, ctx: SessionContext, *, success: bool) -> None:
    message_id = ctx.message_id
    ctx.progress_state = "finalized"
    ctx.finalized_at = time.time()
    if not message_id:
        return
    policy = renderer._resolve_finalization_policy(ctx)
    if policy == "keep":
        return
    should_delete = (
        renderer.settings.finalization.delete_on_success
        if success
        else renderer.settings.finalization.delete_on_failure
    )
    if policy == "collapse":
        await renderer._collapse_progress_message(ctx, message_id)
        if not should_delete:
            ctx.message_id = None
            return
    if policy == "delete" and not should_delete:
        ctx.message_id = None
        return
    if renderer.settings.finalization.delay_seconds > 0:
        await asyncio.sleep(renderer.settings.finalization.delay_seconds)
    await delete_progress_message(
        ctx,
        message_id,
        current_context=lambda: renderer.find_context(ctx.session_id, ctx.session_key),
    )


async def collapse_progress_message(renderer, ctx: SessionContext, message_id: str) -> None:
    text = renderer.settings.finalization.collapse_text.strip() or "Done"
    content = renderer._prepare_message(ctx, text)
    try:
        result = await ctx.adapter.edit_message(
            chat_id=ctx.chat_id,
            message_id=message_id,
            content=content,
        )
    except Exception as exc:
        ctx.last_error = str(exc)
        ctx.delete_failed_reason = str(exc)
        return
    if not getattr(result, "success", False):
        ctx.delete_failed_reason = str(
            getattr(result, "error", "collapse failed") or "collapse failed"
        )


async def delete_progress_message(
    ctx: SessionContext, message_id: str, *, current_context=None
) -> None:
    delete = getattr(ctx.adapter, "delete_message", None)
    ctx.cleanup_attempts += 1
    ctx.message_id = None
    target = current_context() if current_context is not None else ctx
    if target is None:
        target = ctx
    if delete is None:
        _record_delete_failure(ctx, target, message_id, "delete_message unsupported")
        return
    try:
        result = await delete(chat_id=ctx.chat_id, message_id=message_id)
    except Exception as exc:
        ctx.last_error = str(exc)
        _record_delete_failure(ctx, target, message_id, f"delete failed: {exc}")
        return
    target = current_context() if current_context is not None else target
    if target is None:
        target = ctx
    if result is False or getattr(result, "success", True) is False:
        error = (
            getattr(result, "error", "delete failed") if result is not False else "delete failed"
        )
        _record_delete_failure(ctx, target, message_id, str(error or "delete failed"))
        return
    _record_delete_success(ctx, target, message_id)


def _record_delete_failure(
    original: SessionContext, target: SessionContext, message_id: str, reason: str
) -> None:
    for ctx in _unique_contexts(original, target):
        was_active_replacement = ctx is not original and ctx.progress_state == "active"
        if ctx.stale_message_id in {"", message_id}:
            ctx.stale_message_id = message_id
        if not was_active_replacement or ctx.message_id == message_id:
            ctx.message_id = None
        if not was_active_replacement:
            ctx.progress_state = "finalized"
        ctx.delete_failed_reason = reason


def _record_delete_success(
    original: SessionContext, target: SessionContext, message_id: str
) -> None:
    for ctx in _unique_contexts(original, target):
        was_active_replacement = ctx is not original and ctx.progress_state == "active"
        if ctx.stale_message_id == message_id:
            ctx.stale_message_id = ""
        if not was_active_replacement or ctx.message_id == message_id:
            ctx.message_id = None
        if not was_active_replacement:
            ctx.progress_state = "deleted"
        ctx.delete_failed_reason = ""


def _unique_contexts(*contexts: SessionContext) -> tuple[SessionContext, ...]:
    seen = set()
    result = []
    for ctx in contexts:
        marker = id(ctx)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(ctx)
    return tuple(result)
