import ast
import inspect
import subprocess
import sys
from collections import deque
from dataclasses import MISSING, fields
from pathlib import Path

import pytest

from hermes_progress_tail.models import state

MIGRATED = {
    "message_id": "message_id",
    "can_edit": "can_edit",
    "disabled": "disabled",
    "progress_state": "progress_state",
    "finalized_at": "finalized_at",
    "last_render_at": "last_render_at",
    "edit_state": "edit_state",
    "edit_backoff_until": "edit_backoff_until",
    "edit_failure_count": "edit_failure_count",
    "edit_recovery_sends": "edit_recovery_sends",
    "delayed_flush_task": "delayed_flush_task",
    "delete_task": "delete_task",
    "fallback_send_count": "fallback_send_count",
    "snapshots_sent": "snapshots_sent",
    "last_event_at": "last_event_at",
    "new_events_since_snapshot": "new_events_since_snapshot",
    "total_events": "total_events",
    "last_error": "last_error",
    "downgrade_reason": "downgrade_reason",
    "downgrade_at": "downgrade_at",
    "compaction_count": "compaction_count",
    "tool_lines": "lines",
    "active_tool_lines": "active_lines",
    "active_tool_fingerprints": "active_fingerprints",
    "tool_started_count": "started_count",
    "tool_completed_count": "completed_count",
    "tool_failed_count": "failed_count",
    "completed_tool_ids": "completed_ids",
    "todo_items": "todo_items",
    "todo_updated_at": "todo_updated_at",
    "delegate_branches": "branches",
    "delegate_order": "order",
    "background_jobs": "jobs",
    "background_order": "order",
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
    "delivery",
    "started_at",
    "tool",
    "delegate",
    "background",
    "assistant",
    "reasoning",
    "diagnostics",
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
    assert hasattr(state, "DeliveryState"), "DeliveryState owner is missing"
    assert hasattr(state, "DiagnosticsState"), "DiagnosticsState owner is missing"
    assert hasattr(state, "ToolState"), "ToolState owner is missing"
    assert hasattr(state, "AssistantState"), "AssistantState owner is missing"
    assert hasattr(state, "ReasoningState"), "ReasoningState owner is missing"
    assert hasattr(state, "DelegateState")
    assert hasattr(state, "BackgroundState")
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
    assert by_name["delivery"].default_factory is state.DeliveryState
    assert by_name["diagnostics"].default_factory is state.DiagnosticsState
    assert by_name["tool"].default_factory is state.ToolState
    assert by_name["assistant"].default_factory is state.AssistantState
    assert by_name["reasoning"].default_factory is state.ReasoningState
    assert by_name["delegate"].default_factory is state.DelegateState
    assert by_name["background"].default_factory is state.BackgroundState
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
        if flat in {
            "message_id",
            "can_edit",
            "disabled",
            "progress_state",
            "finalized_at",
            "last_render_at",
            "edit_state",
            "edit_backoff_until",
            "edit_failure_count",
            "edit_recovery_sends",
            "delayed_flush_task",
            "delete_task",
            "fallback_send_count",
            "snapshots_sent",
        }:
            owner = ctx.delivery
        elif flat in {
            "last_event_at",
            "new_events_since_snapshot",
            "total_events",
            "last_error",
            "downgrade_reason",
            "downgrade_at",
            "compaction_count",
        }:
            owner = ctx.diagnostics
        elif flat in {
            "tool_lines",
            "active_tool_lines",
            "active_tool_fingerprints",
            "tool_started_count",
            "tool_completed_count",
            "tool_failed_count",
            "completed_tool_ids",
            "todo_items",
            "todo_updated_at",
        }:
            owner = ctx.tool
        elif flat.startswith("delegate"):
            owner = ctx.delegate
        elif flat.startswith("background"):
            owner = ctx.background
        elif flat.startswith("assistant") or flat.startswith("last_assistant"):
            owner = ctx.assistant
        else:
            owner = ctx.reasoning
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
    assert isinstance(first.delivery, state.DeliveryState)
    assert isinstance(first.diagnostics, state.DiagnosticsState)
    assert first.delivery is not second.delivery
    assert first.diagnostics is not second.diagnostics
    assert isinstance(first.tool, state.ToolState)
    assert first.tool is not second.tool
    assert first.tool.lines is not second.tool.lines
    assert first.tool.active_lines is not second.tool.active_lines
    assert first.tool.active_fingerprints is not second.tool.active_fingerprints
    assert first.tool.completed_ids is not second.tool.completed_ids
    assert isinstance(first.delegate, state.DelegateState)
    assert isinstance(first.background, state.BackgroundState)
    assert first.delegate is not second.delegate
    assert first.background is not second.background
    assert first.delegate.branches is not second.delegate.branches
    assert first.delegate.order is not second.delegate.order
    assert first.background.jobs is not second.background.jobs
    assert first.background.order is not second.background.order
    with pytest.raises(TypeError, match="assistant cannot be combined"):
        make_context(assistant=state.AssistantState(), assistant_latest_text="x")
    with pytest.raises(TypeError, match="reasoning cannot be combined"):
        make_context(reasoning=state.ReasoningState(), reasoning_text="x")
    conflicts = (
        ("delivery", "message_id", None),
        ("delivery", "can_edit", True),
        ("delivery", "disabled", False),
        ("delivery", "progress_state", "active"),
        ("delivery", "finalized_at", 0.0),
        ("delivery", "last_render_at", 0.0),
        ("delivery", "edit_state", "editable"),
        ("delivery", "edit_backoff_until", 0.0),
        ("delivery", "edit_failure_count", 0),
        ("delivery", "edit_recovery_sends", 0),
        ("delivery", "delayed_flush_task", None),
        ("delivery", "delete_task", None),
        ("delivery", "fallback_send_count", 0),
        ("delivery", "snapshots_sent", 0),
        ("diagnostics", "last_event_at", 0.0),
        ("diagnostics", "new_events_since_snapshot", 0),
        ("diagnostics", "total_events", 0),
        ("diagnostics", "last_error", ""),
        ("diagnostics", "downgrade_reason", ""),
        ("diagnostics", "downgrade_at", 0.0),
        ("diagnostics", "compaction_count", 0),
        ("tool", "tool_lines", deque()),
        ("tool", "active_tool_lines", {}),
        ("tool", "active_tool_fingerprints", {}),
        ("tool", "tool_started_count", 1),
        ("tool", "tool_completed_count", 1),
        ("tool", "tool_failed_count", 1),
        ("tool", "completed_tool_ids", set()),
        ("tool", "todo_items", ()),
        ("tool", "todo_updated_at", 1.0),
        ("delegate", "delegate_branches", {}),
        ("delegate", "delegate_order", deque()),
        ("background", "background_jobs", {}),
        ("background", "background_order", deque()),
    )
    for owner, legacy, value in conflicts:
        owner_type = {
            "delivery": state.DeliveryState,
            "diagnostics": state.DiagnosticsState,
            "tool": state.ToolState,
            "delegate": state.DelegateState,
            "background": state.BackgroundState,
        }[owner]
        with pytest.raises(TypeError, match=rf"{owner} cannot be combined"):
            make_context(**{owner: owner_type(), legacy: value})


def test_explicit_owner_construction_preserves_identity():
    prerequisites()
    assistant = state.AssistantState(latest_text="owned")
    reasoning = state.ReasoningState(text="owned")
    first = state.SessionContext("s", "k", "discord", "c", None, None, None, assistant=assistant)
    second = state.SessionContext("s", "k", "discord", "c", None, None, None, reasoning=reasoning)
    delegate = state.DelegateState()
    background = state.BackgroundState()
    delivery = state.DeliveryState(message_id="owned")
    diagnostics = state.DiagnosticsState(last_error="owned")
    third = state.SessionContext(
        "s",
        "k",
        "discord",
        "c",
        None,
        None,
        None,
        delegate=delegate,
        background=background,
        delivery=delivery,
        diagnostics=diagnostics,
    )
    assert first.assistant is assistant
    assert second.reasoning is reasoning
    assert third.delegate is delegate
    assert third.background is background
    assert third.delivery is delivery
    assert third.diagnostics is diagnostics


def test_delivery_diagnostics_exact_contract_defaults_and_import_order():
    prerequisites()
    delivery_names = [
        "message_id",
        "can_edit",
        "disabled",
        "progress_state",
        "finalized_at",
        "last_render_at",
        "edit_state",
        "edit_backoff_until",
        "edit_failure_count",
        "edit_recovery_sends",
        "delayed_flush_task",
        "delete_task",
        "fallback_send_count",
        "snapshots_sent",
    ]
    delivery_types = [
        "str | None",
        "bool",
        "bool",
        "str",
        "float",
        "float",
        "str",
        "float",
        "int",
        "int",
        "Any",
        "Any",
        "int",
        "int",
    ]
    diagnostics_names = [
        "last_event_at",
        "new_events_since_snapshot",
        "total_events",
        "last_error",
        "downgrade_reason",
        "downgrade_at",
        "compaction_count",
    ]
    diagnostics_types = ["float", "int", "int", "str", "str", "float", "int"]
    delivery_fields = fields(state.DeliveryState)
    diagnostics_fields = fields(state.DiagnosticsState)
    assert [item.name for item in delivery_fields] == delivery_names
    assert [item.type for item in delivery_fields] == delivery_types
    assert [item.name for item in diagnostics_fields] == diagnostics_names
    assert [item.type for item in diagnostics_fields] == diagnostics_types
    delivery = state.DeliveryState()
    diagnostics = state.DiagnosticsState()
    assert delivery == state.DeliveryState(
        None, True, False, "active", 0.0, 0.0, "editable", 0.0, 0, 0, None, None, 0, 0
    )
    assert isinstance(diagnostics.last_event_at, float)
    assert (
        diagnostics.new_events_since_snapshot,
        diagnostics.total_events,
        diagnostics.last_error,
        diagnostics.downgrade_reason,
        diagnostics.downgrade_at,
        diagnostics.compaction_count,
    ) == (0, 0, "", "", 0.0, 0)
    checks = """
r = importlib.import_module('hermes_progress_tail.models.state_records')
c = importlib.import_module('hermes_progress_tail.models.state_compat')
s = importlib.import_module('hermes_progress_tail.models.state')
assert s.DeliveryState is r.DeliveryState
assert s.DiagnosticsState is r.DiagnosticsState
assert issubclass(s.SessionContext, c.SessionStateCompatibility)
"""
    for first in ("state_records", "state_compat", "state"):
        code = (
            f"import importlib\nimportlib.import_module('hermes_progress_tail.models.{first}')\n"
            + checks
        )
        subprocess.run([sys.executable, "-c", code], check=True)


def test_tool_state_exact_contract_identity_and_import_order():
    prerequisites()
    names = [
        "lines",
        "active_lines",
        "active_fingerprints",
        "started_count",
        "completed_count",
        "failed_count",
        "completed_ids",
        "todo_items",
        "todo_updated_at",
    ]
    annotations = [
        "deque[str]",
        "dict[str, str]",
        "dict[str, str]",
        "int",
        "int",
        "int",
        "set[str]",
        "tuple[TodoItem, ...]",
        "float",
    ]
    tool_fields = fields(state.ToolState)
    assert [item.name for item in tool_fields] == names
    assert [item.type for item in tool_fields] == annotations
    by_name = {item.name: item for item in tool_fields}
    lines = by_name["lines"].default_factory()
    assert isinstance(lines, deque) and lines.maxlen == 3 and not lines
    assert by_name["active_lines"].default_factory is dict
    assert by_name["active_fingerprints"].default_factory is dict
    assert by_name["completed_ids"].default_factory is set
    owner = state.ToolState()
    assert (owner.started_count, owner.completed_count, owner.failed_count) == (0, 0, 0)
    assert owner.todo_items == () and owner.todo_updated_at == 0.0
    assert make_context(tool=owner).tool is owner
    checks = """
r = importlib.import_module('hermes_progress_tail.models.state_records')
s = importlib.import_module('hermes_progress_tail.models.state')
assert s.ToolState is r.ToolState
"""
    for first in ("state_records", "state"):
        code = (
            f"import importlib\nimportlib.import_module('hermes_progress_tail.models.{first}')\n"
            + checks
        )
        subprocess.run([sys.executable, "-c", code], check=True)


def test_event_models_are_dependency_safe_and_preserve_reexport_identity():
    prerequisites()
    checks = """
e = importlib.import_module('hermes_progress_tail.models.events')
s = importlib.import_module('hermes_progress_tail.models.state')
assert s.ToolEvent is e.ToolEvent
assert s.TodoItem is e.TodoItem
assert s.TodoStatus is e.TodoStatus
"""
    for first in ("events", "state"):
        code = (
            f"import importlib\nimportlib.import_module('hermes_progress_tail.models.{first}')\n"
            + checks
        )
        subprocess.run([sys.executable, "-c", code], check=True)


def test_production_uses_nested_owner_access():
    prerequisites()
    package = Path(__file__).parents[1] / "hermes_progress_tail"
    paths = [
        *sorted((package / "rendering").glob("*.py")),
        package / "runtime" / "agent_events.py",
        package / "runtime" / "commands.py",
        package / "runtime" / "tool_events.py",
    ]
    offenders = []
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in MIGRATED
                and isinstance(node.value, ast.Name)
                and node.value.id == "ctx"
            ):
                offenders.append(f"{path.name}:{node.lineno}:{node.attr}")
    assert offenders == [], (
        "flat production access outside runtime compatibility boundaries: " + ", ".join(offenders)
    )
