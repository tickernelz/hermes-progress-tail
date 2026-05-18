from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]


@dataclass(frozen=True)
class TodoItem:
    content: str
    status: str


@dataclass
class DelegateLine:
    kind: str
    text: str
    details: tuple[str, ...] = ()
    tool_name: str = ""


@dataclass
class BackgroundJob:
    process_id: str
    command: str = ""
    cwd: str = ""
    pid: int | None = None
    status: str = "running"
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    exit_code: int | None = None
    output_head: tuple[str, ...] = ()
    output_tail: tuple[str, ...] = ()
    output_chars: int = 0
    last_output: str = ""
    poll_task: Any = None


@dataclass
class DelegateBranch:
    subagent_id: str
    task_index: int = 0
    task_count: int = 1
    goal: str = ""
    status: str = "pending"
    model: str = ""
    tool_count: int = 0
    started_at: float = 0.0
    updated_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    duration_seconds: float = 0.0
    lines: deque[DelegateLine] = field(default_factory=lambda: deque(maxlen=2))
    completion_line: str = ""
    lifecycle_started: bool = False

    def resize(self, lines_per_delegate: int) -> None:
        if self.lines.maxlen == lines_per_delegate:
            return
        self.lines = deque(list(self.lines)[-lines_per_delegate:], maxlen=lines_per_delegate)


@dataclass(frozen=True)
class AssistantLine:
    text: str
    created_at: float = field(default_factory=time.time)


@dataclass
class SessionContext:
    session_id: str
    session_key: str
    platform: str
    chat_id: str
    thread_id: str | None
    adapter: Any
    loop: Any
    strategy: str = "auto"
    lines: int = 3
    preview_length: int = 120
    edit_interval: float = 1.5
    generation: int = 0
    message_id: str | None = None
    can_edit: bool = True
    disabled: bool = False
    progress_state: str = "active"
    finalized_at: float = 0.0
    started_at: float = field(default_factory=time.monotonic)
    tool_lines: deque[str] = field(default_factory=lambda: deque(maxlen=3))
    active_tool_lines: dict[str, str] = field(default_factory=dict)
    active_tool_fingerprints: dict[str, str] = field(default_factory=dict)
    tool_started_count: int = 0
    tool_completed_count: int = 0
    tool_failed_count: int = 0
    completed_tool_ids: set[str] = field(default_factory=set)
    delegate_branches: dict[str, DelegateBranch] = field(default_factory=dict)
    delegate_order: deque[str] = field(default_factory=deque)
    background_jobs: dict[str, BackgroundJob] = field(default_factory=dict)
    background_order: deque[str] = field(default_factory=deque)
    todo_items: tuple[TodoItem, ...] = ()
    todo_updated_at: float = 0.0
    assistant_lines: deque[AssistantLine] = field(default_factory=lambda: deque(maxlen=3))
    assistant_latest_text: str = ""
    assistant_pending_chars: int = 0
    last_assistant_chars: int = 0
    last_assistant_at: float = 0.0
    assistant_transient: bool = False
    reasoning_text: str = ""
    reasoning_pending_chars: int = 0
    last_reasoning_source: str = ""
    last_reasoning_chars: int = 0
    last_reasoning_at: float = 0.0
    last_render_at: float = 0.0
    last_event_at: float = field(default_factory=time.monotonic)
    edit_state: str = "editable"
    edit_backoff_until: float = 0.0
    edit_failure_count: int = 0
    edit_recovery_sends: int = 0
    delayed_flush_task: Any = None
    fallback_send_count: int = 0
    new_events_since_snapshot: int = 0
    snapshots_sent: int = 0
    total_events: int = 0
    last_error: str = ""
    downgrade_reason: str = ""
    downgrade_at: float = 0.0
    tools_enabled: bool = True
    assistant_enabled: bool = True
    reasoning_enabled: bool = True
    delegates_enabled: bool = True
    background_jobs_enabled: bool = True
    timestamp: bool | None = None
    timestamp_format: str = ""
    code_fence: str = "off"
    agent_label: str = ""
    lock: Any = field(default_factory=asyncio.Lock)

    @property
    def line_buffer(self) -> deque[str]:
        return self.tool_lines

    @line_buffer.setter
    def line_buffer(self, value: deque[str]) -> None:
        self.tool_lines = value

    @property
    def metadata(self) -> dict[str, str] | None:
        return {"thread_id": self.thread_id} if self.thread_id else None

    def resize(self, lines: int) -> None:
        if self.tool_lines.maxlen == lines:
            return
        self.tool_lines = deque(list(self.tool_lines)[-lines:], maxlen=lines)
        self.lines = lines


@dataclass(frozen=True)
class ToolEvent:
    session_id: str
    session_key: str
    platform: str
    line: str
    tool_call_id: str = ""
    tool_name: str = ""
    replace_existing: bool = False
    todo_items: tuple[TodoItem, ...] = ()
    created_at: float = field(default_factory=time.time)
    kind: Literal["tool"] = "tool"


@dataclass(frozen=True)
class DelegateEvent:
    session_id: str
    session_key: str
    platform: str
    subagent_id: str
    task_index: int = 0
    task_count: int = 1
    goal: str = ""
    event_type: str = "subagent.tool"
    tool_name: str = ""
    preview: str = ""
    args: dict[str, Any] | None = None
    status: str = ""
    model: str = ""
    tool_count: int = 0
    duration_seconds: float = 0.0
    summary: str = ""
    created_at: float = field(default_factory=time.time)
    kind: Literal["delegate"] = "delegate"


@dataclass(frozen=True)
class ReasoningEvent:
    session_id: str
    session_key: str
    platform: str
    text: str
    source: str = "structured_reasoning"
    created_at: float = field(default_factory=time.time)
    kind: Literal["reasoning"] = "reasoning"


@dataclass(frozen=True)
class AssistantEvent:
    session_id: str
    session_key: str
    platform: str
    text: str
    already_streamed: bool = False
    transient: bool = False
    created_at: float = field(default_factory=time.time)
    kind: Literal["assistant"] = "assistant"


@dataclass(frozen=True)
class BackgroundJobEvent:
    session_id: str
    session_key: str
    platform: str
    process_id: str
    event_type: str = "started"
    command: str = ""
    cwd: str = ""
    pid: int | None = None
    output: str = ""
    exited: bool = False
    exit_code: int | None = None
    created_at: float = field(default_factory=time.time)
    kind: Literal["background_job"] = "background_job"


ProgressEvent = ToolEvent | DelegateEvent | ReasoningEvent | AssistantEvent | BackgroundJobEvent
