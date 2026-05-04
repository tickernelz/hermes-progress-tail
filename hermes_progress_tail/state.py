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
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=2))

    def resize(self, lines_per_delegate: int) -> None:
        if self.lines.maxlen == lines_per_delegate:
            return
        self.lines = deque(list(self.lines)[-lines_per_delegate:], maxlen=lines_per_delegate)


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
    tool_lines: deque[str] = field(default_factory=lambda: deque(maxlen=3))
    active_tool_lines: dict[str, str] = field(default_factory=dict)
    delegate_branches: dict[str, DelegateBranch] = field(default_factory=dict)
    delegate_order: deque[str] = field(default_factory=deque)
    todo_items: tuple[TodoItem, ...] = ()
    todo_updated_at: float = 0.0
    reasoning_text: str = ""
    reasoning_pending_chars: int = 0
    last_render_at: float = 0.0
    last_event_at: float = field(default_factory=time.monotonic)
    new_events_since_snapshot: int = 0
    snapshots_sent: int = 0
    total_events: int = 0
    last_error: str = ""
    downgrade_reason: str = ""
    downgrade_at: float = 0.0
    tools_enabled: bool = True
    reasoning_enabled: bool = True
    delegates_enabled: bool = True
    timestamp: bool | None = None
    timestamp_format: str = ""
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
    created_at: float = field(default_factory=time.time)
    kind: Literal["reasoning"] = "reasoning"


ProgressEvent = ToolEvent | DelegateEvent | ReasoningEvent
