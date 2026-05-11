from __future__ import annotations

import time

from ..models.state import SessionContext


def has_background_jobs(ctx: SessionContext) -> bool:
    return bool(ctx.background_jobs)


def reset_turn(ctx: SessionContext) -> None:
    ctx.tool_lines.clear()
    ctx.active_tool_lines.clear()
    ctx.active_tool_fingerprints.clear()
    ctx.delegate_branches.clear()
    ctx.delegate_order.clear()
    ctx.todo_items = ()
    ctx.todo_updated_at = 0.0
    ctx.assistant_lines.clear()
    ctx.assistant_latest_text = ""
    ctx.assistant_pending_chars = 0
    ctx.last_assistant_chars = 0
    ctx.last_assistant_at = 0.0
    ctx.reasoning_text = ""
    ctx.reasoning_pending_chars = 0
    ctx.last_reasoning_source = ""
    ctx.last_reasoning_chars = 0
    ctx.last_reasoning_at = 0.0
    ctx.generation += 1
    ctx.finalized_at = time.time()
    ctx.can_edit = True
    ctx.edit_state = "editable"
    ctx.edit_backoff_until = 0.0
    ctx.edit_failure_count = 0
    ctx.edit_recovery_sends = 0
    ctx.fallback_send_count = 0
    ctx.new_events_since_snapshot = 0
    ctx.snapshots_sent = 0
    ctx.total_events = 0


def should_flush_before_reset(ctx: SessionContext) -> bool:
    return bool(ctx.message_id and ctx.progress_state == "active")


def finalize_progress_message(ctx: SessionContext) -> None:
    ctx.progress_state = "background_active" if has_background_jobs(ctx) else "finalized"
    ctx.finalized_at = time.time()
