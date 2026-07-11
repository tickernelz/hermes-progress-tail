import dataclasses
import importlib
import inspect

import pytest

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.models.state import (
    AssistantEvent,
    BackgroundJobEvent,
    DelegateEvent,
    ReasoningEvent,
    SessionContext,
    ToolEvent,
)
from hermes_progress_tail.renderer import ProgressRenderer
from tests.support.rendering import EditableAdapter


def _context():
    return SessionContext("s", "k", "discord", "chat", None, EditableAdapter(), None, "live_tail")


def _types():
    module_name = "hermes_progress_tail.rendering.event_reducer"
    spec = importlib.util.find_spec(module_name)
    assert spec is not None, f"missing prospective reducer module: {module_name}"
    module = importlib.import_module(module_name)
    assert hasattr(module, "EventReducer") and hasattr(module, "ReductionResult")
    return module.EventReducer, module.ReductionResult


def _reducer(config=None):
    event_reducer, _ = _types()
    return event_reducer(load_settings(config or {}))


def test_characterization_assistant_append_replace_and_transient_clear():
    renderer = ProgressRenderer(load_settings({}))
    ctx = _context()
    assert (
        renderer._append_assistant(
            ctx, AssistantEvent("s", "k", "discord", "draft", transient=True)
        )
        == 5
    )
    assert renderer._append_assistant(ctx, AssistantEvent("s", "k", "discord", "draft more")) == 15
    assert [line.text for line in ctx.assistant_lines] == ["draft more"]
    renderer._clear_transient_assistant(ctx)
    assert [line.text for line in ctx.assistant_lines] == ["draft more"]


def test_characterization_reasoning_append_and_trim():
    renderer = ProgressRenderer(load_settings({"progress_tail": {"reasoning": {"max_chars": 3}}}))
    ctx = _context()
    assert (
        renderer._append_reasoning(ctx, ReasoningEvent("s", "k", "discord", "abcdefghijklmnop"))
        == 16
    )
    assert len(ctx.reasoning_text) <= 12
    assert ctx.last_reasoning_chars == 16


