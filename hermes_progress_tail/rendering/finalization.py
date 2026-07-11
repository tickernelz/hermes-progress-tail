from __future__ import annotations

import time

from ..models.state import SessionContext


def has_background_jobs(ctx: SessionContext) -> bool:
    return bool(ctx.background.jobs)


def reset_turn(ctx: SessionContext) -> None:
    ctx.tool.lines.clear()
    ctx.tool.active_lines.clear()
    ctx.tool.active_fingerprints.clear()
    ctx.tool.started_count = 0
    ctx.tool.completed_count = 0
    ctx.tool.failed_count = 0
    ctx.tool.completed_ids.clear()
    ctx.started_at = time.monotonic()
    ctx.delegate.branches.clear()
    ctx.delegate.order.clear()
    ctx.tool.todo_items = ()
    ctx.tool.todo_updated_at = 0.0
    ctx.assistant.lines.clear()
    ctx.assistant.latest_text = ""
    ctx.assistant.pending_chars = 0
    ctx.assistant.last_chars = 0
    ctx.assistant.last_at = 0.0
    ctx.assistant.transient = False
    ctx.reasoning.text = ""
    ctx.reasoning.pending_chars = 0
    ctx.reasoning.last_source = ""
    ctx.reasoning.last_chars = 0
    ctx.reasoning.last_at = 0.0
    ctx.generation += 1
    ctx.delivery.finalized_at = time.time()
    ctx.delivery.can_edit = True
    ctx.delivery.edit_state = "editable"
    ctx.delivery.edit_backoff_until = 0.0
    ctx.delivery.edit_failure_count = 0
    ctx.delivery.edit_recovery_sends = 0
    ctx.delivery.fallback_send_count = 0
    ctx.diagnostics.new_events_since_snapshot = 0
    ctx.delivery.snapshots_sent = 0
    ctx.diagnostics.total_events = 0


def should_flush_before_reset(ctx: SessionContext) -> bool:
    return bool(ctx.delivery.message_id and ctx.delivery.progress_state == "active")


def finalize_progress_message(ctx: SessionContext) -> None:
    ctx.delivery.progress_state = "background_active" if has_background_jobs(ctx) else "finalized"
    ctx.delivery.finalized_at = time.time()
