from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hermes_progress_tail.hooks.contracts import HookCallbacks, inert_hook_callbacks


def _callbacks(events: list[tuple[object, ...]]) -> HookCallbacks:
    return HookCallbacks(
        on_reasoning_delta=lambda agent, text, *, source="provider": events.append(
            ("reasoning", agent, text, source)
        ),
        on_assistant_progress=lambda agent, text, *, already_streamed=False: (
            events.append(("assistant", agent, text, already_streamed)) or True
        ),
        on_delegate_progress=lambda parent, event_type, tool_name=None, preview=None, cb_args=None, **kwargs: (
            events.append(("delegate", parent, event_type, tool_name, preview, cb_args, kwargs))
        ),
        on_compression_status=lambda agent, text: (
            events.append(("compression", agent, text)) or True
        ),
        on_compression_lifecycle=lambda agent, *, phase, old_session_id, **metrics: events.append(
            ("lifecycle", agent, phase, old_session_id, metrics)
        ),
        register_adapter_context=lambda adapter, event: events.append(("adapter", adapter, event)),
        on_gateway_stop=lambda runner, *, session_key, source: events.append(
            ("stop", runner, session_key, source)
        ),
        reasoning_enabled=lambda agent: agent == "enabled",
        telegram_settings=lambda: {"enabled": True},
    )


def test_callbacks_forward_positional_keyword_and_variadic_arguments() -> None:
    events: list[tuple[object, ...]] = []
    callbacks = _callbacks(events)

    callbacks.on_reasoning_delta("agent", "text", source="model")
    assert callbacks.on_assistant_progress("agent", "text", already_streamed=True) is True
    callbacks.on_delegate_progress("parent", "event", "tool", "preview", (1,), extra=2)
    assert callbacks.on_compression_status("agent", "shrinking") is True
    callbacks.on_compression_lifecycle("agent", phase="before", old_session_id="old", count=3)
    callbacks.register_adapter_context("adapter", "event")
    callbacks.on_gateway_stop("runner", session_key="key", source="signal")
    assert callbacks.reasoning_enabled("enabled") is True
    assert callbacks.telegram_settings() == {"enabled": True}
    assert events == [
        ("reasoning", "agent", "text", "model"),
        ("assistant", "agent", "text", True),
        ("delegate", "parent", "event", "tool", "preview", (1,), {"extra": 2}),
        ("compression", "agent", "shrinking"),
        ("lifecycle", "agent", "before", "old", {"count": 3}),
        ("adapter", "adapter", "event"),
        ("stop", "runner", "key", "signal"),
    ]


def test_hook_callbacks_are_frozen() -> None:
    callbacks = inert_hook_callbacks()
    with pytest.raises(FrozenInstanceError):
        callbacks.telegram_settings = lambda: object()  # type: ignore[misc]


def test_inert_callbacks_return_fail_open_values() -> None:
    callbacks = inert_hook_callbacks()
    assert callbacks.on_reasoning_delta(object(), "delta") is None
    assert callbacks.on_assistant_progress(object(), "progress") is False
    assert callbacks.on_delegate_progress(object(), "event", extra=True) is None
    assert callbacks.on_compression_status(object(), "status") is False
    assert (
        callbacks.on_compression_lifecycle(object(), phase="before", old_session_id=None, count=1)
        is None
    )
    assert callbacks.register_adapter_context(object(), object()) is None
    assert callbacks.on_gateway_stop(object(), session_key="key", source="test") is None
    assert callbacks.reasoning_enabled(object()) is False
    assert callbacks.telegram_settings() is None
