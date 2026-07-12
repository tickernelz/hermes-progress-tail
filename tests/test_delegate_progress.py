import asyncio
from types import SimpleNamespace

import hermes_progress_tail.plugin as plugin
from hermes_progress_tail.models.state import DelegateEvent, SessionContext
from hermes_progress_tail.monkeypatches import (
    install_delegate_monkeypatches,
    uninstall_delegate_monkeypatches,
)
from hermes_progress_tail.plugin import _on_pre_gateway_dispatch
from hermes_progress_tail.rendering.renderer import ProgressRenderer
from hermes_progress_tail.settings.loading import load_settings
from tests.support.rendering import EditableAdapter


class Source:
    platform = type("P", (), {"value": "discord"})()
    chat_id = "chat"
    thread_id = "thread"
    user_id = "user"
    chat_type = "group"


class Event:
    source = Source()


class SessionEntry:
    session_id = "session-1"
    session_key = "key-1"


class SessionStore:
    def get_or_create_session(self, source):
        return SessionEntry()


class Gateway:
    def __init__(self, adapter):
        self.adapters = {Source.platform: adapter}
        self.config = type(
            "Config", (), {"group_sessions_per_user": True, "thread_sessions_per_user": False}
        )()


class ParentAgent:
    session_id = "session-1"
    gateway_session_key = "key-1"
    _delegate_spinner = None
    tool_progress_callback = None


def test_delegate_monkeypatch_renders_even_when_builtin_progress_callback_is_none(monkeypatch):
    async def run():
        adapter = EditableAdapter()
        plugin._renderer = None
        monkeypatch.setattr(
            plugin,
            "_load_runtime_settings",
            lambda: load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {"lines_per_delegate": 2},
                    }
                }
            ),
        )
        _on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        calls = []

        def original_builder(*args, **kwargs):
            _ = args, kwargs
            return None

        delegate_module = SimpleNamespace(_build_child_progress_callback=original_builder)
        uninstall_delegate_monkeypatches(delegate_module)
        assert install_delegate_monkeypatches(delegate_module) is True

        cb = delegate_module._build_child_progress_callback(
            0, "review delegate path", ParentAgent(), 1
        )
        cb(
            "subagent.tool",
            "terminal",
            "pytest tests/test_delegate_progress.py",
            {"command": "pytest"},
            subagent_id="sa-1",
            task_index=0,
            task_count=1,
            goal="review delegate path",
            tool_count=1,
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert "🔀 Delegates" in adapter.sent[0][1]
        assert "terminal: pytest tests/test_delegate_progress.py" in adapter.sent[0][1]
        assert calls == []
        uninstall_delegate_monkeypatches(delegate_module)

    asyncio.run(run())


def test_delegate_monkeypatch_captures_args_before_original_callback_mutates(monkeypatch):
    async def run():
        adapter = EditableAdapter()
        plugin._renderer = None
        monkeypatch.setattr(
            plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        _on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        def original_builder(*args, **kwargs):
            _ = args, kwargs

            def original_cb(event_type, tool_name=None, preview=None, cb_args=None, **event_kwargs):
                _ = event_type, tool_name, preview, event_kwargs
                cb_args["command"] = "MUTATED COMMAND"

            return original_cb

        delegate_module = SimpleNamespace(_build_child_progress_callback=original_builder)
        uninstall_delegate_monkeypatches(delegate_module)
        assert install_delegate_monkeypatches(delegate_module) is True

        cb = delegate_module._build_child_progress_callback(
            0, "review delegate args", ParentAgent(), 1
        )
        cb(
            "subagent.tool",
            "terminal",
            "pytest tests/test_delegate_progress.py",
            {"command": "pytest tests/test_delegate_progress.py"},
            subagent_id="sa-copy",
            task_index=0,
            task_count=1,
            goal="review delegate args",
        )
        await asyncio.sleep(0.05)

        content = adapter.sent[0][1]
        assert "pytest tests/test_delegate_progress.py" in content
        assert "MUTATED COMMAND" not in content
        uninstall_delegate_monkeypatches(delegate_module)

    asyncio.run(run())


def test_delegate_monkeypatch_preserves_original_callback_and_flush(monkeypatch):
    async def run():
        adapter = EditableAdapter()
        plugin._renderer = None
        monkeypatch.setattr(
            plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        _on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        calls = []
        flushes = []

        def original_builder(*args, **kwargs):
            _ = args, kwargs

            def original_cb(event_type, tool_name=None, preview=None, cb_args=None, **event_kwargs):
                calls.append((event_type, tool_name, preview, cb_args, event_kwargs))

            original_cb._flush = lambda: flushes.append("flushed")
            return original_cb

        delegate_module = SimpleNamespace(_build_child_progress_callback=original_builder)
        uninstall_delegate_monkeypatches(delegate_module)
        assert install_delegate_monkeypatches(delegate_module) is True

        cb = delegate_module._build_child_progress_callback(
            task_index=1,
            goal="implement delegate UI",
            parent_agent=ParentAgent(),
            task_count=2,
        )
        assert hasattr(cb, "_flush")
        cb(
            "subagent.start",
            preview="implement delegate UI",
            subagent_id="sa-2",
            task_count=2,
        )
        cb._flush()
        await asyncio.sleep(0.05)

        assert calls[0][0] == "subagent.start"
        assert flushes == ["flushed"]
        assert adapter.sent
        assert "[2/2] → running · implement delegate UI" in adapter.sent[0][1]
        uninstall_delegate_monkeypatches(delegate_module)

    asyncio.run(run())


def test_delegate_monkeypatch_preserves_current_builder_identity_kwargs(monkeypatch):
    async def run():
        adapter = EditableAdapter()
        plugin._renderer = None
        monkeypatch.setattr(
            plugin,
            "_load_runtime_settings",
            lambda: load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {"show_model": True},
                    }
                }
            ),
        )
        _on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        def original_builder(*args, **kwargs):
            _ = args, kwargs
            return None

        delegate_module = SimpleNamespace(_build_child_progress_callback=original_builder)
        uninstall_delegate_monkeypatches(delegate_module)
        assert install_delegate_monkeypatches(delegate_module) is True

        cb = delegate_module._build_child_progress_callback(
            task_index=1,
            goal="check current Hermes delegate identity kwargs",
            parent_agent=ParentAgent(),
            task_count=3,
            subagent_id="current-sa",
            model="custom/current-model",
        )
        cb("subagent.start")
        await asyncio.sleep(0.05)

        content = adapter.sent[0][1]
        assert "[2/3] → running · check current Hermes delegate identity kwargs" in content
        assert "custom/current-model" in content
        uninstall_delegate_monkeypatches(delegate_module)

    asyncio.run(run())


