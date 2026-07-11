import asyncio
from types import SimpleNamespace

import pytest

from hermes_progress_tail.models.state import BackgroundJobEvent, SessionContext, ToolEvent
from hermes_progress_tail.runtime import plugin, tool_events


def _ctx(*, loop=None, tools=True, background=True):
    ctx = SessionContext("sid", "key", "discord", "chat", None, None, loop)
    ctx.tools_enabled = tools
    ctx.background_jobs_enabled = background
    return ctx


def _renderer(**overrides):
    tools = SimpleNamespace(show_completed=True, show_duration=True)
    jobs = SimpleNamespace(
        enabled=True, suppress_native_notify=True, suppress_watch_notifications=True
    )
    settings = SimpleNamespace(
        tools=tools,
        background_jobs=jobs,
        patch=SimpleNamespace(detail="summary", preview_chars=100, max_files=3),
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return SimpleNamespace(settings=settings)


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (None, "done"),
        ("", "done"),
        ({"success": False}, "failed"),
        ('{"exit_code": 2}', "failed"),
        ({"error": "bad"}, "failed"),
        ({"error": "bad", "success": True}, "done"),
        ("not json: traceback here", "failed"),
        ("an Exception occurred", "failed"),
        (object(), "done"),
    ],
)
def test_result_status_normalizes_host_result_shapes(result, expected):
    assert tool_events._compact_result_status(result) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, ""), (-1, ""), (500, "0.5s"), (9999, "10.0s"), (10000, "10s")],
)
def test_duration_text_uses_compact_boundaries(value, expected):
    assert tool_events._duration_text(value) == expected


def test_json_object_accepts_only_object_payloads():
    original = {"a": 1}
    assert tool_events._json_obj(original) is original
    assert tool_events._json_obj(4) == {}
    assert tool_events._json_obj("bad") == {}
    assert tool_events._json_obj("[]") == {}


def test_schedule_render_handles_no_loop_callback_failure_and_scheduling_failure(monkeypatch):
    renderer = SimpleNamespace(handle_event=lambda *a, **k: None)
    monkeypatch.setattr(plugin, "_get_renderer", lambda: renderer)
    assert not tool_events._schedule_render(_ctx(), ToolEvent("sid", "key", "discord", "x"))

    class Future:
        def add_done_callback(self, callback):
            callback(SimpleNamespace(result=lambda: (_ for _ in ()).throw(RuntimeError("render"))))

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", lambda coro, loop: Future())
    assert tool_events._schedule_render(
        _ctx(loop=object()), ToolEvent("sid", "key", "discord", "x")
    )
    monkeypatch.setattr(
        asyncio,
        "run_coroutine_threadsafe",
        lambda coro, loop: (_ for _ in ()).throw(RuntimeError()),
    )
    assert not tool_events._schedule_render(
        _ctx(loop=object()), ToolEvent("sid", "key", "discord", "x")
    )


def test_pre_tool_guards_and_running_background_fields(monkeypatch):
    renderer = _renderer()
    ctx = _ctx()
    events = []
    monkeypatch.setattr(plugin, "_get_renderer", lambda: renderer)
    monkeypatch.setattr(plugin, "_resolve_tool_agent", lambda *a: (None, None))
    monkeypatch.setattr(plugin, "_should_suppress_agent_progress", lambda agent: False)
    monkeypatch.setattr(plugin, "_tool_context_lookup_ids", lambda *a: ("sid", ""))
    monkeypatch.setattr(plugin, "_context_for_non_background_thread", lambda *a: ctx)
    monkeypatch.setattr(plugin, "_update_environment_from_agent", lambda *a, **k: None)
    monkeypatch.setattr(plugin, "_update_environment_from_terminal", lambda *a: None)
    monkeypatch.setattr(plugin, "_schedule_render", lambda context, event: events.append(event))

    ctx.tools_enabled = False
    assert tool_events._on_pre_tool_call("terminal") is None
    assert events == []
    ctx.tools_enabled = True
    tool_events._on_pre_tool_call(
        "terminal", {"command": "build", "background": True}, tool_call_id="call"
    )
    assert events[-1].tool_call_id == "call"
    assert events[-1].tool_name == "terminal"
    assert events[-1].line.endswith(" · background")

    monkeypatch.setattr(plugin, "_context_for_non_background_thread", lambda *a: None)
    assert tool_events._on_pre_tool_call("terminal") is None


def test_post_tool_emits_background_and_completed_result_fields(monkeypatch):
    renderer = _renderer()
    ctx = _ctx()
    events = []
    polls = []
    suppressed = []
    monkeypatch.setattr(plugin, "_get_renderer", lambda: renderer)
    monkeypatch.setattr(plugin, "_resolve_tool_agent", lambda *a: (None, None))
    monkeypatch.setattr(plugin, "_should_suppress_agent_progress", lambda agent: False)
    monkeypatch.setattr(plugin, "_tool_context_lookup_ids", lambda *a: ("sid", ""))
    monkeypatch.setattr(plugin, "_context_for_non_background_thread", lambda *a: ctx)
    monkeypatch.setattr(plugin, "_update_environment_from_agent", lambda *a, **k: None)
    monkeypatch.setattr(plugin, "_update_environment_from_terminal", lambda *a: None)
    monkeypatch.setattr(plugin, "_schedule_render", lambda context, event: events.append(event))
    monkeypatch.setattr(plugin, "_schedule_background_job_poll", lambda c, pid: polls.append(pid))
    monkeypatch.setattr(plugin, "_suppress_native_background_notify", suppressed.append)

    tool_events._on_post_tool_call(
        "terminal",
        {"command": "pytest", "workdir": "/repo", "background": True},
        '{"session_id":"proc","pid":12,"output":"starting"}',
        tool_call_id="call",
        duration_ms=1250,
    )
    background = next(event for event in events if isinstance(event, BackgroundJobEvent))
    completed = next(event for event in events if isinstance(event, ToolEvent))
    assert (background.process_id, background.command, background.cwd, background.pid) == (
        "proc",
        "pytest",
        "/repo",
        12,
    )
    assert suppressed == ["proc"] and polls == ["proc"]
    assert completed.replace_existing and "✅" in completed.line and "1.2s" in completed.line

    events.clear()
    renderer.settings.tools.show_completed = False
    tool_events._on_post_tool_call("read_file", result="ok")
    assert events == []
