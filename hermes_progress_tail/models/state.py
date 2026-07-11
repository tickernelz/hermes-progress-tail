from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .events import TodoItem, TodoStatus  # noqa: F401 - compatibility re-export
from .state_compat import SessionStateCompatibility
from .state_records import (
    AssistantLine,  # noqa: F401 - compatibility re-export
    AssistantState,
    BackgroundJob,  # noqa: F401 - compatibility re-export
    BackgroundState,
    DelegateBranch,  # noqa: F401 - compatibility re-export
    DelegateLine,  # noqa: F401 - compatibility re-export
    DelegateState,
    DeliveryState,
    DiagnosticsState,
    EnvironmentSnapshot,
    ReasoningState,
    ToolState,
)

_MISSING = object()


@dataclass(init=False)
class SessionContext(SessionStateCompatibility):
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
    delivery: DeliveryState = field(default_factory=DeliveryState)
    started_at: float = field(default_factory=time.monotonic)
    tool: ToolState = field(default_factory=ToolState)
    delegate: DelegateState = field(default_factory=DelegateState)
    background: BackgroundState = field(default_factory=BackgroundState)
    assistant: AssistantState = field(default_factory=AssistantState)
    reasoning: ReasoningState = field(default_factory=ReasoningState)
    diagnostics: DiagnosticsState = field(default_factory=DiagnosticsState)
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
        message_id=_MISSING,
        can_edit=_MISSING,
        disabled=_MISSING,
        progress_state=_MISSING,
        finalized_at=_MISSING,
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
        last_render_at=_MISSING,
        last_event_at=_MISSING,
        edit_state=_MISSING,
        edit_backoff_until=_MISSING,
        edit_failure_count=_MISSING,
        edit_recovery_sends=_MISSING,
        delayed_flush_task=_MISSING,
        delete_task=_MISSING,
        fallback_send_count=_MISSING,
        new_events_since_snapshot=_MISSING,
        snapshots_sent=_MISSING,
        total_events=_MISSING,
        last_error=_MISSING,
        downgrade_reason=_MISSING,
        downgrade_at=_MISSING,
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
        compaction_count=_MISSING,
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
        delivery_legacy = {
            "message_id": message_id,
            "can_edit": can_edit,
            "disabled": disabled,
            "progress_state": progress_state,
            "finalized_at": finalized_at,
            "last_render_at": last_render_at,
            "edit_state": edit_state,
            "edit_backoff_until": edit_backoff_until,
            "edit_failure_count": edit_failure_count,
            "edit_recovery_sends": edit_recovery_sends,
            "delayed_flush_task": delayed_flush_task,
            "delete_task": delete_task,
            "fallback_send_count": fallback_send_count,
            "snapshots_sent": snapshots_sent,
        }
        delivery_legacy = {k: v for k, v in delivery_legacy.items() if v is not _MISSING}
        if delivery is not None and delivery_legacy:
            raise TypeError("delivery cannot be combined with legacy delivery fields")
        self.delivery = delivery if delivery is not None else DeliveryState(**delivery_legacy)
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
        diagnostics_legacy = {
            "last_event_at": last_event_at,
            "new_events_since_snapshot": new_events_since_snapshot,
            "total_events": total_events,
            "last_error": last_error,
            "downgrade_reason": downgrade_reason,
            "downgrade_at": downgrade_at,
            "compaction_count": compaction_count,
        }
        diagnostics_legacy = {k: v for k, v in diagnostics_legacy.items() if v is not _MISSING}
        if diagnostics is not None and diagnostics_legacy:
            raise TypeError("diagnostics cannot be combined with legacy diagnostics fields")
        self.diagnostics = (
            diagnostics if diagnostics is not None else DiagnosticsState(**diagnostics_legacy)
        )
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
        if routing is not None:
            raise TypeError("routing owner is not available until a future migration")
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


from . import events as _events  # noqa: E402

ToolEvent = _events.ToolEvent
DelegateEvent = _events.DelegateEvent
ReasoningEvent = _events.ReasoningEvent
AssistantEvent = _events.AssistantEvent
BackgroundJobEvent = _events.BackgroundJobEvent
ProgressEvent = _events.ProgressEvent
