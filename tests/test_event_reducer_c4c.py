import ast
import asyncio
import dataclasses
import inspect
from pathlib import Path

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.models.state import (
    BackgroundJobEvent,
    DelegateEvent,
    SessionContext,
)
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering.delegate import DelegateProgressRenderer
from hermes_progress_tail.rendering.event_reducer import EventReducer, ReductionResult
from tests.support.rendering import EditableAdapter

EXPECTED_RESULT_FIELDS = (
    "force",
    "skip_render",
    "pending_chars",
    "delegate_cleanup",
    "background_poll_cancellations",
)


class Poll:
    def __init__(self):
        self.cancelled = False

    def done(self):
        return False

    def cancel(self):
        self.cancelled = True


def context(strategy="summary_only"):
    return SessionContext("s", "k", "discord", "chat", None, EditableAdapter(), None, strategy)


def reducer(config=None, *, cleanup=None):
    settings = load_settings(config or {})
    return EventReducer(
        settings,
        DelegateProgressRenderer(settings),
        schedule_delegate_cleanup=cleanup,
    )


def assert_result_protocol():
    fields = tuple(field.name for field in dataclasses.fields(ReductionResult))
    assert fields == EXPECTED_RESULT_FIELDS


def test_characterization_renderer_delegate_application_and_terminal_cleanup_boundary():
    renderer = ProgressRenderer(load_settings({}))
    ctx = context()
    scheduled = []
    renderer._schedule_delegate_cleanup = lambda item, key, branch: scheduled.append(
        (item, key, branch)
    )
    renderer._apply_delegate_event(
        ctx,
        DelegateEvent("s", "k", "discord", "child", event_type="subagent.start", goal="goal"),
    )
    renderer._apply_delegate_event(
        ctx,
        DelegateEvent(
            "s",
            "k",
            "discord",
            "child",
            event_type="subagent.complete",
            status="completed",
            summary="done",
        ),
    )
    assert "child" in ctx.delegate_branches
    assert scheduled and scheduled[0][:2] == (ctx, "child")


def test_characterization_renderer_background_application_cancels_terminal_poll():
    renderer = ProgressRenderer(load_settings({}))
    ctx = context()
    renderer._apply_background_job_event(
        ctx, BackgroundJobEvent("s", "k", "discord", "process", event_type="started")
    )
    poll = Poll()
    ctx.background_jobs["process"].poll_task = poll
    renderer._apply_background_job_event(
        ctx,
        BackgroundJobEvent(
            "s", "k", "discord", "process", event_type="completed", exited=True, exit_code=0
        ),
    )
    assert poll.cancelled
    assert ctx.background_jobs["process"].poll_task is None


def test_architecture_c4c_result_protocol_is_frozen_and_complete():
    assert_result_protocol()
    result = ReductionResult()
    assert result == ReductionResult(
        force=False,
        skip_render=False,
        pending_chars=0,
        delegate_cleanup=None,
        background_poll_cancellations=(),
    )
    try:
        result.background_poll_cancellations = (object(),)
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("ReductionResult must remain frozen")


def test_architecture_delegate_reduction_returns_force_and_cleanup_intent_without_callback():
    assert_result_protocol()
    callback_calls = []
    item = reducer(cleanup=lambda *args: callback_calls.append(args))
    ctx = context()
    started = item.reduce(
        ctx,
        DelegateEvent("s", "k", "discord", "child", event_type="subagent.start", goal="goal"),
    )
    branch = ctx.delegate_branches["child"]
    assert started == ReductionResult()
    terminal = item.reduce(
        ctx,
        DelegateEvent(
            "s",
            "k",
            "discord",
            "child",
            event_type="subagent.complete",
            status="completed",
            summary="done",
        ),
    )
    assert terminal.force
    assert terminal.delegate_cleanup == ("child", branch)
    assert callback_calls == []