def make_ctx(adapter):
    return SessionContext(
        "s1",
        "k1",
        "discord",
        "chat",
        None,
        adapter,
        asyncio.get_running_loop(),
        "live_tail",
        timestamp=False,
    )


def test_completed_delegate_cleanup_uses_default_five_second_ttl():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        import time

        now = time.time()
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-done",
                event_type="subagent.complete",
                goal="done delegate",
                summary="done summary",
                status="completed",
                created_at=now - 4,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-running",
                event_type="subagent.start",
                goal="running delegate",
                created_at=now - 60,
            ),
            force=True,
        )
        assert "sa-done" in renderer.sessions["s1"].delegate_branches
        assert "sa-running" in renderer.sessions["s1"].delegate_branches

        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "cleanup",
                event_type="cleanup",
                created_at=now + 2,
            ),
            force=True,
        )

        assert "sa-done" not in renderer.sessions["s1"].delegate_branches
        assert "sa-running" in renderer.sessions["s1"].delegate_branches
        assert "done delegate" not in adapter.edits[-1][2]
        assert "running delegate" in adapter.edits[-1][2]

    asyncio.run(run())


def test_delegate_thinking_updates_replace_fragmented_text():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {"thinking": "summary", "max_line_chars": 58},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-thinking",
                event_type="subagent.thinking",
                goal="review truncation",
                preview="I need to inspect the renderer",
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-thinking",
                event_type="subagent.thinking",
                goal="review truncation",
                preview="I need to inspect the renderer and verify the truncation boundary stays readable",
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert content.count("thinking:") == 1
        assert "thinking: I need to inspect the renderer and..." not in content
        assert "thinking: I need to inspect the renderer and verify..." in content

    asyncio.run(run())


def test_failed_delegate_cleanup_uses_default_five_second_ttl():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        import time

        now = time.time()
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-failed",
                event_type="subagent.failed",
                goal="failed delegate",
                summary="failed summary",
                status="failed",
                created_at=now - 6,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "cleanup",
                event_type="cleanup",
                created_at=now,
            ),
            force=True,
        )

        assert "sa-failed" not in renderer.sessions["s1"].delegate_branches
        latest = adapter.edits[-1][2] if adapter.edits else ""
        assert "failed delegate" not in latest

    asyncio.run(run())


def test_delegate_terminal_events_schedule_cleanup():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-done",
                event_type="subagent.complete",
                status="completed",
                summary="done",
            ),
            force=True,
        )

        branch = renderer.sessions["s1"].delegate_branches["sa-done"]
        assert branch.cleanup_task is not None
        assert not branch.cleanup_task.done()

    asyncio.run(run())


def test_completed_delegate_reuse_cancels_pending_cleanup(monkeypatch):
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "task-0",
                event_type="subagent.complete",
                status="completed",
                summary="old done",
            ),
            force=True,
        )
        branch = renderer.sessions["s1"].delegate_branches["task-0"]
        cleanup_task = asyncio.create_task(asyncio.sleep(60))
        branch.cleanup_task = cleanup_task

        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "task-0",
                event_type="subagent.start",
                goal="new delegate",
                status="running",
            ),
            force=True,
        )

        branch = renderer.sessions["s1"].delegate_branches["task-0"]
        assert branch.cleanup_task is None
        await asyncio.sleep(0)
        assert cleanup_task.cancelled()
        assert branch.completed_at == 0
        assert branch.completion_line == ""
        assert branch.completion_summary == ""
        assert branch.status == "running"

    asyncio.run(run())


def test_delegate_monkeypatch_is_idempotent_and_uninstall_restores_original():
    def original_builder():
        return "original"

    delegate_module = SimpleNamespace(_build_child_progress_callback=original_builder)
    uninstall_delegate_monkeypatches(delegate_module)

    assert install_delegate_monkeypatches(delegate_module) is True
    patched_once = delegate_module._build_child_progress_callback
    assert install_delegate_monkeypatches(delegate_module) is True
    assert delegate_module._build_child_progress_callback is patched_once

    assert uninstall_delegate_monkeypatches(delegate_module) is True
    assert delegate_module._build_child_progress_callback is original_builder
