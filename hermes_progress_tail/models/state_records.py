from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .events import TodoItem


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
    completion_summary: str = ""
    cleanup_task: Any = None
    lifecycle_started: bool = False
    thinking_text: str = ""
    response_text: str = ""

    def resize(self, lines_per_delegate: int) -> None:
        if self.lines.maxlen == lines_per_delegate:
            return
        self.lines = deque(list(self.lines)[-lines_per_delegate:], maxlen=lines_per_delegate)


@dataclass(frozen=True)
class AssistantLine:
    text: str
    created_at: float = field(default_factory=time.time)


@dataclass
class EnvironmentSnapshot:
    context_tokens: int = 0
    context_window: int = 0
    context_kind: str = ""
    model: str = ""
    provider: str = ""
    profile: str = ""
    cwd: str = ""
    git_branch: str = ""
    git_dirty: bool = False
    git_ahead: int = 0
    git_behind: int = 0
    worktree: str = ""
    strategy: str = ""
    reasoning_effort: str = ""


@dataclass
class AssistantState:
    lines: deque[AssistantLine] = field(default_factory=lambda: deque(maxlen=3))
    latest_text: str = ""
    pending_chars: int = 0
    last_chars: int = 0
    last_at: float = 0.0
    transient: bool = False


@dataclass
class ReasoningState:
    text: str = ""
    pending_chars: int = 0
    last_source: str = ""
    last_chars: int = 0
    last_at: float = 0.0


@dataclass
class DelegateState:
    branches: dict[str, DelegateBranch] = field(default_factory=dict)
    order: deque[str] = field(default_factory=deque)


@dataclass
class BackgroundState:
    jobs: dict[str, BackgroundJob] = field(default_factory=dict)
    order: deque[str] = field(default_factory=deque)


@dataclass
class ToolState:
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=3))
    active_lines: dict[str, str] = field(default_factory=dict)
    active_fingerprints: dict[str, str] = field(default_factory=dict)
    started_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    completed_ids: set[str] = field(default_factory=set)
    todo_items: tuple[TodoItem, ...] = ()
    todo_updated_at: float = 0.0


@dataclass
class DeliveryState:
    message_id: str | None = None
    can_edit: bool = True
    disabled: bool = False
    progress_state: str = "active"
    finalized_at: float = 0.0
    last_render_at: float = 0.0
    edit_state: str = "editable"
    edit_backoff_until: float = 0.0
    edit_failure_count: int = 0
    edit_recovery_sends: int = 0
    delayed_flush_task: Any = None
    delete_task: Any = None
    fallback_send_count: int = 0
    snapshots_sent: int = 0


@dataclass
class DiagnosticsState:
    last_event_at: float = field(default_factory=time.monotonic)
    new_events_since_snapshot: int = 0
    total_events: int = 0
    last_error: str = ""
    downgrade_reason: str = ""
    downgrade_at: float = 0.0
    compaction_count: int = 0