def test_architecture_background_reduction_returns_immutable_intents_without_task_mutation():
    assert_result_protocol()
    item = reducer()
    ctx = context()
    item.reduce(ctx, BackgroundJobEvent("s", "k", "discord", "p", event_type="started"))
    poll = Poll()
    job = ctx.background_jobs["p"]
    job.poll_task = poll
    result = item.reduce(
        ctx,
        BackgroundJobEvent(
            "s", "k", "discord", "p", event_type="completed", exited=True, exit_code=0
        ),
    )
    assert result.force
    assert result.background_poll_cancellations == (job,)
    assert not poll.cancelled
    assert job.poll_task is poll

    cleanup = item.reduce(
        ctx,
        BackgroundJobEvent("s", "k", "discord", "cleanup", event_type="cleanup", created_at=10**12),
    )
    assert cleanup.background_poll_cancellations == (job,)
    assert "p" not in ctx.background_jobs
    assert not poll.cancelled


def test_architecture_background_max_order_pruning_returns_cancel_intents():
    assert_result_protocol()
    item = reducer(
        {"progress_tail": {"background_jobs": {"max_jobs": 1, "completed_ttl_seconds": 10**12}}}
    )
    ctx = context()
    cancellations = []
    for number in range(4):
        process_id = f"p{number}"
        item.reduce(
            ctx,
            BackgroundJobEvent("s", "k", "discord", process_id, event_type="started"),
        )
        poll = Poll()
        ctx.background_jobs[process_id].poll_task = poll
        result = item.reduce(
            ctx,
            BackgroundJobEvent(
                "s",
                "k",
                "discord",
                process_id,
                event_type="completed",
                exited=True,
                exit_code=0,
            ),
        )
        cancellations.extend(result.background_poll_cancellations)
    assert len(ctx.background_order) <= 3
    assert cancellations
    assert all(not job.poll_task.cancelled for job in cancellations if job.poll_task is not None)


def test_architecture_renderer_executes_reducer_intents_and_owns_finalize_orchestration():
    assert_result_protocol()
    renderer = ProgressRenderer(load_settings({}))
    ctx = context()
    renderer.register_context(ctx)
    scheduled = []
    renderer._schedule_delegate_cleanup = lambda item, key, branch: scheduled.append((key, branch))

    async def exercise():
        await renderer.handle_event(
            DelegateEvent("s", "k", "discord", "child", event_type="subagent.start", goal="goal")
        )
        await renderer.handle_event(
            DelegateEvent(
                "s",
                "k",
                "discord",
                "child",
                event_type="subagent.complete",
                status="completed",
            )
        )
        await renderer.handle_event(
            BackgroundJobEvent("s", "k", "discord", "process", event_type="started")
        )
        poll = Poll()
        ctx.background_jobs["process"].poll_task = poll
        await renderer.handle_event(
            BackgroundJobEvent("s", "k", "discord", "process", event_type="completed", exited=True)
        )
        return poll

    poll = asyncio.run(exercise())
    assert scheduled and scheduled[0][0] == "child"
    assert poll.cancelled
    assert ctx.background_jobs["process"].poll_task is None

    path = Path("hermes_progress_tail/rendering/renderer.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProgressRenderer"
    )
    finalize = next(
        node
        for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "finalize"
    )
    assert any(isinstance(node, ast.AsyncWith) for node in ast.walk(finalize))
    assert any(isinstance(node, ast.Await) for node in ast.walk(finalize))
    assert "renderer_orchestration" not in source
    assert not Path("hermes_progress_tail/rendering/renderer_orchestration.py").exists()
    assert len(source.splitlines()) < 400


def test_architecture_c4c_compatibility_forward_signatures_are_exact():
    expected = {
        "_apply_background_job_event": ("self", "ctx", "event"),
        "_apply_delegate_event": ("self", "ctx", "event"),
        "_delegate_event_is_terminal": ("event",),
        "_cancel_background_poll": ("job",),
        "_reset_turn": ("ctx",),
        "_has_background_jobs": ("ctx",),
        "_normalize_reasoning": ("text",),
        "_should_flush_before_reset": ("ctx",),
    }
    for name, parameters in expected.items():
        assert tuple(inspect.signature(getattr(ProgressRenderer, name)).parameters) == parameters

    reducer_source = Path("hermes_progress_tail/rendering/event_reducer.py").read_text(
        encoding="utf-8"
    )
    assert "cancel_background_poll" not in reducer_source
    assert ".cancel()" not in reducer_source
