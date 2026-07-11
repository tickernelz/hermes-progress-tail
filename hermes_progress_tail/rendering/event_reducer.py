from __future__ import annotations

from dataclasses import dataclass

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
        self.schedule_delegate_cleanup = schedule_delegate_cleanup

    def replace_settings(self, settings: Settings) -> None:
        self.settings = settings

    def accepts(self, ctx: SessionContext, event: ProgressEvent) -> bool:
        if isinstance(event, ToolEvent):
            return bool(ctx.tools_enabled)
        if isinstance(event, AssistantEvent):
            return bool(ctx.assistant_enabled)
        if isinstance(event, ReasoningEvent):
            return bool(ctx.reasoning_enabled)
        if isinstance(event, DelegateEvent):
            return bool(ctx.delegates_enabled)
        if isinstance(event, BackgroundJobEvent):
            return bool(self.settings.background_jobs.enabled and ctx.background_jobs_enabled)
        return False

    def reduce(self, ctx: SessionContext, event: ProgressEvent) -> ReductionResult:
        if isinstance(event, AssistantEvent):
            return ReductionResult(pending_chars=self.append_assistant(ctx, event))
        if isinstance(event, ReasoningEvent):
            return ReductionResult(pending_chars=self.append_reasoning(ctx, event))
        return ReductionResult()

    def append_assistant(self, ctx: SessionContext, event: AssistantEvent) -> int:
        text = str(event.text or "").strip()
        if not text:
            return 0
        previous = ctx.assistant_latest_text
        replace_latest = bool(previous and (text.startswith(previous) or previous.startswith(text)))
        if ctx.assistant_transient and not event.transient:
            ctx.assistant_lines.clear()
            previous = ""
            replace_latest = False
            ctx.assistant_transient = False
        if replace_latest and ctx.assistant_lines:
            ctx.assistant_lines[-1] = AssistantLine(text=text, created_at=event.created_at)
        else:
            ctx.assistant_lines.append(AssistantLine(text=text, created_at=event.created_at))
        max_lines = max(1, self.settings.assistant.max_lines)
        if ctx.assistant_lines.maxlen != max_lines:
            ctx.assistant_lines = type(ctx.assistant_lines)(
                list(ctx.assistant_lines)[-max_lines:], maxlen=max_lines
            )
        delta_chars = len(text) - len(previous) if replace_latest else len(text)
        ctx.assistant_pending_chars += max(1, delta_chars)
        ctx.assistant_latest_text = text
        ctx.last_assistant_chars = len(text)
        ctx.last_assistant_at = event.created_at
        ctx.assistant_transient = bool(event.transient)
        return ctx.assistant_pending_chars

    @staticmethod
    def clear_transient_assistant(ctx: SessionContext) -> None:
        if not ctx.assistant_transient:
            return
        ctx.assistant_lines.clear()
        ctx.assistant_latest_text = ""
        ctx.assistant_pending_chars = 0
        ctx.last_assistant_chars = 0
        ctx.last_assistant_at = 0.0
        ctx.assistant_transient = False

    def append_reasoning(self, ctx: SessionContext, event: ReasoningEvent) -> int:
        if not event.text:
            return 0
        event_text = str(event.text)
        merged = ctx.reasoning_text + event_text
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
        ctx.reasoning_text = merged
        ctx.reasoning_pending_chars += len(event_text)
        ctx.last_reasoning_source = event.source or "structured_reasoning"
        ctx.last_reasoning_chars = len(event_text)
        ctx.last_reasoning_at = event.created_at
        return ctx.reasoning_pending_chars

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
