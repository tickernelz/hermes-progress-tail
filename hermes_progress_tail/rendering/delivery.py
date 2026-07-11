from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from ..gateway.compat import delete_message
from ..models.state import SessionContext

logger = logging.getLogger(__name__)


class RendererDelivery:
    def __init__(self, settings: Any, content: Any):
        self.settings = settings
        self._content = content

    def replace_settings(self, settings: Any) -> None:
        self.settings = settings

    @staticmethod
    def fit_message(content: str, limit: int) -> str:
        return _fit_message(content, limit)

    @staticmethod
    def message_limit(ctx: SessionContext) -> int:
        return _message_limit(ctx)

    @staticmethod
    def classify_edit_error(error: Any) -> str:
        return _classify_edit_error(error)

    @staticmethod
    def edit_backoff_seconds(error: Any, kind: str, failure_count: int) -> float:
        return _edit_backoff_seconds(error, kind, failure_count)

    async def render_live(
        self, ctx: SessionContext, force: bool = False, *, ignore_backoff: bool = False
    ) -> None:
        now = time.monotonic()
        if (
            not force
            and ctx.delivery.message_id
            and now - ctx.delivery.last_render_at < ctx.edit_interval
        ):
            return
        if ctx.delivery.message_id and now < ctx.delivery.edit_backoff_until and not ignore_backoff:
            self.schedule_delayed_live_flush(ctx, ctx.delivery.edit_backoff_until - now)
            return
        content = self._content(ctx)
        if not content:
            return
        content = self.prepare_message(ctx, content)
        if ctx.delivery.message_id and ctx.delivery.can_edit:
            try:
                result = await ctx.adapter.edit_message(
                    chat_id=ctx.chat_id,
                    message_id=ctx.delivery.message_id,
                    content=content,
                )
            except Exception as exc:
                logger.debug("hermes-progress-tail edit failed: %s", exc)
                ctx.diagnostics.last_error = str(exc)
                result = _Result(False, ctx.delivery.message_id, str(exc))
            if getattr(result, "success", False):
                ctx.assistant.pending_chars = 0
                ctx.reasoning.pending_chars = 0
                ctx.delivery.edit_state = "editable"
                ctx.delivery.edit_backoff_until = 0.0
                ctx.delivery.edit_failure_count = 0
                ctx.delivery.last_render_at = time.monotonic()
                return
            error = str(getattr(result, "error", "") or "edit failed")
            kind = self.classify_edit_error(error)
            if kind == "noop_success":
                ctx.assistant.pending_chars = 0
                ctx.reasoning.pending_chars = 0
                ctx.delivery.edit_state = "editable"
                ctx.delivery.last_render_at = time.monotonic()
                return
            ctx.diagnostics.last_error = error
            if ctx.delivery.edit_state == kind and kind in {
                "rate_limited",
                "transient",
                "unknown_transient",
            }:
                ctx.delivery.edit_failure_count += 1
            else:
                ctx.delivery.edit_failure_count = 1
            if kind == "unsupported":
                await self.downgrade_to_snapshot(ctx, error, "unsupported")
                return
            if kind == "message_lost":
                if ctx.delivery.edit_recovery_sends == 0:
                    ctx.delivery.edit_state = "recovering"
                    await self.send_live_message(ctx, content, recovery=True)
                    return
                await self.downgrade_to_snapshot(ctx, error, "message_lost")
                return
            if kind == "too_long":
                await self.downgrade_to_snapshot(ctx, error, "too_long")
                return
            delay = self.edit_backoff_seconds(error, kind, ctx.delivery.edit_failure_count)
            ctx.delivery.edit_state = kind
            if ignore_backoff:
                ctx.delivery.edit_backoff_until = 0.0
                return
            ctx.delivery.edit_backoff_until = time.monotonic() + delay
            self.schedule_delayed_live_flush(ctx, delay)
            return
        await self.send_live_message(ctx, content)

    async def send_live_message(
        self, ctx: SessionContext, content: str, *, recovery: bool = False
    ) -> None:
        try:
            result = await ctx.adapter.send(ctx.chat_id, content, metadata=ctx.metadata)
        except Exception as exc:
            logger.debug("hermes-progress-tail send failed: %s", exc)
            result = _Result(False, None, str(exc))
        if getattr(result, "success", False):
            ctx.delivery.message_id = getattr(result, "message_id", None) or ctx.delivery.message_id
            ctx.delivery.can_edit = True
            ctx.delivery.edit_state = "editable"
            ctx.delivery.edit_backoff_until = 0.0
            ctx.delivery.edit_failure_count = 0
            if recovery:
                ctx.delivery.edit_recovery_sends += 1
                ctx.delivery.fallback_send_count += 1
            ctx.assistant.pending_chars = 0
            ctx.reasoning.pending_chars = 0
            ctx.delivery.last_render_at = time.monotonic()
        else:
            error = str(getattr(result, "error", "send failed") or "send failed")
            ctx.diagnostics.last_error = error
            kind = self.classify_edit_error(error)
            if kind in {"rate_limited", "transient", "unknown_transient"}:
                if ctx.delivery.edit_state == kind:
                    ctx.delivery.edit_failure_count += 1
                else:
                    ctx.delivery.edit_failure_count = 1
                delay = self.edit_backoff_seconds(error, kind, ctx.delivery.edit_failure_count)
                ctx.delivery.edit_state = kind
                ctx.delivery.edit_backoff_until = time.monotonic() + delay
                self.schedule_delayed_live_flush(ctx, delay)
                return
            ctx.delivery.disabled = True

    async def downgrade_to_snapshot(self, ctx: SessionContext, error: str, state: str) -> None:
        ctx.strategy = "snapshot"
        ctx.delivery.can_edit = False
        ctx.delivery.edit_state = state
        ctx.diagnostics.downgrade_reason = error
        ctx.diagnostics.downgrade_at = time.time()
        if ctx.delivery.fallback_send_count == 0:
            await self.render_snapshot(ctx, force=True)

    def schedule_delayed_live_flush(self, ctx: SessionContext, delay: float) -> None:
        if ctx.loop is None:
            return
        current = ctx.delivery.delayed_flush_task
        if current is not None and not current.done():
            return
        generation = ctx.generation

        async def _flush_later() -> None:
            try:
                await asyncio.sleep(max(0.05, delay))
                if ctx.generation != generation:
                    return
                async with ctx.lock:
                    if (
                        ctx.delivery.disabled
                        or ctx.strategy != "live_tail"
                        or not self._content(ctx)
                    ):
                        return
                    await self.render_live(ctx, force=True)
            except asyncio.CancelledError:
                raise
            finally:
                if ctx.delivery.delayed_flush_task is task:
                    ctx.delivery.delayed_flush_task = None

        task = ctx.loop.create_task(_flush_later())
        ctx.delivery.delayed_flush_task = task

    def cancel_delayed_flush(self, ctx: SessionContext) -> None:
        task = ctx.delivery.delayed_flush_task
        if task is not None and not task.done():
            task.cancel()
        ctx.delivery.delayed_flush_task = None

    def cancel_delete(self, ctx: SessionContext) -> None:
        task = ctx.delivery.delete_task
        if task is not None and not task.done():
            task.cancel()
        ctx.delivery.delete_task = None

    def schedule_auto_delete(self, ctx: SessionContext, *, success: bool) -> None:
        cleanup = self.settings.cleanup
        if not cleanup.auto_delete or not ctx.delivery.message_id or ctx.loop is None:
            return
        if success and not cleanup.delete_on_success:
            return
        if not success and not cleanup.delete_on_failure:
            return
        if (
            ctx.delivery.progress_state == "background_active"
            and not cleanup.delete_background_active
        ):
            return
        self.cancel_delete(ctx)
        generation = ctx.generation
        message_id = str(ctx.delivery.message_id)
        delay = max(0, cleanup.delay_seconds)

        async def _delete_later() -> None:
            try:
                await asyncio.sleep(delay)
                if ctx.generation != generation or ctx.delivery.message_id != message_id:
                    return
                try:
                    deleted = await delete_message(ctx.adapter, ctx.chat_id, message_id)
                except Exception as exc:
                    logger.debug("hermes-progress-tail delete failed: %s", exc)
                    ctx.diagnostics.last_error = str(exc)
                    return
                if deleted:
                    ctx.delivery.message_id = None
                    ctx.delivery.can_edit = False
                    ctx.delivery.progress_state = "deleted"
            except asyncio.CancelledError:
                raise
            finally:
                if ctx.delivery.delete_task is task:
                    ctx.delivery.delete_task = None

        task = ctx.loop.create_task(_delete_later())
        ctx.delivery.delete_task = task

    async def render_snapshot(
        self, ctx: SessionContext, force: bool = False, final: bool = False
    ) -> None:
        content_body = self._content(ctx)
        if not content_body:
            return
        now = time.monotonic()
        enough_events = (
            ctx.diagnostics.new_events_since_snapshot >= self.settings.no_edit.min_new_events
        )
        enough_time = now - ctx.delivery.last_render_at >= self.settings.no_edit.interval_seconds
        under_cap = ctx.delivery.snapshots_sent < self.settings.no_edit.max_snapshots_per_turn
        if not force and not (enough_events and enough_time and under_cap):
            return
        if not final and not under_cap:
            return
        if final:
            title = "Progress tail — final"
        elif ctx.tool.lines:
            title = f"Progress tail — latest {len(ctx.tool.lines)} tools"
        else:
            title = "Progress tail — latest updates"
        if ctx.diagnostics.total_events:
            title += f" of {ctx.diagnostics.total_events} events"
        content = self.prepare_message(ctx, title + "\n" + content_body)
        try:
            result = await ctx.adapter.send(ctx.chat_id, content, metadata=ctx.metadata)
        except Exception as exc:
            logger.debug("hermes-progress-tail snapshot send failed: %s", exc)
            ctx.diagnostics.last_error = str(exc)
            ctx.delivery.disabled = True
            return
        if getattr(result, "success", False):
            ctx.delivery.snapshots_sent += 1
            ctx.delivery.fallback_send_count += 1
            ctx.diagnostics.new_events_since_snapshot = 0
            ctx.assistant.pending_chars = 0
            ctx.reasoning.pending_chars = 0
            ctx.delivery.last_render_at = time.monotonic()
        else:
            ctx.diagnostics.last_error = str(
                getattr(result, "error", "snapshot send failed") or "snapshot send failed"
            )
            ctx.delivery.disabled = True

    def prepare_message(self, ctx: SessionContext, content: str) -> str:
        return self.fit_message(content, self.message_limit(ctx))

    def prepare_telegram_rich_message(self, ctx: SessionContext, content: str) -> str:
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
