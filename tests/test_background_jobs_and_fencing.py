import asyncio
import time
import types

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.monkeypatches import (
    install_compression_status_monkeypatch,
    install_process_notification_monkeypatch,
    uninstall_compression_status_monkeypatch,
    uninstall_process_notification_monkeypatch,
)
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.runtime import plugin
from hermes_progress_tail.state import BackgroundJobEvent, SessionContext, ToolEvent


class Result:
    def __init__(self, success=True, message_id=None, error=""):
        self.success = success
        self.message_id = message_id
        self.error = error


class EditableAdapter:
    name = "editable"

    def __init__(self):
        self.sent = []
        self.edits = []
        self.next_id = 1

    async def send(self, chat_id, content, metadata=None):
        message_id = f"m{self.next_id}"
        self.next_id += 1
        self.sent.append((chat_id, content, metadata))
        return Result(True, message_id)

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return Result(True, message_id)


def make_ctx(adapter, *, platform="discord", strategy="live_tail"):
    return SessionContext(
        "s1",
        "k1",
        platform,
        "chat",
        None,
        adapter,
        asyncio.get_running_loop(),
        strategy,
        timestamp=False,
    )


def test_background_job_renders_head_tail_completion_and_survives_finalize():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "background_jobs": {
                            "head_lines": 1,
                            "tail_lines": 2,
                            "completed_ttl_seconds": 180,
                        },
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "discord",
                "proc_abc123",
                event_type="started",
                command="pytest -q",
                created_at=time.time() - 60,
            ),
            force=True,
        )
        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "discord",
                "proc_abc123",
                event_type="output",
                output="collecting tests\ntest_a passed\ntest_b passed",
            ),
            force=True,
        )
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "normal tool"), force=True)
        content = adapter.edits[-1][2]
        assert "🖥 Background Jobs" in content
        assert "proc_abc123 · pytest -q" in content
        assert "start: collecting tests" in content
        assert "tail: test_a passed" in content
        assert "test_b passed" in content

        await renderer.finalize(session_id="s1")
        assert "proc_abc123" in renderer.sessions["s1"].background_jobs
        assert list(renderer.sessions["s1"].tool_lines) == []
        assert "🧰 Tools" not in adapter.edits[-1][2]

        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "discord",
                "proc_abc123",
                event_type="completed",
                command="pytest -q",
                output="collecting tests\ntest_a passed\n312 passed in 10s",
                exited=True,
                exit_code=0,
            ),
            force=True,
        )
        completed = adapter.edits[-1][2]
        assert "✅ proc_abc123" in completed
        assert "exit 0" in completed
        assert "end: test_a passed" in completed
        assert "312 passed in 10s" in completed

    asyncio.run(run())


def test_completed_background_job_cleanup_uses_default_five_second_ttl():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        now = time.time()
        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "discord",
                "proc_done",
                event_type="completed",
                command="pytest -q",
                output="2 passed",
                exited=True,
                exit_code=0,
                created_at=now - 4,
            ),
            force=True,
        )
        assert "proc_done" in adapter.sent[-1][1]
        assert "proc_done" in renderer.sessions["s1"].background_jobs

        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "discord",
                "proc_running",
                event_type="started",
                command="sleep 999",
                created_at=now - 60,
            ),
            force=True,
        )
        assert "proc_done" in renderer.sessions["s1"].background_jobs
        assert "proc_running" in renderer.sessions["s1"].background_jobs

        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "discord",
                "proc_cleanup_tick",
                event_type="cleanup",
                created_at=now + 2,
            ),
            force=True,
        )
        assert "proc_done" not in renderer.sessions["s1"].background_jobs
        assert "proc_running" in renderer.sessions["s1"].background_jobs
        assert "proc_done" not in adapter.edits[-1][2]
        assert "proc_running" in adapter.edits[-1][2]

    asyncio.run(run())


def test_background_job_secret_output_is_redacted_and_ansi_is_stripped():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "discord",
                "proc_secret",
                command="python script.py",
                output="\x1b[31mOPENAI_API_KEY=sk-abc...3456\x1b[0m",
            ),
            force=True,
        )
        content = adapter.sent[0][1]
        assert "\x1b" not in content
        assert "sk-abc...wxyz" not in content
        assert "[redacted" in content

    asyncio.run(run())


