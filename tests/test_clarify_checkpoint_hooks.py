import asyncio
import threading
from types import SimpleNamespace

import pytest

from hermes_progress_tail.models.events import DecisionEvent, ToolEvent
from hermes_progress_tail.models.state import SessionContext
from hermes_progress_tail.runtime import tool_events
from hermes_progress_tail.runtime.clarify import (
    classify_clarify_result,
    run_clarify_freeze_barrier,
    run_clarify_resolution_bridge,
)
from hermes_progress_tail.runtime.decision_events import (
    build_todo_record,
    build_tool_completion_record,
    build_tool_start_record,
)


class LoopThread:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()


def _ctx(loop=None, *, tools=True):
    ctx = SessionContext("sid", "key", "discord", "chat", None, None, loop)
    ctx.tools_enabled = tools
    return ctx


def _renderer(*, show_completed=True, freeze=None, resolve=None):
    settings = SimpleNamespace(
        tools=SimpleNamespace(show_completed=show_completed, show_duration=True),
        patch=SimpleNamespace(detail="summary", preview_chars=100, max_files=3),
        background_jobs=SimpleNamespace(enabled=False),
    )
    return SimpleNamespace(
        settings=settings,
        freeze_clarify=freeze,
        resolve_clarify=resolve,
    )


def test_tool_records_share_call_identity_and_completion_replaces_start():
    start = build_tool_start_record("terminal", {"command": "pytest"}, "call-7")
    done = build_tool_completion_record("terminal", {"command": "pytest"}, "ok", "call-7")
    assert (start.identity, done.identity) == ("tool:call-7", "tool:call-7")
    assert start.kind == done.kind == "tool"
    assert "running" in start.text and "ok" in done.text


def test_tool_record_without_id_uses_stable_sanitized_argument_fingerprint():
    args = {"path": "/tmp/source.py", "token": "super-secret"}
    start = build_tool_start_record("read_file", args, "")
    done = build_tool_completion_record("read_file", args, {"success": True}, "")
    assert start.identity == done.identity
    assert start.identity.startswith("tool:fp:")
    assert "super-secret" not in start.identity + start.text


def test_tool_record_priorities_and_compaction_are_bounded_and_redacted():
    ordinary = build_tool_completion_record("read_file", {"path": "a.py"}, "read", "a")
    verified = build_tool_completion_record("terminal", {"command": "pytest"}, "1 passed", "b")
    failed = build_tool_completion_record("terminal", {"command": "pytest"}, {"exit_code": 2}, "c")
    warning = build_tool_completion_record(
        "terminal", {"command": "echo"}, "warning: disk low", "warn"
    )
    noisy = build_tool_completion_record(
        "terminal",
        {"command": "pytest"},
        "\x1b[31mfirst\x1b[0m\nOPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz\nfinal useful line",
        "d",
    )
    assert failed.priority > verified.priority > ordinary.priority
    assert warning.priority == failed.priority
    assert warning.kind == "warning"
    assert noisy.text.endswith("final useful line")
    assert "\x1b" not in noisy.text and "sk-abcdefghijklmnopqrstuvwxyz" not in noisy.text
    assert len(noisy.text) <= 360


def test_todo_record_is_one_replaceable_plan_and_never_stringifies_raw_results():
    first = build_todo_record({"todos": [{"content": "ship", "status": "in_progress"}]})
    second = build_todo_record({"todos": [{"content": "test", "status": "completed"}]})
    raw = build_tool_completion_record("read_file", {}, {"output": ["private", "payload"]}, "read")
    assert (first.kind, first.identity) == ("plan", "todo:plan")
    assert first.identity == second.identity
    assert "['private'" not in raw.text and "private" not in raw.text


@pytest.mark.parametrize(
    ("result", "status", "answer"),
    [
        ('{"user_response":"[user did not respond in time]"}', "timed_out", ""),
        ('{"user_response":["[clarify prompt could not be delivered]"]}', "prompt_failed", ""),
        ('{"user_response":"  "}', "prompt_failed", ""),
        ('{"user_response":[" first ", 2, " ", "second"]}', "resolved", "first, 2, second"),
        (
            '{"user_response":" answer OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz "}',
            "resolved",
            "answer [redacted_env]",
        ),
        ("not-json-secret=do-not-leak", "prompt_failed", ""),
    ],
)
def test_classify_clarify_result_uses_envelope_precedence_and_safe_answers(result, status, answer):
    assert classify_clarify_result(result) == (status, answer)


