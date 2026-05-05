from __future__ import annotations

import asyncio
import contextlib
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
    TodoItem,
    ToolEvent,
)
from ..settings.config import CODE_FENCE_DEFAULTS, Settings
from ..utils.redaction import redact_text
from ..utils.text import truncate_text
from .delegate import DelegateProgressRenderer
from .delegate import event_preview_args as event_preview_args
from .reasoning import normalize_reasoning_text, render_reasoning_tail, split_reasoning_blocks

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
            ctx.message_id = existing.message_id
            ctx.tool_lines = existing.tool_lines
            ctx.active_tool_lines = existing.active_tool_lines
            ctx.active_tool_fingerprints = existing.active_tool_fingerprints
            ctx.delegate_branches = existing.delegate_branches
            ctx.delegate_order = existing.delegate_order
            ctx.background_jobs = existing.background_jobs
            ctx.background_order = existing.background_order
            ctx.todo_items = existing.todo_items
            ctx.todo_updated_at = existing.todo_updated_at
            ctx.reasoning_text = existing.reasoning_text
            ctx.reasoning_pending_chars = existing.reasoning_pending_chars
            ctx.last_reasoning_source = existing.last_reasoning_source
            ctx.last_reasoning_chars = existing.last_reasoning_chars
            ctx.last_reasoning_at = existing.last_reasoning_at
            ctx.total_events = existing.total_events
            ctx.snapshots_sent = existing.snapshots_sent
            ctx.last_error = existing.last_error
            ctx.downgrade_reason = existing.downgrade_reason
            ctx.downgrade_at = existing.downgrade_at
            ctx.last_render_at = existing.last_render_at
            ctx.edit_state = existing.edit_state
            ctx.edit_backoff_until = existing.edit_backoff_until
            ctx.edit_failure_count = existing.edit_failure_count
            ctx.edit_recovery_sends = existing.edit_recovery_sends
            if existing.delayed_flush_task is not None and not existing.delayed_flush_task.done():
                self._cancel_delayed_flush(existing)
                ctx.edit_backoff_until = 0.0
            ctx.fallback_send_count = existing.fallback_send_count
            ctx.new_events_since_snapshot = existing.new_events_since_snapshot
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
        self, session_id: str = "", session_key: str = "", purge: bool = False
    ) -> None:
        ctx = self.find_context(session_id, session_key)
        if ctx is None:
            return
        async with ctx.lock:
            if ctx.disabled:
                self._cancel_delayed_flush(ctx)
                return
            if ctx.strategy == "live_tail" and self._content(ctx):
                await self._render_live(ctx, force=True, ignore_backoff=True)
            elif (
                ctx.strategy == "snapshot"
                and self.settings.no_edit.final_summary
                and self._content(ctx)
            ):
                await self._render_snapshot(ctx, force=True, final=True)
            self._reset_turn(ctx)
            if ctx.strategy == "live_tail" and self._content(ctx):
                await self._render_live(ctx, force=True, ignore_backoff=True)
            elif (
                ctx.strategy == "snapshot"
                and self.settings.no_edit.final_summary
                and self._content(ctx)
            ):
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
        parts = []
        reasoning = self._reasoning_tail(ctx)
        if reasoning:
            parts.append(self._section("Reasoning", "💭", reasoning))
        todo = self._todo_section(ctx)
        if todo:
            parts.append(todo)
        delegates = self.delegate_renderer.section(ctx)
        if delegates:
            parts.append(delegates)
        background = self._background_jobs_section(ctx)
        if background:
            parts.append(background)
        if ctx.tool_lines:
            parts.append(self._section("Tools", "🧰", "\n".join(ctx.tool_lines)))
        if self.settings.renderer.density == "debug":
            debug = self._debug_section(ctx)
            if debug:
                parts.append(debug)
        content = "\n\n".join(parts)
        return redact_text(content) if self.settings.renderer.redact_secrets else content

    def _section(self, title: str, emoji: str, body: str) -> str:
        header = f"{emoji} {title}" if self.settings.renderer.style == "emoji" else title
        return header + "\n" + body

    def _debug_section(self, ctx: SessionContext) -> str:
        lines = [f"strategy={ctx.strategy}", f"events={ctx.total_events}"]
        if ctx.edit_state != "editable":
            lines.append(f"edit_state={ctx.edit_state}")
        if ctx.downgrade_reason:
            lines.append(f"downgrade={ctx.downgrade_reason}")
        if ctx.last_error:
            lines.append(f"last_error={ctx.last_error}")
        return self._section("Debug", "🛠️", "\n".join(lines))

    def _format_tool_line(self, ctx: SessionContext, event: ToolEvent) -> str:
        timestamp_enabled = (
            self.settings.tools.timestamp if ctx.timestamp is None else ctx.timestamp
        )
        if not timestamp_enabled:
            return event.line
        timestamp_format = ctx.timestamp_format or self.settings.tools.timestamp_format
        timestamp = self._timestamp(event.created_at, timestamp_format)
        return f"[{timestamp}] {event.line}"

    def _todo_section(self, ctx: SessionContext) -> str:
        if not ctx.todo_items:
            return ""
        timestamp_enabled = (
            self.settings.tools.timestamp if ctx.timestamp is None else ctx.timestamp
        )
        timestamp_format = ctx.timestamp_format or self.settings.tools.timestamp_format
        timestamp = self._timestamp(ctx.todo_updated_at, timestamp_format)
        title = f"Todo [{timestamp}]" if timestamp_enabled else "Todo"
        if self.settings.renderer.density == "compact":
            return self._todo_compact(ctx.todo_items, title)
        header = f"📋 {title}" if self.settings.renderer.style == "emoji" else title
        lines = self._todo_lines(ctx.todo_items)
        return header + "\n" + "\n".join(lines)

    def _todo_compact(self, items: tuple[TodoItem, ...], title: str) -> str:
        counts = {"in_progress": 0, "pending": 0, "completed": 0, "cancelled": 0}
        current = ""
        for item in items:
            if item.status in counts:
                counts[item.status] += 1
            if item.status == "in_progress" and not current:
                current = item.content
        parts = []
        if current:
            parts.append("active: " + truncate_text(current, self.settings.todo.max_item_chars))
        for status, label in (
            ("pending", "pending"),
            ("completed", "done"),
            ("cancelled", "cancelled"),
        ):
            if counts[status]:
                parts.append(f"{counts[status]} {label}")
        body = " · ".join(parts) or "no tasks"
        prefix = "📋 " if self.settings.renderer.style == "emoji" else ""
        return f"{prefix}{title}: {body}"

    @staticmethod
    def _timestamp(value: float, fmt: str) -> str:
        try:
            return time.strftime(fmt, time.localtime(value))
        except Exception:
            return time.strftime("%H:%M", time.localtime(value))

    def _todo_lines(self, items: tuple[TodoItem, ...]) -> list[str]:
        by_status = {"in_progress": [], "pending": [], "completed": [], "cancelled": []}
        for item in items:
            by_status.setdefault(item.status, []).append(item.content)
        emoji = self.settings.renderer.style == "emoji"
        labels = {
            "in_progress": "🔄 in progress" if emoji else "in progress",
            "pending": "⏳ pending" if emoji else "pending",
            "completed": "✅ done" if emoji else "done",
            "cancelled": "❌ cancelled" if emoji else "cancelled",
        }
        lines = []
        todo_settings = self.settings.todo
        for status, limit in (
            ("in_progress", 1),
            ("pending", todo_settings.max_pending),
            ("completed", todo_settings.max_completed),
            ("cancelled", todo_settings.max_cancelled),
        ):
            values = by_status[status]
            if not values:
                continue
            visible = ", ".join(
                truncate_text(value, todo_settings.max_item_chars) for value in values[:limit]
            )
            hidden = len(values) - limit
            suffix = f" +{hidden} more" if hidden > 0 else ""
            lines.append(f"{labels[status]} ({len(values)}): {visible}{suffix}")
        return lines or ["no tasks"]

    def _apply_background_job_event(self, ctx: SessionContext, event: BackgroundJobEvent) -> None:
        job = ctx.background_jobs.get(event.process_id)
        if job is None:
            job = BackgroundJob(
                process_id=event.process_id,
                command=event.command,
                cwd=event.cwd,
                pid=event.pid,
                started_at=event.created_at,
                updated_at=event.created_at,
            )
            ctx.background_jobs[event.process_id] = job
            ctx.background_order.append(event.process_id)
        if event.command:
            job.command = event.command
        if event.cwd:
            job.cwd = event.cwd
        if event.pid is not None:
            job.pid = event.pid
        if event.output or event.event_type in {"output", "completed", "killed", "lost"}:
            self._update_background_output(job, event.output)
        if event.exited or event.event_type in {"completed", "killed", "lost"}:
            job.status = "completed" if event.exit_code in {0, None} else "failed"
            if event.event_type == "killed":
                job.status = "killed"
            elif event.event_type == "lost":
                job.status = "lost"
            job.exit_code = event.exit_code
            job.completed_at = event.created_at
            self._cancel_background_poll(job)
        elif event.event_type == "started":
            job.status = "running"
        job.updated_at = event.created_at
        self._prune_background_jobs(ctx)

    def _update_background_output(self, job: BackgroundJob, output: str) -> None:
        clean = self._normalize_background_output(output)
        if clean == job.last_output:
            return
        job.last_output = clean
        job.output_chars = len(clean)
        lines = self._useful_output_lines(clean)
        cfg = self.settings.background_jobs
        job.output_head = tuple(lines[: cfg.head_lines])
        job.output_tail = tuple(lines[-cfg.tail_lines :])

    def _background_jobs_section(self, ctx: SessionContext) -> str:
        if not self._background_jobs_enabled(ctx):
            return ""
        self._prune_background_jobs(ctx)
        jobs = [
            ctx.background_jobs[jid] for jid in ctx.background_order if jid in ctx.background_jobs
        ]
        visible = []
        for job in jobs:
            if job.status == "running" and not self.settings.background_jobs.list_running:
                continue
            if job.status != "running" and not self.settings.background_jobs.show_completed:
                continue
            visible.append(job)
        if not visible:
            return ""
        visible = visible[-self.settings.background_jobs.max_jobs :]
        lines: list[str] = []
        for idx, job in enumerate(visible, 1):
            lines.extend(self._background_job_lines(job, idx))
        return self._section("Background Jobs", "🖥", "\n".join(lines))

    def _background_job_lines(self, job: BackgroundJob, idx: int) -> list[str]:
        emoji = self.settings.renderer.style == "emoji"
        marker = self._background_marker(job.status, emoji)
        command = truncate_text(redact_text(job.command or job.process_id), 72)
        elapsed = self._duration_short((job.completed_at or time.time()) - job.started_at)
        title = f"[{idx}] {marker} {job.process_id} · {command} · {elapsed}"
        if job.status != "running" and job.exit_code is not None:
            title += f" · exit {job.exit_code}"
        lines = [title]
        rendered_head = [self._cap_bg_line(line) for line in job.output_head]
        rendered_tail = [self._cap_bg_line(line) for line in job.output_tail]
        if rendered_head:
            lines.append("    start: " + rendered_head[0])
            for line in rendered_head[1:]:
                lines.append("           " + line)
        tail_label = "end" if job.status != "running" else "tail"
        tail_lines = [line for line in rendered_tail if line not in rendered_head]
        if tail_lines:
            lines.append(f"    {tail_label}: " + tail_lines[0])
            for line in tail_lines[1:]:
                lines.append("         " + line)
        return lines

    @staticmethod
    def _background_marker(status: str, emoji: bool) -> str:
        if not emoji:
            return status
        return {
            "running": "🔄",
            "completed": "✅",
            "failed": "❌",
            "killed": "🛑",
            "lost": "⚠️",
        }.get(status, "•")

    def _cap_bg_line(self, line: str) -> str:
        return truncate_text(redact_text(line), self.settings.background_jobs.max_line_chars)

    @staticmethod
    def _duration_short(seconds: float) -> str:
        seconds = max(0, int(seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, secs = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {secs}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"

    @staticmethod
    def _normalize_background_output(output: str) -> str:
        text = str(output or "").replace("\r", "\n")
        text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
        return text

    @staticmethod
    def _useful_output_lines(output: str) -> list[str]:
        lines = []
        for raw in output.splitlines():
            line = " ".join(raw.strip().split())
            if not line:
                continue
            if any(
                noise in line
                for noise in (
                    "bash: cannot set terminal process group",
                    "bash: no job control in this shell",
                    "no job control in this shell",
                )
            ):
                continue
            lines.append(line)
        return lines

    def _prune_background_jobs(self, ctx: SessionContext) -> None:
        ttl = self.settings.background_jobs.completed_ttl_seconds
        now = time.time()
        for process_id in list(ctx.background_order):
            job = ctx.background_jobs.get(process_id)
            if job is None:
                with contextlib.suppress(ValueError):
                    ctx.background_order.remove(process_id)
                continue
            if job.status != "running" and job.completed_at and now - job.completed_at > ttl:
                self._cancel_background_poll(job)
                ctx.background_jobs.pop(process_id, None)
                with contextlib.suppress(ValueError):
                    ctx.background_order.remove(process_id)
        while len(ctx.background_order) > self.settings.background_jobs.max_jobs * 3:
            process_id = ctx.background_order.popleft()
            job = ctx.background_jobs.pop(process_id, None)
            if job is not None:
                self._cancel_background_poll(job)

    @staticmethod
    def _cancel_background_poll(job: BackgroundJob) -> None:
        task = job.poll_task
        if task is not None and not task.done():
            task.cancel()
        job.poll_task = None

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
        keep_progress_bubble = bool(ctx.background_jobs)
        if not keep_progress_bubble:
            ctx.message_id = None
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
        ctx.can_edit = True
        ctx.edit_state = "editable"
        ctx.edit_backoff_until = 0.0
        ctx.edit_failure_count = 0
        ctx.edit_recovery_sends = 0
        ProgressRenderer._cancel_delayed_flush(ctx)
        ctx.fallback_send_count = 0
        ctx.new_events_since_snapshot = 0
        ctx.snapshots_sent = 0
        ctx.total_events = 0

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