def test_background_job_filters_wsl_login_banner_noise():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "background_jobs": {"head_lines": 2, "tail_lines": 3},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)
        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "telegram",
                "proc_wsl",
                command="python - <<'PY'",
                output=(
                    "Welcome to Ubuntu 26.04 LTS "
                    "(GNU/Linux 6.6.114.1-microsoft-standard-WSL2 x86_64)\n"
                    "\n"
                    " * Documentation:  https://docs.ubuntu.com\n"
                    " * Management:     https://landscape.canonical.com\n"
                    " * Support:        https://ubuntu.com/pro\n"
                    "\n"
                    "This message is shown once a day. To disable it please create the\n"
                    "/home/example/.hushlogin file.\n"
                    "background smoke tick 0\n"
                    "background smoke tick 1\n"
                    "background smoke done\n"
                ),
                exited=True,
                exit_code=0,
            ),
            force=True,
        )
        content = adapter.sent[0][1]
        assert "Welcome to Ubuntu" not in content
        assert "docs.ubuntu.com" not in content
        assert "landscape.canonical.com" not in content
        assert "ubuntu.com/pro" not in content
        assert ".hushlogin" not in content
        assert "background smoke tick 0" in content
        assert "background smoke done" in content

    asyncio.run(run())


def test_terminal_background_events_schedule_terminal_cleanup(monkeypatch):
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        monkeypatch.setattr(plugin, "_renderer", renderer)
        cleanup_calls = []
        original_cleanup = plugin._schedule_background_job_cleanup

        def track_cleanup(ctx, process_id):
            cleanup_calls.append(process_id)
            return original_cleanup(ctx, process_id)

        monkeypatch.setattr(plugin, "_schedule_background_job_cleanup", track_cleanup)
        for process_id, event_type in (
            ("proc_completed", "completed"),
            ("proc_killed", "killed"),
            ("proc_lost", "lost"),
        ):
            assert plugin._schedule_render(
                ctx,
                BackgroundJobEvent(
                    "s1",
                    "k1",
                    "discord",
                    process_id,
                    event_type=event_type,
                    exited=True,
                ),
                force=True,
            )
        for _ in range(10):
            await asyncio.sleep(0.01)
            if len(cleanup_calls) >= 3:
                break
        assert cleanup_calls == ["proc_completed", "proc_killed", "proc_lost"]

    asyncio.run(run())


def test_background_terminal_states_are_pruned_after_ttl_but_running_is_preserved():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        now = time.time()
        for process_id, event_type in (
            ("proc_failed", "completed"),
            ("proc_killed", "killed"),
            ("proc_lost", "lost"),
        ):
            await renderer.handle_event(
                BackgroundJobEvent(
                    "s1",
                    "k1",
                    "discord",
                    process_id,
                    event_type=event_type,
                    command="pytest -q",
                    output="done",
                    exited=True,
                    exit_code=1 if process_id == "proc_failed" else None,
                    created_at=now - 6,
                ),
                force=True,
            )
        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "discord",
                "proc_running",
                event_type="started",
                command="sleep 999",
                created_at=now - 60,
            ),
            force=True,
        )

        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "discord",
                "proc_cleanup_tick",
                event_type="cleanup",
                created_at=now,
            ),
            force=True,
        )

        remaining = renderer.sessions["s1"].background_jobs
        assert "proc_failed" not in remaining
        assert "proc_killed" not in remaining
        assert "proc_lost" not in remaining
        assert "proc_running" in remaining
        assert "proc_running" in adapter.edits[-1][2]

    asyncio.run(run())