def test_freeze_barrier_waits_for_real_helper_loop_completion():
    with LoopThread() as helper:
        ctx = _ctx(helper.loop)
        completed = []

        async def operation(guard):
            await asyncio.sleep(0.02)
            completed.append(not guard.is_cancelled())
            return True

        assert run_clarify_freeze_barrier(ctx, operation)
        assert completed == [True]


def test_freeze_barrier_fails_open_for_closed_loop_and_same_loop_is_best_effort():
    closed = asyncio.new_event_loop()
    closed.close()
    assert not run_clarify_freeze_barrier(_ctx(closed), lambda _: asyncio.sleep(0))

    async def run():
        called = []

        async def operation(_):
            called.append(True)
            return True

        assert run_clarify_freeze_barrier(_ctx(asyncio.get_running_loop()), operation)
        await asyncio.sleep(0)
        assert called == [True]

    asyncio.run(run())


def test_resolution_bridge_timeout_leaves_queued_resolution_alive(monkeypatch):
    from hermes_progress_tail.runtime import clarify

    monkeypatch.setattr(clarify, "_BARRIER_TIMEOUT_SECONDS", 0.01)
    with LoopThread() as helper:
        ctx = _ctx(helper.loop)
        ctx.decision.pending_freeze = object()
        started, release, resolved = threading.Event(), threading.Event(), []

        async def freeze_holds_lock():
            async with ctx.lock:
                started.set()
                while not release.is_set():
                    await asyncio.sleep(0.001)

        async def operation():
            async with ctx.lock:
                ctx.decision.pending_freeze = None
                resolved.append(True)
                return True

        asyncio.run_coroutine_threadsafe(freeze_holds_lock(), helper.loop)
        assert started.wait(1)
        run_clarify_resolution_bridge(ctx, operation)
        assert ctx.decision.pending_freeze is not None and resolved == []
        release.set()
        for _ in range(100):
            if resolved:
                break
            threading.Event().wait(0.005)
        assert ctx.decision.pending_freeze is None and resolved == [True]


def test_clarify_hooks_bypass_live_tool_guards_and_publish_no_tool_lines(monkeypatch):
    with LoopThread() as helper:
        ctx = _ctx(helper.loop, tools=False)
        frozen, resolved, events = [], [], []

        async def freeze(context, args, is_cancelled):
            frozen.append((context, args, is_cancelled()))
            return True

        async def resolve(context, status, answer=""):
            resolved.append((context, status, answer))
            return True

        renderer = _renderer(show_completed=False, freeze=freeze, resolve=resolve)
        monkeypatch.setattr(
            tool_events, "_runtime_provider", SimpleNamespace(get_renderer=lambda: renderer)
        )
        monkeypatch.setattr(tool_events, "_resolve_tool_agent", lambda *args: (None, None))
        monkeypatch.setattr(tool_events, "_should_suppress_agent_progress", lambda _: False)
        monkeypatch.setattr(tool_events, "_tool_context_lookup_ids", lambda *args: ("sid", "key"))
        monkeypatch.setattr(tool_events, "_context_for_non_background_thread", lambda *args: ctx)
        monkeypatch.setattr(
            tool_events, "_schedule_render", lambda _ctx, event: events.append(event)
        )

        tool_events._on_pre_tool_call("clarify", {"question": "continue?", "choices": []})
        tool_events._on_post_tool_call("clarify", result='{"user_response":"yes"}')
        assert frozen == [(ctx, {"question": "continue?", "choices": []}, False)]
        assert resolved == [(ctx, "resolved", "yes")]
        assert not events


def test_non_clarify_hooks_archive_decision_events_when_live_display_is_disabled(monkeypatch):
    ctx, events = _ctx(tools=False), []
    renderer = _renderer(show_completed=False)
    monkeypatch.setattr(
        tool_events, "_runtime_provider", SimpleNamespace(get_renderer=lambda: renderer)
    )
    monkeypatch.setattr(tool_events, "_resolve_tool_agent", lambda *args: (None, None))
    monkeypatch.setattr(tool_events, "_should_suppress_agent_progress", lambda _: False)
    monkeypatch.setattr(tool_events, "_tool_context_lookup_ids", lambda *args: ("sid", "key"))
    monkeypatch.setattr(tool_events, "_context_for_non_background_thread", lambda *args: ctx)
    monkeypatch.setattr(tool_events, "_update_environment_from_agent", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_events, "_schedule_render", lambda _ctx, event: events.append(event))

    tool_events._on_pre_tool_call("read_file", {"path": "a.py"}, tool_call_id="read")
    tool_events._on_post_tool_call("read_file", result="ok", tool_call_id="read")
    assert [type(event) for event in events] == [DecisionEvent, DecisionEvent]
    assert not any(isinstance(event, ToolEvent) for event in events)