def test_architecture_reduction_result_is_frozen_with_exact_defaults():
    _, result_type = _types()
    result = result_type()
    assert dataclasses.is_dataclass(result_type)
    assert (result.force, result.skip_render, result.pending_chars) == (False, False, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.force = True


def test_architecture_accepts_exact_feature_disable_contract():
    reducer = _reducer()
    ctx = _context()
    assistant = AssistantEvent("s", "k", "discord", "a")
    reasoning = ReasoningEvent("s", "k", "discord", "r")
    tool = ToolEvent("s", "k", "discord", "t")
    delegate = DelegateEvent("s", "k", "discord", "child")
    background = BackgroundJobEvent("s", "k", "discord", "process")
    assert reducer.accepts(ctx, assistant)
    assert reducer.accepts(ctx, reasoning)
    assert reducer.accepts(ctx, tool)
    assert reducer.accepts(ctx, delegate)
    assert reducer.accepts(ctx, background)
    ctx.assistant_enabled = False
    ctx.reasoning_enabled = False
    ctx.tools_enabled = False
    ctx.delegates_enabled = False
    ctx.background_jobs_enabled = False
    assert not reducer.accepts(ctx, assistant)
    assert not reducer.accepts(ctx, reasoning)
    assert not reducer.accepts(ctx, tool)
    assert not reducer.accepts(ctx, delegate)
    assert not reducer.accepts(ctx, background)


def test_architecture_default_reducer_wiring_and_falsey_injection():
    event_reducer, _ = _types()
    settings = load_settings({})
    renderer = ProgressRenderer(settings)
    assert isinstance(renderer.reducer, event_reducer)
    assert renderer.reducer.delegate_renderer is renderer.delegate_renderer
    assert renderer.reducer.schedule_delegate_cleanup == renderer._schedule_delegate_cleanup

    class FalseyReducer:
        def __init__(self):
            self.settings = settings

        def __bool__(self):
            return False

        def replace_settings(self, replacement):
            self.settings = replacement

    injected = FalseyReducer()
    injected_renderer = ProgressRenderer(settings, reducer=injected)
    assert injected_renderer.reducer is injected


def test_architecture_assistant_reduction_result_and_transient_clearing():
    reducer = _reducer()
    ctx = _context()
    first = reducer.reduce(ctx, AssistantEvent("s", "k", "discord", "draft", transient=True))
    assert first.pending_chars == 5
    reducer.clear_transient_assistant(ctx)
    assert list(ctx.assistant_lines) == []
    assert (ctx.assistant_latest_text, ctx.assistant_pending_chars, ctx.assistant_transient) == (
        "",
        0,
        False,
    )


def test_architecture_reasoning_reduction_result_and_pending_threshold_input():
    reducer = _reducer()
    ctx = _context()
    result = reducer.reduce(ctx, ReasoningEvent("s", "k", "discord", "abc"))
    assert result.pending_chars == 3
    assert not result.force
    assert not result.skip_render
    assert ctx.reasoning_text == "abc"


def test_architecture_reducer_settings_identity_replacement():
    settings = load_settings({})
    event_reducer, _ = _types()
    reducer = event_reducer(settings)
    replacement = load_settings({"progress_tail": {"assistant": {"max_lines": 1}}})
    reducer.replace_settings(replacement)
    assert reducer.settings is replacement


def _assert_tool_reduction_contract(reducer):
    assert "tool_line" in inspect.signature(reducer.reduce).parameters


def _tool(reducer, ctx, line, **kwargs):
    _assert_tool_reduction_contract(reducer)
    event = ToolEvent("s", "k", "discord", line, **kwargs)
    return reducer.reduce(ctx, event, tool_line=line)


def test_architecture_tool_new_replace_terminal_and_idempotence():
    reducer = _reducer()
    ctx = _context()
    assert _tool(reducer, ctx, "🔧 shell · running", tool_call_id="a") == _types()[1]()
    assert (ctx.tool_started_count, ctx.active_tool_lines) == (1, {"a": "🔧 shell · running"})
    result = _tool(reducer, ctx, "✅ shell · done", tool_call_id="a", replace_existing=True)
    assert (result.force, result.skip_render, result.pending_chars) == (True, False, 0)
    assert list(ctx.tool_lines) == ["✅ shell · done"]
    assert (ctx.tool_completed_count, ctx.tool_failed_count) == (1, 0)
    _tool(reducer, ctx, "✅ shell · done", tool_call_id="a", replace_existing=True)
    assert ctx.tool_completed_count == 1


def test_architecture_tool_fingerprint_replacement_failure_and_new_completes_active():
    reducer = _reducer()
    ctx = _context()
    _tool(reducer, ctx, "🔧 build · running")
    _tool(reducer, ctx, "❌ build · failed", replace_existing=True)
    assert list(ctx.tool_lines) == ["❌ build · failed"]
    assert ctx.tool_failed_count == 1
    _tool(reducer, ctx, "🔧 one · running", tool_call_id="one")
    _tool(reducer, ctx, "🔧 two · running", tool_call_id="two")
    assert ctx.tool_completed_count == 1
    assert ctx.active_tool_lines == {"two": "🔧 two · running"}


@pytest.mark.parametrize(
    ("sticky", "hide", "expected_items", "skip"),
    [
        (True, True, True, True),
        (True, False, True, False),
        (False, True, False, True),
        (False, False, False, False),
    ],
)
def test_architecture_todo_sticky_hide_matrix(sticky, hide, expected_items, skip):
    reducer = _reducer({"progress_tail": {"todo": {"sticky": sticky, "hide_tool_line": hide}}})
    _assert_tool_reduction_contract(reducer)
    ctx = _context()
    state = importlib.import_module("hermes_progress_tail.models.state")
    items = (state.TodoItem("x", "pending"),)
    event = ToolEvent(
        "s", "k", "discord", "📋 todo", tool_name="todo", todo_items=items, created_at=42
    )
    result = reducer.reduce(ctx, event, tool_line=event.line)
    assert (bool(ctx.todo_items), ctx.todo_updated_at, result.skip_render) == (
        expected_items,
        42 if expected_items else 0.0,
        skip,
    )
    assert list(ctx.tool_lines) == ([] if hide else [event.line])


def test_architecture_tool_line_buffer_maxlen_and_compatibility_forwards():
    reducer = _reducer()
    ctx = _context()
    for number in range(5):
        _tool(reducer, ctx, f"🔧 {number} · running", tool_call_id=str(number))
    assert list(ctx.tool_lines) == ["🔧 2 · running", "🔧 3 · running", "🔧 4 · running"]
    assert tuple(inspect.signature(ProgressRenderer._tool_line_fingerprint).parameters) == ("line",)
    assert tuple(inspect.signature(ProgressRenderer._tool_line_terminal_status).parameters) == (
        "line",
    )
    assert ProgressRenderer._tool_line_fingerprint("✅ x · done") == "x"
    assert ProgressRenderer._tool_line_terminal_status("❌ x · failed") == "failed"
