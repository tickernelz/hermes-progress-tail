import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hermes_progress_tail.models.state import BackgroundJob, SessionContext
from hermes_progress_tail.runtime import plugin, tool_events


class CapturingLoop:
    def __init__(self):
        self.coroutines = []

    def create_task(self, coroutine):
        self.coroutines.append(coroutine)
        return SimpleNamespace(done=lambda: False)


class Registry:
    def __init__(self, values):
        self.values = iter(values)
        self.pending_watchers = []

    def get(self, process_id):
        value = next(self.values)
        if isinstance(value, BaseException):
            raise value
        return value


def _ctx(loop=None):
    return SessionContext("sid", "key", "discord", "chat", None, None, loop)


def _renderer(**job_settings):
    defaults = {
        "enabled": True,
        "update_interval_seconds": 0,
        "completed_ttl_seconds": 0,
        "suppress_native_notify": True,
        "suppress_watch_notifications": True,
    }
    defaults.update(job_settings)
    return SimpleNamespace(settings=SimpleNamespace(background_jobs=SimpleNamespace(**defaults)))


def _install_registry(monkeypatch, registry):
    package = ModuleType("tools")
    module = ModuleType("tools.process_registry")
    module.process_registry = registry
    monkeypatch.setitem(sys.modules, "tools", package)
    monkeypatch.setitem(sys.modules, "tools.process_registry", module)


def _run_poll(loop):
    coroutine = loop.coroutines.pop()
    asyncio.run(coroutine)


def test_native_notification_suppression_updates_session_and_pending_watchers(monkeypatch):
    session = SimpleNamespace(notify_on_complete=True, watcher_interval=10, watch_patterns=["done"])
    registry = Registry([session])
    registry.pending_watchers = [{"session_id": "proc"}, {"session_id": "other"}]
    _install_registry(monkeypatch, registry)
    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())

    tool_events._suppress_native_background_notify("")
    tool_events._suppress_native_background_notify("proc")

    assert not session.notify_on_complete
    assert session.watcher_interval == 0
    assert session.watch_patterns == []
    assert registry.pending_watchers == [{"session_id": "other"}]


def test_native_notification_suppression_tolerates_missing_registry(monkeypatch, caplog):
    monkeypatch.delitem(sys.modules, "tools.process_registry", raising=False)
    caplog.set_level("DEBUG", logger=tool_events.__name__)
    tool_events._suppress_native_background_notify("proc")
    assert [record.getMessage() for record in caplog.records] == [
        "hermes-progress-tail failed to suppress native background notify"
    ]


@pytest.mark.parametrize(("process_id", "loop"), [("", CapturingLoop()), ("proc", None)])
def test_poll_scheduling_guards_missing_identity_or_loop(monkeypatch, process_id, loop):
    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())
    ctx = _ctx(loop)
    tool_events._schedule_background_job_poll(ctx, process_id)
    assert ctx.background_jobs == {}
    if loop is not None:
        assert loop.coroutines == []


def test_poll_scheduling_ignores_live_existing_task(monkeypatch):
    loop = CapturingLoop()
    ctx = _ctx(loop)
    ctx.background_jobs["proc"] = BackgroundJob(
        "proc", poll_task=SimpleNamespace(done=lambda: False)
    )
    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())
    tool_events._schedule_background_job_poll(ctx, "proc")
    assert loop.coroutines == []


def test_poll_reports_lost_registry_session_without_real_sleep(monkeypatch):
    loop = CapturingLoop()
    ctx = _ctx(loop)
    ctx.background_jobs["proc"] = BackgroundJob("proc")
    events = []
    _install_registry(monkeypatch, Registry([None]))
    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())
    monkeypatch.setattr(asyncio, "sleep", lambda delay: _immediate())
    monkeypatch.setattr(
        tool_events, "_schedule_render", lambda context, event: events.append(event)
    )

    tool_events._schedule_background_job_poll(ctx, "proc")
    assert ctx.background_jobs["proc"].poll_task is not None
    _run_poll(loop)

    assert [
        (e.session_id, e.session_key, e.platform, e.process_id, e.event_type, e.exited)
        for e in events
    ] == [("sid", "key", "discord", "proc", "lost", True)]


def test_poll_skips_unchanged_output_then_reports_exit(monkeypatch):
    loop = CapturingLoop()
    ctx = _ctx(loop)
    ctx.background_jobs["proc"] = BackgroundJob("proc", last_output="same")
    running = SimpleNamespace(output_buffer="same", exited=False)
    exited = SimpleNamespace(
        output_buffer="done", exited=True, command="pytest", cwd="/repo", pid=9, exit_code=0
    )
    events = []
    _install_registry(monkeypatch, Registry([running, exited]))
    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())
    monkeypatch.setattr(asyncio, "sleep", lambda delay: _immediate())
    monkeypatch.setattr(
        tool_events, "_schedule_render", lambda context, event: events.append(event)
    )

    tool_events._schedule_background_job_poll(ctx, "proc")
    _run_poll(loop)

    assert len(events) == 1
    event = events[0]
    assert (
        event.event_type,
        event.command,
        event.cwd,
        event.pid,
        event.output,
        event.exit_code,
    ) == (
        "completed",
        "pytest",
        "/repo",
        9,
        "done",
        0,
    )


def test_poll_registry_error_becomes_lost_and_callback_error_is_contained(monkeypatch):
    loop = CapturingLoop()
    ctx = _ctx(loop)
    _install_registry(monkeypatch, Registry([RuntimeError("registry")]))
    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())
    monkeypatch.setattr(asyncio, "sleep", lambda delay: _immediate())
    attempted = []

    def reject(context, event):
        attempted.append((context, event))
        raise ValueError("callback")

    monkeypatch.setattr(tool_events, "_schedule_render", reject)

    tool_events._schedule_background_job_poll(ctx, "proc")
    _run_poll(loop)
    assert len(attempted) == 1
    context, event = attempted[0]
    assert context is ctx
    assert (
        event.session_id,
        event.session_key,
        event.platform,
        event.process_id,
        event.event_type,
        event.exited,
    ) == ("sid", "key", "discord", "proc", "lost", True)


def test_poll_propagates_cancellation(monkeypatch):
    loop = CapturingLoop()
    ctx = _ctx(loop)

    async def cancel(_delay):
        raise asyncio.CancelledError

    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())
    monkeypatch.setattr(asyncio, "sleep", cancel)
    tool_events._schedule_background_job_poll(ctx, "proc")
    with pytest.raises(asyncio.CancelledError):
        _run_poll(loop)


def test_cleanup_scheduling_guard_and_terminal_callback(monkeypatch):
    loop = CapturingLoop()
    ctx = _ctx(loop)
    events = []
    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())
    monkeypatch.setattr(asyncio, "sleep", lambda delay: _immediate())
    monkeypatch.setattr(
        tool_events,
        "_schedule_render",
        lambda context, event, force=False: events.append((event, force)),
    )

    tool_events._schedule_background_job_cleanup(ctx, "")
    assert loop.coroutines == []
    tool_events._schedule_background_job_cleanup(ctx, "proc")
    asyncio.run(loop.coroutines.pop())
    assert [
        (
            event.session_id,
            event.session_key,
            event.platform,
            event.process_id,
            event.event_type,
            force,
        )
        for event, force in events
    ] == [("sid", "key", "discord", "proc", "cleanup", True)]


async def _immediate():
    return None
