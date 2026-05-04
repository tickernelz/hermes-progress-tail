from __future__ import annotations

import logging
import os
import re
import time
from contextlib import suppress
from pathlib import Path, PureWindowsPath
from typing import Any

from .compat import adapter_supports_edit
from .config import Settings
from .formatter import format_tool_line
from .redaction import redact_text
from .state import (
    DelegateBranch,
    DelegateEvent,
    DelegateLine,
    ProgressEvent,
    ReasoningEvent,
    SessionContext,
    TodoItem,
    ToolEvent,
)

logger = logging.getLogger(__name__)


def event_preview_args(event: DelegateEvent) -> dict[str, Any]:
    preview = str(event.preview or "").strip()
    args = dict(event.args) if isinstance(event.args, dict) else {}
    if event.tool_name == "terminal" and preview:
        command = str(args.get("command") or "").strip()
        if not command or len(preview) > len(command):
            args["command"] = preview
    if args:
        if preview:
            if event.tool_name in {"read_file", "write_file"} and not (
                args.get("path") or args.get("file_path")
            ):
                args["path"] = preview
            elif event.tool_name == "search_files" and not (args.get("pattern") or args.get("q")):
                args["pattern"] = preview
        return args
    if not preview:
        return {}
    if event.tool_name == "terminal":
        return {"command": preview}
    if event.tool_name in {"read_file", "write_file"}:
        return {"path": preview}
    if event.tool_name == "search_files":
        return {"pattern": preview}
    if event.tool_name == "patch":
        if "*** " in preview:
            return {"mode": "patch", "patch": preview}
        return {"path": preview, "old_string": "", "new_string": ""}
    return {}


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
            ctx.active_tool_lines = existing.active_tool_lines
            ctx.delegate_branches = existing.delegate_branches
            ctx.delegate_order = existing.delegate_order
            ctx.todo_items = existing.todo_items
            ctx.todo_updated_at = existing.todo_updated_at
            ctx.reasoning_text = existing.reasoning_text
            ctx.reasoning_pending_chars = existing.reasoning_pending_chars
            ctx.total_events = existing.total_events
            ctx.snapshots_sent = existing.snapshots_sent
            ctx.last_error = existing.last_error
            ctx.downgrade_reason = existing.downgrade_reason
            ctx.downgrade_at = existing.downgrade_at
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
            if isinstance(event, DelegateEvent) and not ctx.delegates_enabled:
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
                if event.replace_existing and event.tool_call_id:
                    previous = ctx.active_tool_lines.get(event.tool_call_id)
                    if previous in ctx.tool_lines:
                        items = list(ctx.tool_lines)
                        items[items.index(previous)] = line
                        ctx.tool_lines.clear()
                        ctx.tool_lines.extend(items)
                    else:
                        ctx.tool_lines.append(line)
                    ctx.active_tool_lines[event.tool_call_id] = line
                    force = True
                else:
                    ctx.tool_lines.append(line)
                    if event.tool_call_id:
                        ctx.active_tool_lines[event.tool_call_id] = line
            elif isinstance(event, DelegateEvent):
                self._apply_delegate_event(ctx, event)
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
        delegates = self._delegate_section(ctx)
        if delegates:
            parts.append(delegates)
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

    def _apply_delegate_event(self, ctx: SessionContext, event: DelegateEvent) -> None:
        event_type = event.event_type
        key = event.subagent_id or f"task-{event.task_index}"
        branch = ctx.delegate_branches.get(key)
        if branch is None:
            branch = DelegateBranch(
                subagent_id=key,
                task_index=event.task_index,
                task_count=event.task_count,
                goal=event.goal,
                model=event.model,
                started_at=event.created_at,
            )
            branch.resize(self.settings.delegates.lines_per_delegate)
            ctx.delegate_branches[key] = branch
            ctx.delegate_order.append(key)
        else:
            if event_type in {"subagent.spawn_requested", "subagent.start"} and branch.completed_at:
                branch.completion_line = ""
                branch.lines.clear()
                branch.completed_at = 0.0
                branch.duration_seconds = 0.0
                branch.tool_count = 0
                branch.lifecycle_started = False
            branch.task_index = event.task_index
            branch.task_count = event.task_count or branch.task_count
            branch.goal = event.goal or branch.goal
            branch.model = event.model or branch.model
            branch.resize(self.settings.delegates.lines_per_delegate)
        branch.updated_at = event.created_at
        if event.tool_count:
            branch.tool_count = event.tool_count
        if event_type in {"subagent.spawn_requested", "subagent.start"}:
            branch.status = "running" if event_type == "subagent.start" else "queued"
            if not branch.lifecycle_started:
                branch.started_at = event.created_at
                branch.lifecycle_started = True
            return
        if event_type in {"subagent.complete", "subagent.failed"}:
            branch.status = event.status or (
                "failed" if event_type == "subagent.failed" else "completed"
            )
            branch.completed_at = event.created_at
            branch.duration_seconds = event.duration_seconds
            if event.summary and self.settings.delegates.show_completion:
                branch.completion_line = self._format_delegate_completion_line(event)
            return
        if event_type in {"subagent.thinking", "delegate.task_thinking", "_thinking"}:
            if self.settings.delegates.thinking != "summary":
                return
            text = event.preview or event.tool_name or event.summary
            if text:
                branch.lines.append(
                    DelegateLine(
                        "update",
                        self._delegate_line(
                            f"thinking: {text}", self.settings.delegates.max_line_chars
                        ),
                    )
                )
            return
        branch.status = event.status or (
            "running" if branch.status in {"", "pending"} else branch.status
        )
        line = self._format_delegate_progress_line(event)
        if line:
            branch.lines.append(line)

    def _format_delegate_progress_line(self, event: DelegateEvent) -> DelegateLine | None:
        if event.tool_name:
            return self._format_delegate_tool_line(event)
        text = self._delegate_line(
            event.preview or event.summary or "", self.settings.delegates.max_line_chars
        )
        if not text:
            return None
        return DelegateLine("update", text)

    def _format_delegate_tool_line(self, event: DelegateEvent) -> DelegateLine | None:
        args = event_preview_args(event)
        if self._delegate_tool_detail_is_missing(event.tool_name, args, event.preview):
            if self.settings.renderer.density != "debug":
                return None
            return DelegateLine("debug", f"{event.tool_name}: <unknown>", tool_name=event.tool_name)
        if (
            event.tool_name == "patch"
            and event.preview
            and not event.args
            and "*** " not in event.preview
        ):
            text = self._delegate_line(
                f"patch: {event.preview}", self.settings.delegates.max_line_chars
            )
            return DelegateLine("tool", text, tool_name=event.tool_name)
        text = format_tool_line(
            event.tool_name,
            args,
            preview=event.preview,
            preview_length=self.settings.delegates.max_line_chars,
            patch_detail=self.settings.patch.detail,
            patch_preview_chars=self.settings.patch.preview_chars,
            patch_max_files=self.settings.patch.max_files,
        )
        if self.settings.renderer.style != "emoji":
            text = self._strip_tool_emoji(text)
        text = self._delegate_line(text, self.settings.delegates.max_line_chars)
        if not text:
            return None
        return DelegateLine(
            "tool",
            text,
            details=self._delegate_tool_details(event, args),
            tool_name=event.tool_name,
        )

    @staticmethod
    def _delegate_tool_detail_is_missing(
        tool_name: str, args: dict[str, Any], preview: str
    ) -> bool:
        if tool_name == "terminal":
            return not str(args.get("command") or preview or "").strip()
        if tool_name == "read_file":
            return not str(args.get("path") or preview or "").strip()
        if tool_name == "write_file":
            return not str(args.get("path") or args.get("file_path") or preview or "").strip()
        if tool_name == "search_files":
            return not str(args.get("pattern") or args.get("q") or preview or "").strip()
        return False

    def _delegate_tool_details(self, event: DelegateEvent, args: dict[str, Any]) -> tuple[str, ...]:
        if self.settings.renderer.density != "normal" or event.tool_name != "terminal":
            return ()
        details: list[str] = []
        cwd = args.get("workdir") or args.get("cwd")
        if cwd:
            details.append(f"cwd: {self._delegate_cwd(cwd)}")
        first = self._terminal_first_line(str(args.get("command") or event.preview or ""))
        if first:
            details.append(f"first: {first}")
        return tuple(details[:2])

    @staticmethod
    def _terminal_first_line(command: str) -> str:
        lines = [line.strip() for line in str(command or "").splitlines() if line.strip()]
        if not lines:
            return ""
        return _truncate_local(redact_text(lines[0]), 80)

    @staticmethod
    def _delegate_cwd(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if raw in {".", "./"}:
            return "."
        normalized = raw.replace("\\", "/")
        if normalized.endswith("/hermes-progress-tail"):
            return "."
        home_display = ProgressRenderer._home_relative_path(raw)
        if home_display:
            return _truncate_local(redact_text(home_display), 80)
        return _truncate_local(redact_text(raw), 80)

    @staticmethod
    def _home_relative_path(raw: str) -> str:
        candidates = []
        with suppress(Exception):
            candidates.append(str(Path.home()))
        env_home = os.environ.get("HOME")
        if env_home:
            candidates.append(env_home)
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            candidates.append(userprofile)
        home_drive = os.environ.get("HOMEDRIVE")
        home_path = os.environ.get("HOMEPATH")
        if home_drive and home_path:
            candidates.append(home_drive + home_path)
        for home in dict.fromkeys(candidates):
            display = ProgressRenderer._relative_to_home(raw, home)
            if display:
                return display
        return ""

    @staticmethod
    def _relative_to_home(raw: str, home: str) -> str:
        home = str(home or "").strip()
        if not home:
            return ""
        raw_norm = raw.replace("\\", "/").rstrip("/")
        home_norm = home.replace("\\", "/").rstrip("/")
        if not raw_norm or not home_norm:
            return ""
        if raw_norm == home_norm:
            return "~"
        if raw_norm.startswith(home_norm + "/"):
            rel = raw_norm[len(home_norm) + 1 :].strip("/")
            rel = re.sub(r"/+", "/", rel)
            return "~/" + rel if rel else "~"
        try:
            raw_win = PureWindowsPath(raw)
            home_win = PureWindowsPath(home)
            rel = raw_win.relative_to(home_win)
            return "~/" + rel.as_posix()
        except Exception:
            return ""

    def _format_delegate_completion_line(self, event: DelegateEvent) -> str:
        summary = self._brief_completion_summary(event.summary)
        if not summary:
            return ""
        label = (
            "failed"
            if event.event_type == "subagent.failed" or event.status == "failed"
            else "done"
        )
        if self.settings.renderer.style == "emoji":
            label = f"{self._status_emoji(label)} {label}"
        return self._delegate_line(f"{label}: {summary}", self.settings.delegates.max_line_chars)

    @staticmethod
    def _strip_tool_emoji(text: str) -> str:
        return re.sub(r"^[^\w\s]+\s+", "", str(text or "")).strip()

    @staticmethod
    def _brief_completion_summary(text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        if raw.startswith("{") or raw.startswith("["):
            return re.sub(r"\s+", " ", raw)
        lines = [line.strip(" -•\t") for line in raw.splitlines() if line.strip()]
        if len(lines) >= 2 and lines[0].endswith(":"):
            value = f"{lines[0]} {lines[1]}"
        else:
            value = re.sub(r"\s+", " ", raw).strip()
        parts = re.split(r"(?:\s+-\s+|[.!?]\s+)", value, maxsplit=1)
        brief = parts[0].strip(" -•\t")
        return brief or value

    @staticmethod
    def _delegate_line(text: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        return _truncate_local(text, limit)

    def _delegate_section(self, ctx: SessionContext) -> str:
        if not ctx.delegate_branches:
            return ""
        settings = self.settings.delegates
        visible_keys = list(ctx.delegate_order)[-settings.max_delegates :]
        lines: list[str] = []
        for key in visible_keys:
            branch = ctx.delegate_branches.get(key)
            if branch is None:
                continue
            title = self._delegate_title(branch)
            if self.settings.renderer.density == "compact":
                current = branch.completion_line or (
                    self._delegate_compact_line(branch.lines[-1])
                    if branch.lines
                    else branch.status or "running"
                )
                lines.append(f"{title}: {current}")
                continue
            lines.append(title)
            delegate_lines = list(branch.lines)
            has_result = bool(branch.completion_line)
            total = len(delegate_lines) + (1 if has_result else 0)
            for index, item in enumerate(delegate_lines):
                connector = self._delegate_connector(index, total)
                lines.append(f"{connector} {self._delegate_event_label(item)}")
                detail_connector = "   " if connector == "└" else "│  "
                for detail in item.details:
                    lines.append(f"{detail_connector}{detail}")
            if branch.completion_line:
                connector = self._delegate_connector(len(delegate_lines), total)
                lines.append(f"{connector} result: {branch.completion_line}")
        hidden = len(ctx.delegate_order) - len(visible_keys)
        if hidden > 0:
            lines.append(f"+{hidden} older delegate{'s' if hidden != 1 else ''}")
        return self._section("Delegates", "🔀", "\n".join(lines)) if lines else ""

    def _delegate_title(self, branch: DelegateBranch) -> str:
        settings = self.settings.delegates
        label = _truncate_local(
            branch.goal or f"task {branch.task_index + 1}", settings.max_goal_chars
        )
        status = branch.status or "running"
        if self.settings.renderer.style == "emoji":
            status = f"{self._status_emoji(status)} {status}"
        parts = [f"[{branch.task_index + 1}/{branch.task_count}] {status}"]
        if label:
            parts.append(label)
        if settings.show_tool_count and branch.tool_count:
            parts.append(f"{branch.tool_count} tools")
        if settings.show_model and branch.model:
            parts.append(branch.model)
        if settings.show_completion and branch.duration_seconds:
            parts.append(self._duration(branch.duration_seconds))
        return " · ".join(parts)

    def _delegate_connector(self, index: int, total: int) -> str:
        if total <= 1:
            return "└"
        return "└" if index == total - 1 else "├"

    def _delegate_compact_line(self, item: DelegateLine) -> str:
        if item.kind == "debug":
            return item.text
        return item.text

    def _delegate_event_label(self, item: DelegateLine) -> str:
        if item.kind == "tool":
            return f"tool: {item.text}"
        if item.kind == "debug":
            return f"debug: {item.text}"
        return f"update: {item.text}"

    @staticmethod
    def _looks_like_progress_output(text: str) -> bool:
        lowered = str(text or "").lower()
        return any(token in lowered for token in ("<empty>", "stdout", "stderr", "exit_code"))

    @staticmethod
    def _status_emoji(status: str) -> str:
        normalized = str(status or "").strip().lower()
        if normalized in {"completed", "done"}:
            return "✅"
        if normalized in {"failed", "cancelled"}:
            return "❌"
        if normalized in {"queued", "pending"}:
            return "⏳"
        return "🔄"

    @staticmethod
    def _duration(seconds: float) -> str:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return ""
        if value < 10:
            return f"{value:.1f}s"
        return f"{value:.0f}s"

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
            parts.append("active: " + _truncate_local(current, self.settings.todo.max_item_chars))
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
                _truncate_local(value, todo_settings.max_item_chars) for value in values[:limit]
            )
            hidden = len(values) - limit
            suffix = f" +{hidden} more" if hidden > 0 else ""
            lines.append(f"{labels[status]} ({len(values)}): {visible}{suffix}")
        return lines or ["no tasks"]

    @staticmethod
    def _reset_turn(ctx: SessionContext) -> None:
        ctx.message_id = None
        ctx.tool_lines.clear()
        ctx.active_tool_lines.clear()
        ctx.delegate_branches.clear()
        ctx.delegate_order.clear()
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
                ctx.last_error = str(exc)
                result = _Result(False, ctx.message_id, str(exc))
            if getattr(result, "success", False):
                ctx.reasoning_pending_chars = 0
                ctx.last_render_at = time.monotonic()
                return
            if self._is_unsupported_edit(getattr(result, "error", "")):
                error = str(getattr(result, "error", "") or "edit failed")
                ctx.strategy = "snapshot"
                ctx.can_edit = False
                ctx.downgrade_reason = error
                ctx.downgrade_at = time.time()
                ctx.last_error = error
                await self._render_snapshot(ctx, force=True)
                return
            ctx.can_edit = False
        try:
            result = await ctx.adapter.send(ctx.chat_id, content, metadata=ctx.metadata)
        except Exception as exc:
            logger.debug("hermes-progress-tail send failed: %s", exc)
            ctx.last_error = str(exc)
            ctx.disabled = True
            return
        if getattr(result, "success", False):
            ctx.message_id = getattr(result, "message_id", None) or ctx.message_id
            ctx.reasoning_pending_chars = 0
            ctx.last_render_at = time.monotonic()
        else:
            ctx.last_error = str(getattr(result, "error", "send failed") or "send failed")
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
        if final:
            title = "Progress tail — final"
        elif ctx.tool_lines:
            title = f"Progress tail — latest {len(ctx.tool_lines)} tools"
        else:
            title = "Progress tail — latest updates"
        if ctx.total_events:
            title += f" of {ctx.total_events} events"
        content = title + "\n" + content_body
        try:
            result = await ctx.adapter.send(ctx.chat_id, content, metadata=ctx.metadata)
        except Exception as exc:
            logger.debug("hermes-progress-tail snapshot send failed: %s", exc)
            ctx.last_error = str(exc)
            ctx.disabled = True
            return
        if getattr(result, "success", False):
            ctx.snapshots_sent += 1
            ctx.new_events_since_snapshot = 0
            ctx.reasoning_pending_chars = 0
            ctx.last_render_at = time.monotonic()
        else:
            ctx.last_error = str(
                getattr(result, "error", "snapshot send failed") or "snapshot send failed"
            )
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
