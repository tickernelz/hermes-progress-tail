from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal


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
    reasoning_text: str = ""
    reasoning_pending_chars: int = 0
    last_render_at: float = 0.0
    last_event_at: float = field(default_factory=time.monotonic)
    new_events_since_snapshot: int = 0
    snapshots_sent: int = 0
    total_events: int = 0
    tools_enabled: bool = True
    reasoning_enabled: bool = True
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
    created_at: float = field(default_factory=time.monotonic)
    kind: Literal["tool"] = "tool"


@dataclass(frozen=True)
class ReasoningEvent:
    session_id: str
    session_key: str
    platform: str
    text: str
    created_at: float = field(default_factory=time.monotonic)
    kind: Literal["reasoning"] = "reasoning"


ProgressEvent = ToolEvent | ReasoningEvent
