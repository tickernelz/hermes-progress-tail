import dataclasses
import importlib

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
