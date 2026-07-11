from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal

TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]


@dataclass(frozen=True)
class TodoItem:
    content: str
    status: str


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
