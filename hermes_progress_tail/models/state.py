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


_MISSING = object()


@dataclass(init=False)
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
    owner_thread_id: int = 0
    owner_thread_name: str = ""
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
    assistant: AssistantState = field(default_factory=AssistantState)
    reasoning: ReasoningState = field(default_factory=ReasoningState)
    last_render_at: float = 0.0
    last_event_at: float = field(default_factory=time.monotonic)
    edit_state: str = "editable"
    edit_backoff_until: float = 0.0
    edit_failure_count: int = 0
    edit_recovery_sends: int = 0
    delayed_flush_task: Any = None
    delete_task: Any = None
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
    agent_label: str = ""
    chat_type: str = ""
    source_message_id: str | None = None
    lock: Any = field(default_factory=asyncio.Lock)
    environment: EnvironmentSnapshot = field(default_factory=EnvironmentSnapshot)
    compaction_count: int = 0

    def __init__(
        self,
        session_id,
        session_key,
        platform,
        chat_id,
        thread_id,
        adapter,
        loop,
        strategy="auto",
        *,
        lines=3,
        preview_length=120,
        edit_interval=1.5,
        generation=0,
        owner_thread_id=0,
        owner_thread_name="",
        message_id=None,
        can_edit=True,
        disabled=False,
        progress_state="active",
        finalized_at=0.0,
        started_at=_MISSING,
        tool_lines=_MISSING,
        active_tool_lines=_MISSING,
        active_tool_fingerprints=_MISSING,
        tool_started_count=0,
        tool_completed_count=0,
        tool_failed_count=0,
        completed_tool_ids=_MISSING,
        delegate_branches=_MISSING,
        delegate_order=_MISSING,
        background_jobs=_MISSING,
        background_order=_MISSING,
        todo_items=(),
        todo_updated_at=0.0,
        last_render_at=0.0,
        last_event_at=_MISSING,
        edit_state="editable",
        edit_backoff_until=0.0,
        edit_failure_count=0,
        edit_recovery_sends=0,
        delayed_flush_task=None,
        delete_task=None,
        fallback_send_count=0,
        new_events_since_snapshot=0,
        snapshots_sent=0,
        total_events=0,
        last_error="",
        downgrade_reason="",
        downgrade_at=0.0,
        tools_enabled=True,
        assistant_enabled=True,
        reasoning_enabled=True,
        delegates_enabled=True,
        background_jobs_enabled=True,
        timestamp=None,
        timestamp_format="",
        agent_label="",
        chat_type="",
        source_message_id=None,
        lock=_MISSING,
        environment=_MISSING,
        compaction_count=0,
        assistant_lines=_MISSING,
        assistant_latest_text=_MISSING,
        assistant_pending_chars=_MISSING,
        last_assistant_chars=_MISSING,
        last_assistant_at=_MISSING,
        assistant_transient=_MISSING,
        reasoning_text=_MISSING,
        reasoning_pending_chars=_MISSING,
        last_reasoning_source=_MISSING,
        last_reasoning_chars=_MISSING,
        last_reasoning_at=_MISSING,
        routing=None,
        delivery=None,
        tool=None,
        delegate=None,
        background=None,
        assistant=None,
        reasoning=None,
        diagnostics=None,
    ):
        self.session_id = session_id
        self.session_key = session_key
        self.platform = platform
        self.chat_id = chat_id
        self.thread_id = thread_id
        self.adapter = adapter
        self.loop = loop
        self.strategy = strategy
        self.lines = lines
        self.preview_length = preview_length
        self.edit_interval = edit_interval
        self.generation = generation
        self.owner_thread_id = owner_thread_id
        self.owner_thread_name = owner_thread_name
        self.message_id = message_id
        self.can_edit = can_edit
        self.disabled = disabled
        self.progress_state = progress_state
        self.finalized_at = finalized_at
        self.started_at = time.monotonic() if started_at is _MISSING else started_at
        self.tool_lines = deque(maxlen=3) if tool_lines is _MISSING else tool_lines
        self.active_tool_lines = {} if active_tool_lines is _MISSING else active_tool_lines
        self.active_tool_fingerprints = (
            {} if active_tool_fingerprints is _MISSING else active_tool_fingerprints
        )
        self.tool_started_count = tool_started_count
        self.tool_completed_count = tool_completed_count
        self.tool_failed_count = tool_failed_count
        self.completed_tool_ids = set() if completed_tool_ids is _MISSING else completed_tool_ids
        self.delegate_branches = {} if delegate_branches is _MISSING else delegate_branches
        self.delegate_order = deque() if delegate_order is _MISSING else delegate_order
        self.background_jobs = {} if background_jobs is _MISSING else background_jobs
        self.background_order = deque() if background_order is _MISSING else background_order
        self.todo_items = todo_items
        self.todo_updated_at = todo_updated_at
        self.last_render_at = last_render_at
        self.last_event_at = time.monotonic() if last_event_at is _MISSING else last_event_at
        self.edit_state = edit_state
        self.edit_backoff_until = edit_backoff_until
        self.edit_failure_count = edit_failure_count
        self.edit_recovery_sends = edit_recovery_sends
        self.delayed_flush_task = delayed_flush_task
        self.delete_task = delete_task
        self.fallback_send_count = fallback_send_count
        self.new_events_since_snapshot = new_events_since_snapshot
        self.snapshots_sent = snapshots_sent
        self.total_events = total_events
        self.last_error = last_error
        self.downgrade_reason = downgrade_reason
        self.downgrade_at = downgrade_at
        self.tools_enabled = tools_enabled
        self.assistant_enabled = assistant_enabled
        self.reasoning_enabled = reasoning_enabled
        self.delegates_enabled = delegates_enabled
        self.background_jobs_enabled = background_jobs_enabled
        self.timestamp = timestamp
        self.timestamp_format = timestamp_format
        self.agent_label = agent_label
        self.chat_type = chat_type
        self.source_message_id = source_message_id
        self.lock = asyncio.Lock() if lock is _MISSING else lock
        self.environment = EnvironmentSnapshot() if environment is _MISSING else environment
        self.compaction_count = compaction_count
        for owner_name, owner_value in (
            ("routing", routing),
            ("delivery", delivery),
            ("tool", tool),
            ("delegate", delegate),
            ("background", background),
            ("diagnostics", diagnostics),
        ):
            if owner_value is not None:
                raise TypeError(f"{owner_name} owner is not available until a future migration")
        assistant_legacy = {
            "lines": assistant_lines,
            "latest_text": assistant_latest_text,
            "pending_chars": assistant_pending_chars,
            "last_chars": last_assistant_chars,
            "last_at": last_assistant_at,
            "transient": assistant_transient,
        }
        assistant_legacy = {k: v for k, v in assistant_legacy.items() if v is not _MISSING}
        if assistant is not None and assistant_legacy:
            raise TypeError("assistant cannot be combined with legacy assistant fields")
        self.assistant = assistant if assistant is not None else AssistantState(**assistant_legacy)
        reasoning_legacy = {
            "text": reasoning_text,
            "pending_chars": reasoning_pending_chars,
            "last_source": last_reasoning_source,
            "last_chars": last_reasoning_chars,
            "last_at": last_reasoning_at,
        }
        reasoning_legacy = {k: v for k, v in reasoning_legacy.items() if v is not _MISSING}
        if reasoning is not None and reasoning_legacy:
            raise TypeError("reasoning cannot be combined with legacy reasoning fields")
        self.reasoning = reasoning if reasoning is not None else ReasoningState(**reasoning_legacy)

    @property
    def assistant_lines(self):
        return self.assistant.lines

    @assistant_lines.setter
    def assistant_lines(self, value):
        self.assistant.lines = value

    @property
    def assistant_latest_text(self):
        return self.assistant.latest_text

    @assistant_latest_text.setter
    def assistant_latest_text(self, value):
        self.assistant.latest_text = value

    @property
    def assistant_pending_chars(self):
        return self.assistant.pending_chars

    @assistant_pending_chars.setter
    def assistant_pending_chars(self, value):
        self.assistant.pending_chars = value

    @property
    def last_assistant_chars(self):
        return self.assistant.last_chars

    @last_assistant_chars.setter
    def last_assistant_chars(self, value):
        self.assistant.last_chars = value

    @property
    def last_assistant_at(self):
        return self.assistant.last_at

    @last_assistant_at.setter
    def last_assistant_at(self, value):
        self.assistant.last_at = value

    @property
    def assistant_transient(self):
        return self.assistant.transient

    @assistant_transient.setter
    def assistant_transient(self, value):
        self.assistant.transient = value

    @property
    def reasoning_text(self):
        return self.reasoning.text

    @reasoning_text.setter
    def reasoning_text(self, value):
        self.reasoning.text = value

    @property
    def reasoning_pending_chars(self):
        return self.reasoning.pending_chars

    @reasoning_pending_chars.setter
    def reasoning_pending_chars(self, value):
        self.reasoning.pending_chars = value

    @property
    def last_reasoning_source(self):
        return self.reasoning.last_source

    @last_reasoning_source.setter
    def last_reasoning_source(self, value):
        self.reasoning.last_source = value

    @property
    def last_reasoning_chars(self):
        return self.reasoning.last_chars

    @last_reasoning_chars.setter
    def last_reasoning_chars(self, value):
        self.reasoning.last_chars = value

    @property
    def last_reasoning_at(self):
        return self.reasoning.last_at

    @last_reasoning_at.setter
    def last_reasoning_at(self, value):
        self.reasoning.last_at = value

    @property
    def line_buffer(self) -> deque[str]:
        return self.tool_lines

    @line_buffer.setter
    def line_buffer(self, value: deque[str]) -> None:
        self.tool_lines = value

    @property
    def metadata(self) -> dict[str, str | bool] | None:
        if not self.thread_id:
            return None
        metadata: dict[str, str | bool] = {"thread_id": self.thread_id}
        if self.platform == "telegram" and self.chat_type == "dm":
            metadata["telegram_dm_topic_reply_fallback"] = True
            if self.thread_id not in {"", "1"}:
                metadata["direct_messages_topic_id"] = self.thread_id
            if self.source_message_id:
                metadata["telegram_reply_to_message_id"] = self.source_message_id
        return metadata

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
