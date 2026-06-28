from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from ..gateway.compat import delete_message
from ..models.state import SessionContext

logger = logging.getLogger(__name__)


async def _render_live(
    self, ctx: SessionContext, force: bool = False, *, ignore_backoff: bool = False
) -> None:
    now = time.monotonic()
    if not force and ctx.message_id and now - ctx.last_render_at < ctx.edit_interval:
        return
    if ctx.message_id and now < ctx.edit_backoff_until and not ignore_backoff:
        self._schedule_delayed_live_flush(ctx, ctx.edit_backoff_until - now)
        return
    content = self._content(ctx)
    if not content:
        return
    content = self._prepare_message(ctx, content)
    if ctx.message_id and ctx.can_edit:
        try:
            result = await ctx.adapter.edit_message(
                chat_id=ctx.chat_id,
                message_id=ctx.message_id,
                content=content,
            )
        except Exception as exc:
            logger.debug("hermes-progress-tail edit failed: %s", exc)
            ctx.last_error = str(exc)
            result = _Result(False, ctx.message_id, str(exc))
        if getattr(result, "success", False):
            ctx.assistant_pending_chars = 0
            ctx.reasoning_pending_chars = 0
            ctx.edit_state = "editable"
            ctx.edit_backoff_until = 0.0
            ctx.edit_failure_count = 0
            ctx.last_render_at = time.monotonic()
            return
        error = str(getattr(result, "error", "") or "edit failed")
        kind = self._classify_edit_error(error)
        if kind == "noop_success":
            ctx.assistant_pending_chars = 0
            ctx.reasoning_pending_chars = 0
            ctx.edit_state = "editable"
            ctx.last_render_at = time.monotonic()
            return
        ctx.last_error = error
        if ctx.edit_state == kind and kind in {
            "rate_limited",
            "transient",
            "unknown_transient",
        }:
            ctx.edit_failure_count += 1
        else:
            ctx.edit_failure_count = 1
        if kind == "unsupported":
            await self._downgrade_to_snapshot(ctx, error, "unsupported")
            return
        if kind == "message_lost":
            if ctx.edit_recovery_sends == 0:
                ctx.edit_state = "recovering"
                await self._send_live_message(ctx, content, recovery=True)
                return
            await self._downgrade_to_snapshot(ctx, error, "message_lost")
            return
        if kind == "too_long":
            await self._downgrade_to_snapshot(ctx, error, "too_long")
            return
        delay = self._edit_backoff_seconds(error, kind, ctx.edit_failure_count)
        ctx.edit_state = kind
        if ignore_backoff:
            ctx.edit_backoff_until = 0.0
            return
        ctx.edit_backoff_until = time.monotonic() + delay
        self._schedule_delayed_live_flush(ctx, delay)
        return
    await self._send_live_message(ctx, content)


async def _send_live_message(
    self, ctx: SessionContext, content: str, *, recovery: bool = False
) -> None:
    try:
        result = await ctx.adapter.send(ctx.chat_id, content, metadata=ctx.metadata)
    except Exception as exc:
        logger.debug("hermes-progress-tail send failed: %s", exc)
        result = _Result(False, None, str(exc))
    if getattr(result, "success", False):
        ctx.message_id = getattr(result, "message_id", None) or ctx.message_id
        ctx.can_edit = True
        ctx.edit_state = "editable"
        ctx.edit_backoff_until = 0.0
        ctx.edit_failure_count = 0
        if recovery:
            ctx.edit_recovery_sends += 1
            ctx.fallback_send_count += 1
        ctx.assistant_pending_chars = 0
        ctx.reasoning_pending_chars = 0
        ctx.last_render_at = time.monotonic()
    else:
        error = str(getattr(result, "error", "send failed") or "send failed")
        ctx.last_error = error
        kind = self._classify_edit_error(error)
        if kind in {"rate_limited", "transient", "unknown_transient"}:
            if ctx.edit_state == kind:
                ctx.edit_failure_count += 1
            else:
                ctx.edit_failure_count = 1
            delay = self._edit_backoff_seconds(error, kind, ctx.edit_failure_count)
            ctx.edit_state = kind
            ctx.edit_backoff_until = time.monotonic() + delay
            self._schedule_delayed_live_flush(ctx, delay)
            return
        ctx.disabled = True


