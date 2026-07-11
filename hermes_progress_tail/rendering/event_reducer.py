from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models.state import (
    AssistantEvent,
    AssistantLine,
    BackgroundJobEvent,
    DelegateEvent,
    ProgressEvent,
    ReasoningEvent,
    SessionContext,
    ToolEvent,
)
from ..settings.config import Settings
from .background_jobs import apply_background_job_event
from .reasoning import (
    normalize_reasoning_text,
    split_reasoning_blocks,
    split_reasoning_stream_suffix,
    trim_reasoning_fenced_tail,
)


@dataclass(frozen=True)
class ReductionResult:
    force: bool = False
    skip_render: bool = False
    pending_chars: int = 0
    delegate_cleanup: tuple[str, Any] | None = None
    background_poll_cancellations: tuple[Any, ...] = ()


class EventReducer:
    def __init__(
        self,
        settings: Settings,
        delegate_renderer=None,
        *,
        schedule_delegate_cleanup=None,
    ) -> None:
        self.settings = settings
        self.delegate_renderer = delegate_renderer
        # Compatibility reference only; reduction never invokes or schedules it.
        self.schedule_delegate_cleanup = schedule_delegate_cleanup

    def replace_settings(self, settings: Settings) -> None:
        self.settings = settings

    def accepts(self, ctx: SessionContext, event: ProgressEvent) -> bool:
        if isinstance(event, ToolEvent):
            return bool(ctx.routing.tools_enabled)
        if isinstance(event, AssistantEvent):
            return bool(ctx.routing.assistant_enabled)
        if isinstance(event, ReasoningEvent):
            return bool(ctx.routing.reasoning_enabled)
        if isinstance(event, DelegateEvent):
            return bool(ctx.routing.delegates_enabled)
        if isinstance(event, BackgroundJobEvent):
            return bool(
                self.settings.background_jobs.enabled and ctx.routing.background_jobs_enabled
            )
        return False

    def reduce(
        self, ctx: SessionContext, event: ProgressEvent, *, tool_line: str = ""
    ) -> ReductionResult:
        if isinstance(event, ToolEvent):
            return self.apply_tool(ctx, event, tool_line)
        if isinstance(event, AssistantEvent):
            return ReductionResult(pending_chars=self.append_assistant(ctx, event))
        if isinstance(event, ReasoningEvent):
            return ReductionResult(pending_chars=self.append_reasoning(ctx, event))
        if isinstance(event, DelegateEvent):
            self.delegate_renderer.apply_event(ctx, event)
            if self.delegate_event_is_terminal(event):
                key = event.subagent_id or f"task-{event.task_index}"
                branch = ctx.delegate.branches.get(key)
                cleanup = (key, branch) if branch is not None else None
                return ReductionResult(force=True, delegate_cleanup=cleanup)
            return ReductionResult()
        if isinstance(event, BackgroundJobEvent):
            cancellations = []
            apply_background_job_event(
                ctx,
                event,
                settings=self.settings.background_jobs,
                cancel_poll=cancellations.append,
            )
            return ReductionResult(force=True, background_poll_cancellations=tuple(cancellations))
        return ReductionResult()

    @staticmethod
    def delegate_event_is_terminal(event: DelegateEvent) -> bool:
        return bool(
            event.event_type in {"subagent.complete", "subagent.failed"}
            or str(event.status or "").strip().lower()
            in {"completed", "done", "success", "failed", "error", "cancelled", "killed"}
        )

    def apply_tool(self, ctx: SessionContext, event: ToolEvent, line: str) -> ReductionResult:
        if event.tool_name == "todo" and event.todo_items:
            if self.settings.todo.sticky:
                ctx.tool.todo_items = event.todo_items
                ctx.tool.todo_updated_at = event.created_at
            if self.settings.todo.hide_tool_line:
                return ReductionResult(skip_render=True)
        if event.replace_existing:
            previous = self.find_previous_tool_line(ctx, event, line)
            terminal = self.record_tool_lifecycle(ctx, event, line)
            if terminal:
                self.clear_previous_tool_tracking(ctx, event, previous)
            if previous in ctx.tool.lines:
                items = list(ctx.tool.lines)
                items[items.index(previous)] = line
                ctx.tool.lines.clear()
                ctx.tool.lines.extend(items)
            else:
                ctx.tool.lines.append(line)
            if not terminal:
                self.clear_previous_tool_tracking(ctx, event, previous)
                self.track_active_tool(ctx, event, line)
            return ReductionResult(force=True)
        self.record_tool_lifecycle(ctx, event, line)
        ctx.tool.lines.append(line)
        self.track_active_tool(ctx, event, line)
        return ReductionResult()

    def record_tool_lifecycle(self, ctx, event, line):
        if event.tool_name == "todo":
            return False
        identity = self.tool_event_identity(event, line)
        if not event.replace_existing:
            self.complete_active_tools(ctx)
            ctx.tool.started_count += 1
            return False
        status = self.tool_line_terminal_status(line)
        if not status or identity in ctx.tool.completed_ids:
            return bool(status)
        if status == "failed":
            ctx.tool.failed_count += 1
        else:
            ctx.tool.completed_count += 1
        ctx.tool.completed_ids.add(identity)
        self.clear_previous_tool_tracking(ctx, event, line)
        return True

    def complete_active_tools(self, ctx):
        active_lines = set(ctx.tool.active_lines.values())
        identities = {"id:" + key for key in ctx.tool.active_lines}
        identities.update(
            "fp:" + key
            for key, line in ctx.tool.active_fingerprints.items()
            if line not in active_lines
        )
        new = identities - ctx.tool.completed_ids
        ctx.tool.completed_count += len(new)
        ctx.tool.completed_ids.update(new)
        ctx.tool.active_lines.clear()
        ctx.tool.active_fingerprints.clear()

    def clear_previous_tool_tracking(self, ctx, event, previous):
        if event.tool_call_id:
            ctx.tool.active_lines.pop(event.tool_call_id, None)
        fingerprint = self.tool_line_fingerprint(previous)
        if fingerprint:
            ctx.tool.active_fingerprints.pop(fingerprint, None)

    def track_active_tool(self, ctx, event, line):
        if event.tool_call_id:
            ctx.tool.active_lines[event.tool_call_id] = line
        fingerprint = self.tool_line_fingerprint(line)
        if fingerprint:
            ctx.tool.active_fingerprints[fingerprint] = line

    def tool_event_identity(self, event, line):
        if event.tool_call_id:
            return "id:" + event.tool_call_id
        fingerprint = self.tool_line_fingerprint(line)
        return "fp:" + fingerprint if fingerprint else "line:" + line.strip()

    def find_previous_tool_line(self, ctx, event, line):
        if event.tool_call_id and (previous := ctx.tool.active_lines.get(event.tool_call_id, "")):
            return previous
        fingerprint = self.tool_line_fingerprint(line)
        return ctx.tool.active_fingerprints.get(fingerprint, "") if fingerprint else ""

    @staticmethod
    def tool_line_terminal_status(line: str) -> str:
        text = str(line or "").strip().lower()
        if text.startswith("❌") or " · failed" in text:
            return "failed"
        if text.startswith("✅") or " · done" in text:
            return "done"
        return ""

    @staticmethod
    def tool_line_fingerprint(line: str) -> str:
        text = line.strip()
        if "] " in text and text.startswith("["):
            text = text.split("] ", 1)[1]
        for prefix in ("✅ ", "❌ ", "🔎 ", "📖 ", "✍️ ", "🔧 ", "💻 ", "📋 ", "🧑‍💻 ", "🧰 "):
            if text.startswith(prefix):
                text = text[len(prefix) :]
        for suffix in (" · running", " · done", " · failed"):
            if suffix in text:
                text = text.split(suffix, 1)[0]
                break
        return text.strip()

    def append_assistant(self, ctx: SessionContext, event: AssistantEvent) -> int:
        text = str(event.text or "").strip()
        if not text:
            return 0
        previous = ctx.assistant.latest_text
        replace_latest = bool(previous and (text.startswith(previous) or previous.startswith(text)))
        if ctx.assistant.transient and not event.transient:
            ctx.assistant.lines.clear()
            previous = ""
            replace_latest = False
            ctx.assistant.transient = False
        if replace_latest and ctx.assistant.lines:
            ctx.assistant.lines[-1] = AssistantLine(text=text, created_at=event.created_at)
        else:
            ctx.assistant.lines.append(AssistantLine(text=text, created_at=event.created_at))
        max_lines = max(1, self.settings.assistant.max_lines)
        if ctx.assistant.lines.maxlen != max_lines:
            ctx.assistant.lines = type(ctx.assistant.lines)(
                list(ctx.assistant.lines)[-max_lines:], maxlen=max_lines
            )
        delta_chars = len(text) - len(previous) if replace_latest else len(text)
        ctx.assistant.pending_chars += max(1, delta_chars)
        ctx.assistant.latest_text = text
        ctx.assistant.last_chars = len(text)
        ctx.assistant.last_at = event.created_at
        ctx.assistant.transient = bool(event.transient)
        return ctx.assistant.pending_chars

    @staticmethod
    def clear_transient_assistant(ctx: SessionContext) -> None:
        if not ctx.assistant.transient:
            return
        ctx.assistant.lines.clear()
        ctx.assistant.latest_text = ""
        ctx.assistant.pending_chars = 0
        ctx.assistant.last_chars = 0
        ctx.assistant.last_at = 0.0
        ctx.assistant.transient = False

    def append_reasoning(self, ctx: SessionContext, event: ReasoningEvent) -> int:
        if not event.text:
            return 0
        event_text = str(event.text)
        merged = ctx.reasoning.text + event_text
        max_chars = self.settings.reasoning.max_chars
        buffer_limit = max(0, max_chars * 4)
        if len(merged) > buffer_limit:
            core, stream_suffix = split_reasoning_stream_suffix(
                merged, max_suffix_chars=max(0, buffer_limit - 1)
            )
            normalized = normalize_reasoning_text(core)
            trim_limit = buffer_limit - len(stream_suffix)
            trimmed = self.trim_reasoning_buffer(normalized, trim_limit) if trim_limit > 0 else ""
            merged = trimmed + stream_suffix
        ctx.reasoning.text = merged
        ctx.reasoning.pending_chars += len(event_text)
        ctx.reasoning.last_source = event.source or "structured_reasoning"
        ctx.reasoning.last_chars = len(event_text)
        ctx.reasoning.last_at = event.created_at
        return ctx.reasoning.pending_chars

    @staticmethod
    def trim_reasoning_buffer(text: str, max_chars: int) -> str:
        fenced_tail = trim_reasoning_fenced_tail(text, max_chars)
        if fenced_tail is not None:
            return fenced_tail
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
