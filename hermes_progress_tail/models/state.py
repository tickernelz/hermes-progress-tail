from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .events import TodoItem, TodoStatus  # noqa: F401 - compatibility re-export
from .state_records import (
    AssistantLine,  # noqa: F401 - compatibility re-export
    AssistantState,
    BackgroundJob,  # noqa: F401 - compatibility re-export
    BackgroundState,
    DelegateBranch,  # noqa: F401 - compatibility re-export
    DelegateLine,  # noqa: F401 - compatibility re-export
    DelegateState,
    EnvironmentSnapshot,
    ReasoningState,
    ToolState,
)

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
    tool: ToolState = field(default_factory=ToolState)
    delegate: DelegateState = field(default_factory=DelegateState)
    background: BackgroundState = field(default_factory=BackgroundState)
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
        tool_started_count=_MISSING,
        tool_completed_count=_MISSING,
        tool_failed_count=_MISSING,
        completed_tool_ids=_MISSING,
        delegate_branches=_MISSING,
        delegate_order=_MISSING,
        background_jobs=_MISSING,
        background_order=_MISSING,
        todo_items=_MISSING,
        todo_updated_at=_MISSING,
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
        tool_legacy = {
            "lines": tool_lines,
            "active_lines": active_tool_lines,
            "active_fingerprints": active_tool_fingerprints,
            "started_count": tool_started_count,
            "completed_count": tool_completed_count,
            "failed_count": tool_failed_count,
            "completed_ids": completed_tool_ids,
            "todo_items": todo_items,
            "todo_updated_at": todo_updated_at,
        }
        tool_legacy = {k: v for k, v in tool_legacy.items() if v is not _MISSING}
        if tool is not None and tool_legacy:
            raise TypeError("tool cannot be combined with legacy tool fields")
        self.tool = tool if tool is not None else ToolState(**tool_legacy)
        delegate_legacy = delegate_branches is not _MISSING or delegate_order is not _MISSING
        if delegate is not None and delegate_legacy:
            raise TypeError("delegate cannot be combined with legacy delegate fields")
        self.delegate = (
            delegate
            if delegate is not None
            else DelegateState(
                branches={} if delegate_branches is _MISSING else delegate_branches,
                order=deque() if delegate_order is _MISSING else delegate_order,
            )
        )
        background_legacy = background_jobs is not _MISSING or background_order is not _MISSING
        if background is not None and background_legacy:
            raise TypeError("background cannot be combined with legacy background fields")
        self.background = (
            background
            if background is not None
            else BackgroundState(
                jobs={} if background_jobs is _MISSING else background_jobs,
                order=deque() if background_order is _MISSING else background_order,
            )
        )
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
    def tool_lines(self):
        return self.tool.lines

    @tool_lines.setter
    def tool_lines(self, value):
        self.tool.lines = value

    @property
    def active_tool_lines(self):
        return self.tool.active_lines

    @active_tool_lines.setter
    def active_tool_lines(self, value):
        self.tool.active_lines = value

    @property
    def active_tool_fingerprints(self):
        return self.tool.active_fingerprints

    @active_tool_fingerprints.setter
    def active_tool_fingerprints(self, value):
        self.tool.active_fingerprints = value

    @property
    def tool_started_count(self):
        return self.tool.started_count

    @tool_started_count.setter
    def tool_started_count(self, value):
        self.tool.started_count = value

    @property
    def tool_completed_count(self):
        return self.tool.completed_count

    @tool_completed_count.setter
    def tool_completed_count(self, value):
        self.tool.completed_count = value

    @property
    def tool_failed_count(self):
        return self.tool.failed_count

    @tool_failed_count.setter
    def tool_failed_count(self, value):
        self.tool.failed_count = value

    @property
    def completed_tool_ids(self):
        return self.tool.completed_ids

    @completed_tool_ids.setter
    def completed_tool_ids(self, value):
        self.tool.completed_ids = value

    @property
    def todo_items(self):
        return self.tool.todo_items

    @todo_items.setter
    def todo_items(self, value):
        self.tool.todo_items = value

    @property
    def todo_updated_at(self):
        return self.tool.todo_updated_at

    @todo_updated_at.setter
    def todo_updated_at(self, value):
        self.tool.todo_updated_at = value

    @property
    def delegate_branches(self):
        return self.delegate.branches

    @delegate_branches.setter
    def delegate_branches(self, value):
        self.delegate.branches = value

    @property
    def delegate_order(self):
        return self.delegate.order

    @delegate_order.setter
    def delegate_order(self, value):
        self.delegate.order = value

    @property
    def background_jobs(self):
        return self.background.jobs

    @background_jobs.setter
    def background_jobs(self, value):
        self.background.jobs = value

    @property
    def background_order(self):
        return self.background.order

    @background_order.setter
    def background_order(self, value):
        self.background.order = value

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


from . import events as _events  # noqa: E402

ToolEvent = _events.ToolEvent
DelegateEvent = _events.DelegateEvent
ReasoningEvent = _events.ReasoningEvent
AssistantEvent = _events.AssistantEvent
BackgroundJobEvent = _events.BackgroundJobEvent
ProgressEvent = _events.ProgressEvent