async def _downgrade_to_snapshot(self, ctx: SessionContext, error: str, state: str) -> None:
    ctx.strategy = "snapshot"
    ctx.can_edit = False
    ctx.edit_state = state
    ctx.downgrade_reason = error
    ctx.downgrade_at = time.time()
    if ctx.fallback_send_count == 0:
        await self._render_snapshot(ctx, force=True)


def _schedule_delayed_live_flush(self, ctx: SessionContext, delay: float) -> None:
    if ctx.loop is None:
        return
    current = ctx.delayed_flush_task
    if current is not None and not current.done():
        return
    generation = ctx.generation

    async def _flush_later() -> None:
        try:
            await asyncio.sleep(max(0.05, delay))
            if ctx.generation != generation:
                return
            async with ctx.lock:
                if ctx.disabled or ctx.strategy != "live_tail" or not self._content(ctx):
                    return
                await self._render_live(ctx, force=True)
        except asyncio.CancelledError:
            raise
        finally:
            if ctx.delayed_flush_task is task:
                ctx.delayed_flush_task = None

    task = ctx.loop.create_task(_flush_later())
    ctx.delayed_flush_task = task


def _cancel_delayed_flush(ctx: SessionContext) -> None:
    task = ctx.delayed_flush_task
    if task is not None and not task.done():
        task.cancel()
    ctx.delayed_flush_task = None


def _cancel_delete(ctx: SessionContext) -> None:
    task = ctx.delete_task
    if task is not None and not task.done():
        task.cancel()
    ctx.delete_task = None


def _schedule_auto_delete(self, ctx: SessionContext, *, success: bool) -> None:
    cleanup = self.settings.cleanup
    if not cleanup.auto_delete or not ctx.message_id or ctx.loop is None:
        return
    if success and not cleanup.delete_on_success:
        return
    if not success and not cleanup.delete_on_failure:
        return
    if ctx.progress_state == "background_active" and not cleanup.delete_background_active:
        return
    self._cancel_delete(ctx)
    generation = ctx.generation
    message_id = str(ctx.message_id)
    delay = max(0, cleanup.delay_seconds)

    async def _delete_later() -> None:
        try:
            await asyncio.sleep(delay)
            if ctx.generation != generation or ctx.message_id != message_id:
                return
            try:
                deleted = await delete_message(ctx.adapter, ctx.chat_id, message_id)
            except Exception as exc:
                logger.debug("hermes-progress-tail delete failed: %s", exc)
                ctx.last_error = str(exc)
                return
            if deleted:
                ctx.message_id = None
                ctx.can_edit = False
                ctx.progress_state = "deleted"
        except asyncio.CancelledError:
            raise
        finally:
            if ctx.delete_task is task:
                ctx.delete_task = None

    task = ctx.loop.create_task(_delete_later())
    ctx.delete_task = task


