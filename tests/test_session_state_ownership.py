import ast
import inspect
from collections import deque
from dataclasses import MISSING, fields
from pathlib import Path

import pytest

from hermes_progress_tail.models import state

MIGRATED = {
    "assistant_lines": "lines",
    "assistant_latest_text": "latest_text",
    "assistant_pending_chars": "pending_chars",
    "last_assistant_chars": "last_chars",
    "last_assistant_at": "last_at",
    "assistant_transient": "transient",
    "reasoning_text": "text",
    "reasoning_pending_chars": "pending_chars",
    "last_reasoning_source": "last_source",
    "last_reasoning_chars": "last_chars",
    "last_reasoning_at": "last_at",
}
FIELD_NAMES = [
    "session_id",
    "session_key",
    "platform",
    "chat_id",
    "thread_id",
    "adapter",
    "loop",
    "strategy",
    "lines",
    "preview_length",
    "edit_interval",
    "generation",
    "owner_thread_id",
    "owner_thread_name",
    "message_id",
    "can_edit",
    "disabled",
    "progress_state",
    "finalized_at",
    "started_at",
    "tool_lines",
    "active_tool_lines",
    "active_tool_fingerprints",
    "tool_started_count",
    "tool_completed_count",
    "tool_failed_count",
    "completed_tool_ids",
    "delegate_branches",
    "delegate_order",
    "background_jobs",
    "background_order",
    "todo_items",
    "todo_updated_at",
    "assistant",
    "reasoning",
    "last_render_at",
    "last_event_at",
    "edit_state",
    "edit_backoff_until",
    "edit_failure_count",
    "edit_recovery_sends",
    "delayed_flush_task",
    "delete_task",
    "fallback_send_count",
    "new_events_since_snapshot",
    "snapshots_sent",
    "total_events",
    "last_error",
    "downgrade_reason",
    "downgrade_at",
    "tools_enabled",
    "assistant_enabled",
    "reasoning_enabled",
    "delegates_enabled",
    "background_jobs_enabled",
    "timestamp",
    "timestamp_format",
    "agent_label",
    "chat_type",
    "source_message_id",
    "lock",
    "environment",
    "compaction_count",
]
LEGACY = [
    "lines",
    "preview_length",
    "edit_interval",
    "generation",
    "owner_thread_id",
    "owner_thread_name",
    "message_id",
    "can_edit",
    "disabled",
    "progress_state",
    "finalized_at",
    "started_at",
    "tool_lines",
    "active_tool_lines",
    "active_tool_fingerprints",
    "tool_started_count",
    "tool_completed_count",
    "tool_failed_count",
    "completed_tool_ids",
    "delegate_branches",
    "delegate_order",
    "background_jobs",
    "background_order",
    "todo_items",
    "todo_updated_at",
    "last_render_at",
    "last_event_at",
    "edit_state",
    "edit_backoff_until",
    "edit_failure_count",
    "edit_recovery_sends",
    "delayed_flush_task",
    "delete_task",
    "fallback_send_count",
    "new_events_since_snapshot",
    "snapshots_sent",
    "total_events",
    "last_error",
    "downgrade_reason",
    "downgrade_at",
    "tools_enabled",
    "assistant_enabled",
    "reasoning_enabled",
    "delegates_enabled",
    "background_jobs_enabled",
    "timestamp",
    "timestamp_format",
    "agent_label",
    "chat_type",
    "source_message_id",
    "lock",
    "environment",
    "compaction_count",
    "assistant_lines",
    "assistant_latest_text",
    "assistant_pending_chars",
    "last_assistant_chars",
    "last_assistant_at",
    "assistant_transient",
    "reasoning_text",
    "reasoning_pending_chars",
    "last_reasoning_source",
    "last_reasoning_chars",
    "last_reasoning_at",
    "routing",
    "delivery",
    "tool",
    "delegate",
    "background",
    "assistant",
    "reasoning",
    "diagnostics",
]


def prerequisites():
    assert hasattr(state, "AssistantState"), "AssistantState owner is missing"
    assert hasattr(state, "ReasoningState"), "ReasoningState owner is missing"
    assert hasattr(state, "SessionContext"), "SessionContext is missing"
    return state.SessionContext


def make_context(**kwargs):
    cls = prerequisites()
    return cls("s", "k", "discord", "c", None, None, None, "auto", **kwargs)


def test_exact_canonical_fields_annotations_and_factories():
    cls = prerequisites()
    assert [f.name for f in fields(cls)] == FIELD_NAMES
    expected = {f.name: f.type for f in fields(cls)}
    assert cls.__annotations__ == expected
    by_name = {f.name: f for f in fields(cls)}
    assert by_name["assistant"].default_factory is state.AssistantState
    assert by_name["reasoning"].default_factory is state.ReasoningState
    assert by_name["assistant"].default is MISSING
    assert by_name["reasoning"].default is MISSING


def test_exact_explicit_constructor_parameter_order_and_kinds():
    cls = prerequisites()
    params = list(inspect.signature(cls).parameters.values())
    positional = [
        "session_id",
        "session_key",
        "platform",
        "chat_id",
        "thread_id",
        "adapter",
        "loop",
        "strategy",
    ]
    assert [p.name for p in params] == positional + LEGACY
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in params[:8])
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params[8:])


def test_all_compatibility_properties_delegate_without_storage():
    cls = prerequisites()
    ctx = make_context()
    assert all(isinstance(getattr(cls, name, None), property) for name in MIGRATED)
    for index, (flat, nested) in enumerate(MIGRATED.items(), 1):
        owner = (
            ctx.assistant
            if flat.startswith("assistant") or flat.startswith("last_assistant")
            else ctx.reasoning
        )
        value = deque([state.AssistantLine("x")], maxlen=5) if flat == "assistant_lines" else index
        setattr(ctx, flat, value)
        assert getattr(ctx, flat) is value
        assert getattr(owner, nested) is value
    assert set(ctx.__dict__).isdisjoint(MIGRATED)


def test_owner_defaults_types_identity_and_conflicts():
    first, second = make_context(), make_context()
    assert isinstance(first.assistant, state.AssistantState)
    assert isinstance(first.reasoning, state.ReasoningState)
    assert (
        first.assistant is not second.assistant
        and first.assistant.lines is not second.assistant.lines
    )
    assert first.reasoning is not second.reasoning
    with pytest.raises(TypeError, match="assistant cannot be combined"):
        make_context(assistant=state.AssistantState(), assistant_latest_text="x")
    with pytest.raises(TypeError, match="reasoning cannot be combined"):
        make_context(reasoning=state.ReasoningState(), reasoning_text="x")


def test_explicit_owner_construction_preserves_identity():
    prerequisites()
    assistant = state.AssistantState(latest_text="owned")
    reasoning = state.ReasoningState(text="owned")
    first = state.SessionContext("s", "k", "discord", "c", None, None, None, assistant=assistant)
    second = state.SessionContext("s", "k", "discord", "c", None, None, None, reasoning=reasoning)
    assert first.assistant is assistant
    assert second.reasoning is reasoning


def test_production_uses_nested_owner_access():
    prerequisites()
    root = Path(__file__).parents[1] / "hermes_progress_tail" / "rendering"
    offenders = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in MIGRATED:
                offenders.append(f"{path.name}:{node.lineno}:{node.attr}")
    assert offenders == [], (
        "flat production access outside runtime compatibility boundaries: " + ", ".join(offenders)
    )