def test_terminal_background_immediate_completed_result_does_not_stay_running(monkeypatch):
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        monkeypatch.setattr(plugin, "_renderer", renderer)
        monkeypatch.setattr(
            plugin,
            "_context_for_non_background_thread",
            lambda renderer, session_id="", session_key="": ctx,
        )
        monkeypatch.setattr(plugin, "_suppress_native_background_notify", lambda process_id: None)
        poll_calls = []
        cleanup_calls = []
        monkeypatch.setattr(
            plugin,
            "_schedule_background_job_poll",
            lambda ctx, process_id: poll_calls.append(process_id),
        )
        original_cleanup = plugin._schedule_background_job_cleanup

        def track_cleanup(ctx, process_id):
            cleanup_calls.append(process_id)
            return original_cleanup(ctx, process_id)

        monkeypatch.setattr(plugin, "_schedule_background_job_cleanup", track_cleanup)

        plugin._on_post_tool_call(
            "terminal",
            args={"background": True, "command": "pytest -q"},
            result='{"session_id":"proc_done","exited":true,"exit_code":0,"output":"2 passed"}',
            task_id="k1",
            session_id="s1",
        )
        for _ in range(10):
            await asyncio.sleep(0.01)
            if "proc_done" in renderer.sessions["s1"].background_jobs:
                break

        job = renderer.sessions["s1"].background_jobs["proc_done"]
        assert job.status == "completed"
        assert job.exit_code == 0
        assert poll_calls == []
        assert cleanup_calls == ["proc_done"]
        assert "✅ proc_done" in adapter.sent[-1][1]
        assert "exit 0" in adapter.sent[-1][1]
        assert "2 passed" in adapter.sent[-1][1]

    asyncio.run(run())


def test_process_completion_notifications_are_suppressed_when_progress_tail_owns_them():
    calls = []

    module = types.SimpleNamespace(
        format_process_notification=lambda evt: calls.append(evt) or "native notification",
        _hermes_progress_tail_process_notification_patched=False,
    )

    install_process_notification_monkeypatch(module)

    try:
        text = module.format_process_notification(
            {
                "type": "completion",
                "session_id": "proc_bg",
                "command": "pytest -q",
                "exit_code": 0,
                "output": "312 passed",
            }
        )
    finally:
        uninstall_process_notification_monkeypatch(module)

    assert text is None
    assert calls == []


def test_process_failure_notifications_are_compacted_not_dumped():
    module = types.SimpleNamespace(
        format_process_notification=lambda evt: "native full output",
        _hermes_progress_tail_process_notification_patched=False,
    )

    install_process_notification_monkeypatch(module)

    try:
        text = module.format_process_notification(
            {
                "type": "completion",
                "session_id": "proc_fail",
                "command": "pytest -q",
                "exit_code": 1,
                "output": "line one\nline two\nline three\nline four",
            }
        )
    finally:
        uninstall_process_notification_monkeypatch(module)

    assert text is not None
    assert "proc_fail" in text
    assert "exit 1" in text
    assert "line four" in text
    assert "line one" not in text
    assert "native full output" not in text


def test_compression_status_is_suppressed_when_progress_tail_captures_it(monkeypatch):
    import hermes_progress_tail.plugin as plugin

    captured = []
    monkeypatch.setattr(
        plugin,
        "on_compression_status_from_agent",
        lambda agent, text: captured.append(text) or True,
    )

    class FakeAgent:
        def _emit_status(self, text):
            return f"native:{text}"

    install_compression_status_monkeypatch(FakeAgent)

    try:
        agent = FakeAgent()
        result = agent._emit_status(
            "🗜️ Compacting context — summarizing earlier conversation so I can continue..."
        )
    finally:
        uninstall_compression_status_monkeypatch(FakeAgent)

    assert result is None
    assert captured == [
        "🗜️ Compacting context — summarizing earlier conversation so I can continue..."
    ]


def test_preflight_compression_status_is_suppressed_when_progress_tail_captures_it(monkeypatch):
    import hermes_progress_tail.plugin as plugin

    captured = []
    monkeypatch.setattr(
        plugin,
        "on_compression_status_from_agent",
        lambda agent, text: captured.append(text) or True,
    )

    class FakeAgent:
        def _emit_status(self, text):
            return f"native:{text}"

    install_compression_status_monkeypatch(FakeAgent)

    try:
        agent = FakeAgent()
        result = agent._emit_status(
            "📦 Preflight compression: ~204,662 tokens >= 204,000 threshold. This may take a moment."
        )
    finally:
        uninstall_compression_status_monkeypatch(FakeAgent)

    assert result is None
    assert captured == [
        "📦 Preflight compression: ~204,662 tokens >= 204,000 threshold. This may take a moment."
    ]
