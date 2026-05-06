from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from ..gateway.compat import adapter_supports_edit
from ..models.state import (
    BackgroundJob,
    BackgroundJobEvent,
    DelegateEvent,
    ProgressEvent,
    ReasoningEvent,
    SessionContext,
    ToolEvent,
)
from ..settings.config import CODE_FENCE_DEFAULTS, Settings
from .background_jobs import (
    apply_background_job_event,
    background_jobs_section,
    cancel_background_poll,
)
from .delegate import DelegateProgressRenderer
from .delegate import event_preview_args as event_preview_args
from .finalization import (
    finalize_progress_message,
    has_background_jobs,
    reset_turn,
    should_flush_before_reset,
)
from .reasoning import normalize_reasoning_text, render_reasoning_tail, split_reasoning_blocks
from .sections import (
    compose_content,
    debug_section,
    format_tool_line_for_context,
    section,
    timestamp_text,
    todo_section,
)

logger = logging.getLogger(__name__)


class ProgressRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.delegate_renderer = DelegateProgressRenderer(settings)
        self.sessions: dict[str, SessionContext] = {}
        self.session_keys: dict[str, str] = {}

    def register_context(self, ctx: SessionContext) -> None:
        existing = self.sessions.get(ctx.session_id)
        if existing is not None:
            reuse_progress = existing.progress_state == "active"
            if reuse_progress:
                ctx.message_id = existing.message_id
                ctx.tool_lines = existing.tool_lines
                ctx.active_tool_lines = existing.active_tool_lines
                ctx.active_tool_fingerprints = existing.active_tool_fingerprints
                ctx.delegate_branches = existing.delegate_branches
                ctx.delegate_order = existing.delegate_order
                ctx.todo_items = existing.todo_items
                ctx.todo_updated_at = existing.todo_updated_at
                ctx.reasoning_text = existing.reasoning_text
                ctx.reasoning_pending_chars = existing.reasoning_pending_chars
                ctx.last_reasoning_source = existing.last_reasoning_source
                ctx.last_reasoning_chars = existing.last_reasoning_chars
                ctx.last_reasoning_at = existing.last_reasoning_at
            ctx.background_jobs = existing.background_jobs
            ctx.background_order = existing.background_order
            ctx.progress_state = "active"
            ctx.finalized_at = 0.0
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
            ctx.new_events_since_snapshot = (
                existing.new_events_since_snapshot if reuse_progress else 0
            )
            ctx.lock = existing.lock
        ctx.resize(ctx.lines)
        if ctx.strategy == "auto":
            ctx.strategy = "live_tail" if adapter_supports_edit(ctx.adapter) else "snapshot"
        if ctx.strategy == "live_tail" and not adapter_supports_edit(ctx.adapter):
            ctx.strategy = "snapshot"
        self.sessions[ctx.session_id] = ctx
        if ctx.session_key:
            self.session_keys[ctx.session_key] = ctx.session_id

    def find_context(self, session_id: str = "", session_key: str = "") -> SessionContext | None:
        if session_id and session_id in self.sessions:
            return self.sessions[session_id]
        if session_key and session_key in self.session_keys:
            return self.sessions.get(self.session_keys[session_key])
        return None

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
                    if previous in ctx.tool_lines:
                        items = list(ctx.tool_lines)
                        items[items.index(previous)] = line
                        ctx.tool_lines.clear()
                        ctx.tool_lines.extend(items)
                    else:
                        ctx.tool_lines.append(line)
                    if event.tool_call_id:
                        ctx.active_tool_lines[event.tool_call_id] = line
                    fingerprint = self._tool_line_fingerprint(line)
                    if fingerprint:
                        ctx.active_tool_fingerprints[fingerprint] = line
                    force = True
                else:
                    ctx.tool_lines.append(line)
                    if event.tool_call_id:
                        ctx.active_tool_lines[event.tool_call_id] = line
                    fingerprint = self._tool_line_fingerprint(line)
                    if fingerprint:
                        ctx.active_tool_fingerprints[fingerprint] = line
            elif isinstance(event, DelegateEvent):
                self.delegate_renderer.apply_event(ctx, event)
            elif isinstance(event, BackgroundJobEvent):
                self._apply_background_job_event(ctx, event)
                force = True
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
    ) -> None:
        ctx = self.find_context(session_id, session_key)
        if ctx is None:
            return
        async with ctx.lock:
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
            if ctx.progress_state == "background_active" and self._content(ctx):
                if ctx.strategy == "live_tail":
                    await self._render_live(ctx, force=True, ignore_backoff=True)
                elif ctx.strategy == "snapshot" and self.settings.no_edit.final_summary:
                    await self._render_snapshot(ctx, force=True, final=True)
        if purge:
            self.purge(session_id=ctx.session_id)

    def _append_reasoning(self, ctx: SessionContext, event: ReasoningEvent) -> int:
        if not event.text:
            return 0
        merged = ctx.reasoning_text + str(event.text)
        merged = self._normalize_reasoning(merged)
        max_chars = self.settings.reasoning.max_chars
        if len(merged) > max_chars * 4:
            merged = self._trim_reasoning_buffer(merged, max_chars * 4)
        ctx.reasoning_text = merged
        ctx.reasoning_pending_chars += len(str(event.text))
        ctx.last_reasoning_source = event.source or "structured_reasoning"
        ctx.last_reasoning_chars = len(str(event.text))
        ctx.last_reasoning_at = event.created_at
        return ctx.reasoning_pending_chars

    def _find_previous_tool_line(self, ctx: SessionContext, event: ToolEvent, line: str) -> str:
        if event.tool_call_id:
            previous = ctx.active_tool_lines.get(event.tool_call_id, "")
            if previous:
                return previous
        fingerprint = self._tool_line_fingerprint(line)
        if fingerprint:
            previous = ctx.active_tool_fingerprints.get(fingerprint, "")
            if previous:
                return previous
        return ""

    @staticmethod
    def _tool_line_fingerprint(line: str) -> str:
        text = line.strip()
        if "] " in text and text.startswith("["):
            text = text.split("] ", 1)[1]
        for prefix in (
            "✅ ",
            "❌ ",
            "🔎 ",
            "📖 ",
            "✍️ ",
            "🔧 ",
            "💻 ",
            "📋 ",
            "🧑‍💻 ",
            "🧰 ",
        ):
            if text.startswith(prefix):
                text = text[len(prefix) :]
        for suffix in (" · running", " · done", " · failed"):
            if suffix in text:
                text = text.split(suffix, 1)[0]
                break
        return text.strip()

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
                ctx.reasoning_pending_chars = 0
                ctx.edit_state = "editable"
                ctx.edit_backoff_until = 0.0
                ctx.edit_failure_count = 0
                ctx.last_render_at = time.monotonic()
                return
            error = str(getattr(result, "error", "") or "edit failed")
            kind = self._classify_edit_error(error)
            if kind == "noop_success":
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
            ctx.last_error = str(exc)
            ctx.disabled = True
            return
        if getattr(result, "success", False):
            ctx.message_id = getattr(result, "message_id", None) or ctx.message_id
            ctx.can_edit = True
            ctx.edit_state = "editable"
            ctx.edit_backoff_until = 0.0
            ctx.edit_failure_count = 0
            if recovery:
                ctx.edit_recovery_sends += 1
                ctx.fallback_send_count += 1
            ctx.reasoning_pending_chars = 0
            ctx.last_render_at = time.monotonic()
        else:
            ctx.last_error = str(getattr(result, "error", "send failed") or "send failed")
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

    @staticmethod
    def _cancel_delayed_flush(ctx: SessionContext) -> None:
        task = ctx.delayed_flush_task
        if task is not None and not task.done():
            task.cancel()
        ctx.delayed_flush_task = None

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
            ctx.reasoning_pending_chars = 0
            ctx.last_render_at = time.monotonic()
        else:
            ctx.last_error = str(
                getattr(result, "error", "snapshot send failed") or "snapshot send failed"
            )
            ctx.disabled = True

    def _prepare_message(self, ctx: SessionContext, content: str) -> str:
        fence = self._should_code_fence(ctx)
        limit = self._message_limit(ctx)
        overhead = self._code_fence_overhead() if fence else 0
        body_limit = max(0, limit - overhead) if limit > 0 else 0
        content = self._fit_message(content, body_limit)
        if fence:
            content = self._code_fence(content)
        return self._fit_message(content, limit)

    @staticmethod
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

    def _should_code_fence(self, ctx: SessionContext) -> bool:
        ctx_mode = str(getattr(ctx, "code_fence", "") or "").lower()
        mode = ctx_mode or self.settings.renderer.code_fence
        if mode == "off":
            return False
        if mode == "on":
            return ctx.platform in CODE_FENCE_DEFAULTS
        return ctx.platform in CODE_FENCE_DEFAULTS

    def _code_fence(self, content: str) -> str:
        lang = self.settings.renderer.code_fence_language.strip()
        safe = content.replace("```", "`\u200b``")
        return f"```{lang}\n{safe}\n```"

    def _code_fence_overhead(self) -> int:
        return len(f"```{self.settings.renderer.code_fence_language.strip()}\n\n```")

    @staticmethod
    def _message_limit(ctx: SessionContext) -> int:
        if ctx.platform == "telegram":
            return 4096
        return 0

    @staticmethod
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
            part in msg
            for part in ("flood", "retry after", "too many requests", "rate limit", "429")
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
            )
        ):
            return "transient"
        return "unknown_transient"

    @staticmethod
    def _edit_backoff_seconds(error: Any, kind: str, failure_count: int) -> float:
        msg = str(error or "").lower()
        match = re.search(
            r"(?:retry after|flood_control:|retry_after=)\s*:?\s*(\d+(?:\.\d+)?)", msg
        )
        if match:
            return min(float(match.group(1)), 30.0)
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