async def _render_snapshot(
    self, ctx: SessionContext, force: bool = False, final: bool = False
) -> None:
    content_body = self._content(ctx)
    if not content_body:
        return
    now = time.monotonic()
    enough_events = ctx.new_events_since_snapshot >= self.settings.no_edit.min_new_events
    enough_time = now - ctx.last_render_at >= self.settings.no_edit.interval_seconds
    under_cap = ctx.snapshots_sent < self.settings.no_edit.max_snapshots_per_turn
    if not force and not (enough_events and enough_time and under_cap):
        return
    if not final and not under_cap:
        return
    if final:
        title = "Progress tail — final"
    elif ctx.tool_lines:
        title = f"Progress tail — latest {len(ctx.tool_lines)} tools"
    else:
        title = "Progress tail — latest updates"
    if ctx.total_events:
        title += f" of {ctx.total_events} events"
    content = self._prepare_message(ctx, title + "\n" + content_body)
    try:
        result = await ctx.adapter.send(ctx.chat_id, content, metadata=ctx.metadata)
    except Exception as exc:
        logger.debug("hermes-progress-tail snapshot send failed: %s", exc)
        ctx.last_error = str(exc)
        ctx.disabled = True
        return
    if getattr(result, "success", False):
        ctx.snapshots_sent += 1
        ctx.fallback_send_count += 1
        ctx.new_events_since_snapshot = 0
        ctx.assistant_pending_chars = 0
        ctx.reasoning_pending_chars = 0
        ctx.last_render_at = time.monotonic()
    else:
        ctx.last_error = str(
            getattr(result, "error", "snapshot send failed") or "snapshot send failed"
        )
        ctx.disabled = True


def _prepare_message(self, ctx: SessionContext, content: str) -> str:
    return self._fit_message(content, self._message_limit(ctx))


def _prepare_telegram_rich_message(self, ctx: SessionContext, content: str) -> str:
    # Telegram rich preparation is owned by the Telegram send/edit monkeypatch.
    # Pre-transforming here is unsafe because legacy send fallbacks would receive
    # rich-markdown tables/details as plain MarkdownV2 text.
    return content


def _fit_message(content: str, limit: int) -> str:
    if limit <= 0 or len(content) <= limit:
        return content
    marker = "\n…\n"
    budget = max(0, limit - len(marker))
    if budget <= 0:
        return content[:limit]
    head_budget = min(180, max(0, budget // 4))
    tail_budget = max(0, budget - head_budget)
    return content[:head_budget].rstrip() + marker + content[-tail_budget:].lstrip()


def _message_limit(ctx: SessionContext) -> int:
    if ctx.platform == "telegram":
        return 4096
    return 0


def _classify_edit_error(error: Any) -> str:
    msg = str(error or "").lower()
    if "not modified" in msg:
        return "noop_success"
    if "too long" in msg or "message_too_long" in msg:
        return "too_long"
    if any(
        part in msg
        for part in (
            "unsupported",
            "not supported",
            "not implemented",
            "notimplementederror",
            "edit not supported",
            "cannot edit",
            "can't edit",
            "can't be edited",
            "method not found",
            "edit not available",
        )
    ):
        return "unsupported"
    if any(part in msg for part in ("forbidden", "blocked by the user", "unauthorized")):
        return "permanent"
    if (
        "message to edit not found" in msg
        or "message_id_invalid" in msg
        or "unknown message" in msg
        or "message not found" in msg
        or "message_id" in msg
        and "not found" in msg
    ):
        return "message_lost"
    if any(
        part in msg for part in ("flood", "retry after", "too many requests", "rate limit", "429")
    ):
        return "rate_limited"
    if any(
        part in msg
        for part in (
            "timeout",
            "timed out",
            "network",
            "connection",
            "temporarily",
            "temporary",
            "reset by peer",
            "server disconnected",
            "bad gateway",
            "gateway timeout",
            "502",
            "503",
            "504",
        )
    ):
        return "transient"
    return "unknown_transient"


def _edit_backoff_seconds(error: Any, kind: str, failure_count: int) -> float:
    msg = str(error or "").lower()
    match = re.search(
        r"(?:retry after|flood_control:|retry_after=|retry in)\s*:?\s*(\d+(?:\.\d+)?)",
        msg,
    )
    if match:
        return min(float(match.group(1)), 600.0)
    if kind == "rate_limited":
        return min(2.0 * max(1, failure_count), 30.0)
    if kind == "too_long":
        return 1.0
    return min(1.0 * max(1, failure_count), 10.0)


class _Result:
    def __init__(self, success: bool, message_id: str | None = None, error: str = ""):
        self.success = success
        self.message_id = message_id
        self.error = error
