import asyncio
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

from hermes_progress_tail.models.state import BackgroundJob, SessionContext
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.runtime import plugin, tool_events
from hermes_progress_tail.settings.loading import load_settings
from tests.support.rendering import EditableAdapter, SequenceEditAdapter


class LoopThread:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.loop.set_debug(True)
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        async def shutdown():
            pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


class Registry:
    def __init__(self, values):
        self.values = iter(values)
        self.pending_watchers = []

    def get(self, process_id):
        return next(self.values)


class Session:
    def __init__(self):
        self.output_buffer = "running"
        self.exited = False
        self.exit_code = None
        self.command = "pytest -q"
        self.cwd = "/repo"
        self.pid = 9
        self.notify_on_complete = True
        self.watcher_interval = 10
        self.watch_patterns = ["done"]

    def complete(self):
        self.output_buffer = "2 passed"
        self.exited = True
        self.exit_code = 0


class CompletingRegistry(Registry):
    def __init__(self, session, *, after=2):
        super().__init__([])
        self.session = session
        self.after = after
        self.reads = 0

    def get(self, process_id):
        self.reads += 1
        if self.reads >= self.after:
            self.session.complete()
        return self.session


def _install_registry(monkeypatch, registry):
    package = ModuleType("tools")
    module = ModuleType("tools.process_registry")
    module.process_registry = registry
    monkeypatch.setitem(sys.modules, "tools", package)
    monkeypatch.setitem(sys.modules, "tools.process_registry", module)


def _ctx(loop):
    return SessionContext("sid", "key", "discord", "chat", None, None, loop)


def _renderer(**job_settings):
    defaults = {"enabled": True, "update_interval_seconds": 0, "completed_ttl_seconds": 0}
    defaults.update(job_settings)
    return SimpleNamespace(settings=SimpleNamespace(background_jobs=SimpleNamespace(**defaults)))


def test_poll_from_a_foreign_thread_runs_on_an_idle_loop(monkeypatch):
    events = []
    delivered = threading.Event()

    def record(context, event, force=False):
        events.append(event)
        delivered.set()

    monkeypatch.setattr(tool_events, "_schedule_render", record)
    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())
    _install_registry(monkeypatch, Registry([None]))

    with LoopThread() as harness:
        ctx = _ctx(harness.loop)
        ctx.background.jobs["proc"] = BackgroundJob("proc")
        tool_events._schedule_background_job_poll(ctx, "proc")
        assert delivered.wait(5)

    assert [(event.process_id, event.event_type, event.exited) for event in events] == [
        ("proc", "lost", True)
    ]


def test_cleanup_from_a_foreign_thread_runs_on_an_idle_loop(monkeypatch):
    events = []
    delivered = threading.Event()

    def record(context, event, force=False):
        events.append((event, force))
        delivered.set()

    monkeypatch.setattr(tool_events, "_schedule_render", record)
    monkeypatch.setattr(plugin, "_get_renderer", lambda: _renderer())

    with LoopThread() as harness:
        ctx = _ctx(harness.loop)
        tool_events._schedule_background_job_cleanup(ctx, "proc")
        assert delivered.wait(5)

    assert [(event.process_id, event.event_type, force) for event, force in events] == [
        ("proc", "cleanup", True)
    ]


def test_post_tool_hook_from_a_worker_thread_polls_without_another_loop_event(monkeypatch):
    adapter = EditableAdapter()
    renderer = ProgressRenderer(
        load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False},
                    "background_jobs": {"update_interval_seconds": 1},
                }
            }
        )
    )
    _install_registry(monkeypatch, CompletingRegistry(Session()))
    monkeypatch.setattr(plugin, "_get_renderer", lambda: renderer)

    with LoopThread() as harness:
        ctx = SessionContext(
            "s1",
            "k1",
            "discord",
            "chat",
            None,
            adapter,
            harness.loop,
            "live_tail",
            timestamp=False,
        )
        renderer.register_context(ctx)
        plugin._on_post_tool_call(
            "terminal",
            args={"background": True, "command": "pytest -q"},
            result='{"session_id":"proc_live","exited":false}',
            task_id="k1",
            session_id="s1",
        )
        deadline = time.monotonic() + 10
        claimed = False
        while time.monotonic() < deadline:
            job = ctx.background_jobs.get("proc_live")
            if job is not None and job.status == "running" and job.poll_task is not None:
                claimed = True
            if job is not None and job.status == "completed":
                break
            time.sleep(0.01)
        job = ctx.background_jobs["proc_live"]

    assert claimed
    assert (job.status, job.exit_code) == ("completed", 0)
    assert "2 passed" in " ".join(job.output_tail)

def _wait_until(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError("condition not reached before timeout")

def _scheduled_delete(adapter, loop):
    renderer = ProgressRenderer(
        load_settings({"progress_tail": {"cleanup": {"auto_delete": True, "delay_seconds": 60}}})
    )
    ctx = SessionContext("s1", "k1", "discord", "chat", None, adapter, loop, "live_tail")
    renderer.register_context(ctx)
    ctx.delivery.message_id = "m1"
    loop.call_soon_threadsafe(lambda: renderer._schedule_auto_delete(ctx, success=True))
    return renderer, ctx, _wait_until(lambda: ctx.delivery.delete_task)

def test_cancel_delete_from_a_foreign_thread_cancels_the_delete_task():
    adapter = SequenceEditAdapter([])

    with LoopThread() as harness:
        renderer, ctx, task = _scheduled_delete(adapter, harness.loop)
        renderer._cancel_delete(ctx)

        assert ctx.delivery.delete_task is None
        _wait_until(task.done)

    assert task.cancelled()
    assert adapter.deleted == []

def test_cancel_delayed_flush_from_a_foreign_thread_cancels_the_flush_task():
    renderer = ProgressRenderer(load_settings({}))

    with LoopThread() as harness:
        ctx = _ctx(harness.loop)
        ctx.adapter = EditableAdapter()
        renderer.register_context(ctx)
        harness.loop.call_soon_threadsafe(lambda: renderer._schedule_delayed_live_flush(ctx, 60))
        task = _wait_until(lambda: ctx.delivery.delayed_flush_task)
        renderer._cancel_delayed_flush(ctx)

        assert ctx.delivery.delayed_flush_task is None
        _wait_until(task.done)

    assert task.cancelled()

def test_reactivate_from_a_worker_thread_cancels_the_pending_delete():
    adapter = SequenceEditAdapter([])

    with LoopThread() as harness:
        renderer, ctx, task = _scheduled_delete(adapter, harness.loop)
        ctx.delivery.progress_state = "finalized"
        tool_events._reactivate_foreground_context(renderer, ctx)

        assert ctx.delivery.progress_state == "active"
        assert ctx.delivery.delete_task is None
        _wait_until(task.done)

    assert task.cancelled()
    assert adapter.deleted == []
