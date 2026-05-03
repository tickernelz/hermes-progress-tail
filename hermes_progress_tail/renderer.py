from __future__ import annotations

import logging
import re
import time
from typing import Any

from .compat import adapter_supports_edit
from .config import Settings
from .redaction import redact_text
from .state import ProgressEvent, ReasoningEvent, SessionContext, TodoItem, ToolEvent

logger = logging.getLogger(__name__)


def _truncate_local(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


class ProgressRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sessions: dict[str, SessionContext] = {}
        self.session_keys: dict[str, str] = {}

    def register_context(self, ctx: SessionContext) -> None:
        existing = self.sessions.get(ctx.session_id)
        if existing is not None:
            ctx.message_id = existing.message_id
            ctx.tool_lines = existing.tool_lines
            ctx.todo_items = existing.todo_items
            ctx.todo_updated_at = existing.todo_updated_at
            ctx.reasoning_text = existing.reasoning_text
            ctx.reasoning_pending_chars = existing.reasoning_pending_chars
            ctx.total_events = existing.total_events
            ctx.snapshots_sent = existing.snapshots_sent
            ctx.last_render_at = existing.last_render_at
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
                ctx.tool_lines.append(self._format_tool_line(ctx, event))
            else:
                pending = self._append_reasoning(ctx, event.text)
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
                return
            if ctx.strategy == "live_tail" and self._content(ctx):
                await self._render_live(ctx, force=True)
            elif (
                ctx.strategy == "snapshot"
                and self.settings.no_edit.final_summary
                and self._content(ctx)
            ):
                await self._render_snapshot(ctx, force=True, final=True)
            self._reset_turn(ctx)
        if purge:
            self.purge(session_id=ctx.session_id)

    def _append_reasoning(self, ctx: SessionContext, text: str) -> int:
        if not text:
            return 0
        merged = ctx.reasoning_text + str(text)
        merged = self._normalize_reasoning(merged)
        max_chars = self.settings.reasoning.max_chars
        if len(merged) > max_chars:
            merged = merged[-max_chars:].lstrip()
        ctx.reasoning_text = merged
        ctx.reasoning_pending_chars += len(str(text))
        return ctx.reasoning_pending_chars

    def _content(self, ctx: SessionContext) -> str:
        parts = []
        reasoning = self._reasoning_tail(ctx)
        if reasoning:
            parts.append(self._section("Reasoning", "💭", reasoning))
        todo = self._todo_section(ctx)
        if todo:
            parts.append(todo)
        if ctx.tool_lines:
            parts.append(self._section("Tools", "🧰", "\n".join(ctx.tool_lines)))
        content = "\n\n".join(parts)
        return redact_text(content) if self.settings.renderer.redact_secrets else content

    def _section(self, title: str, emoji: str, body: str) -> str:
        header = f"{emoji} {title}" if self.settings.renderer.style == "emoji" else title
        return header + "\n" + body

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
        header = f"📋 {title}" if self.settings.renderer.style == "emoji" else title
        lines = self._todo_lines(ctx.todo_items)
        return header + "\n" + "\n".join(lines)

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
        lines = []
        if by_status["in_progress"]:
            lines.append(
                "▶ "
                + _truncate_local(by_status["in_progress"][0], self.settings.todo.max_item_chars)
            )
        todo_settings = self.settings.todo
        for status, label, limit in (
            ("pending", "pending", todo_settings.max_pending),
            ("completed", "done", todo_settings.max_completed),
            ("cancelled", "cancelled", todo_settings.max_cancelled),
        ):
            values = by_status[status]
            if not values:
                continue
            visible = ", ".join(
                _truncate_local(value, todo_settings.max_item_chars) for value in values[:limit]
            )
            hidden = len(values) - limit
            suffix = f" +{hidden} more" if hidden > 0 else ""
            lines.append(f"{label}: {visible}{suffix}")
        return lines or ["no tasks"]

    @staticmethod
    def _reset_turn(ctx: SessionContext) -> None:
        ctx.message_id = None
        ctx.tool_lines.clear()
        ctx.todo_items = ()
        ctx.todo_updated_at = 0.0
        ctx.reasoning_text = ""
        ctx.reasoning_pending_chars = 0
        ctx.new_events_since_snapshot = 0
        ctx.snapshots_sent = 0
        ctx.total_events = 0

    def _reasoning_tail(self, ctx: SessionContext) -> str:
        text = ctx.reasoning_text.strip()
        if not text:
            return ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        max_lines = self.settings.reasoning.max_lines
        if lines:
            text = "\n".join(lines[-max_lines:])
        max_chars = self.settings.reasoning.max_chars
        if len(text) > max_chars:
            text = text[-max_chars:].lstrip()
        return redact_text(text)

    @staticmethod
    def _normalize_reasoning(text: str) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text

    async def _render_live(self, ctx: SessionContext, force: bool = False) -> None:
        now = time.monotonic()
        if not force and ctx.message_id and now - ctx.last_render_at < ctx.edit_interval:
            return
        content = self._content(ctx)
        if not content:
            return
        if ctx.message_id and ctx.can_edit:
            try:
                result = await ctx.adapter.edit_message(
                    chat_id=ctx.chat_id,
                    message_id=ctx.message_id,
                    content=content,
                )
            except Exception as exc:
                logger.debug("hermes-progress-tail edit failed: %s", exc)
                result = _Result(False, ctx.message_id, str(exc))
            if getattr(result, "success", False):
                ctx.reasoning_pending_chars = 0
                ctx.last_render_at = time.monotonic()
                return
            if self._is_unsupported_edit(getattr(result, "error", "")):
                ctx.strategy = "snapshot"
                ctx.can_edit = False
                await self._render_snapshot(ctx, force=True)
                return
            ctx.can_edit = False
        try:
            result = await ctx.adapter.send(ctx.chat_id, content, metadata=ctx.metadata)
        except Exception as exc:
            logger.debug("hermes-progress-tail send failed: %s", exc)
            ctx.disabled = True
            return
        if getattr(result, "success", False):
            ctx.message_id = getattr(result, "message_id", None) or ctx.message_id
            ctx.reasoning_pending_chars = 0
            ctx.last_render_at = time.monotonic()
        else:
            ctx.disabled = True

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
        title = (
            "Progress tail — final"
            if final
            else f"Progress tail — latest {len(ctx.tool_lines)} tools"
        )
        if ctx.total_events:
            title += f" of {ctx.total_events} events"
        content = title + "\n" + content_body
        try:
            result = await ctx.adapter.send(ctx.chat_id, content, metadata=ctx.metadata)
        except Exception as exc:
            logger.debug("hermes-progress-tail snapshot send failed: %s", exc)
            ctx.disabled = True
            return
        if getattr(result, "success", False):
            ctx.snapshots_sent += 1
            ctx.new_events_since_snapshot = 0
            ctx.reasoning_pending_chars = 0
            ctx.last_render_at = time.monotonic()
        else:
            ctx.disabled = True

    @staticmethod
    def _is_unsupported_edit(error: Any) -> bool:
        msg = str(error or "").lower()
        return any(
            part in msg
            for part in ("unsupported", "not supported", "not found", "unknown message", "edit")
        )


class _Result:
    def __init__(self, success: bool, message_id: str | None = None, error: str = ""):
        self.success = success
        self.message_id = message_id
        self.error = error
