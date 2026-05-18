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
from hermes_progress_tail.state import AssistantEvent, BackgroundJobEvent, SessionContext, ToolEvent


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


def make_ctx(adapter, *, platform="discord", strategy="live_tail", code_fence="off"):
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
        code_fence=code_fence,
    )


def test_background_job_renders_head_tail_completion_and_survives_finalize():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"code_fence": "off"},
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


def test_background_job_secret_output_is_redacted_and_ansi_is_stripped():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"code_fence": "off"},
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
                        "renderer": {"code_fence": "off"},
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
                    "/home/zhafron/.hushlogin file.\n"
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


def test_compression_status_clears_when_real_progress_resumes():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "renderer": {"mode": "focused"},
                        "tools": {"timestamp": False},
                        "assistant": {"min_update_chars": 1},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            AssistantEvent(
                ctx.session_id,
                ctx.session_key,
                ctx.platform,
                "📦 Preflight compression: ~204,662 tokens >= 204,000 threshold. This may take a moment.",
                transient=True,
            ),
            force=True,
        )
        assert "Preflight compression" in adapter.sent[-1][1]

        await renderer.handle_event(
            ToolEvent(ctx.session_id, ctx.session_key, ctx.platform, "→ terminal: pytest -q"),
            force=True,
        )

        latest = adapter.edits[-1][2] if adapter.edits else adapter.sent[-1][1]
        assert "Preflight compression" not in latest
        assert "terminal: pytest -q" in latest

    asyncio.run(run())


def test_compression_status_falls_back_to_native_when_not_captured(monkeypatch):
    import hermes_progress_tail.plugin as plugin

    monkeypatch.setattr(plugin, "on_compression_status_from_agent", lambda agent, text: False)

    class FakeAgent:
        def _emit_status(self, text):
            return f"native:{text}"

    install_compression_status_monkeypatch(FakeAgent)

    try:
        agent = FakeAgent()
        result = agent._emit_status("🗜️ Compacting context — summarizing earlier conversation")
    finally:
        uninstall_compression_status_monkeypatch(FakeAgent)

    assert result == "native:🗜️ Compacting context — summarizing earlier conversation"


def test_code_fence_auto_wraps_discord_but_not_telegram_or_webhook():
    async def run():
        discord_adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        discord_ctx = make_ctx(discord_adapter, platform="discord", code_fence="auto")
        renderer.register_context(discord_ctx)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "tool one"), force=True)
        assert discord_adapter.sent[0][1].startswith("```\n")
        assert discord_adapter.sent[0][1].endswith("\n```")

        telegram_adapter = EditableAdapter()
        telegram_ctx = SessionContext(
            "s3",
            "k3",
            "telegram",
            "chat",
            None,
            telegram_adapter,
            asyncio.get_running_loop(),
            "live_tail",
            timestamp=False,
            code_fence="auto",
        )
        renderer.register_context(telegram_ctx)
        await renderer.handle_event(ToolEvent("s3", "k3", "telegram", "tool three"), force=True)
        assert not telegram_adapter.sent[0][1].startswith("```")

        webhook_adapter = EditableAdapter()
        webhook_ctx = SessionContext(
            "s2",
            "k2",
            "webhook",
            "chat",
            None,
            webhook_adapter,
            asyncio.get_running_loop(),
            "live_tail",
            timestamp=False,
            code_fence="auto",
        )
        renderer.register_context(webhook_ctx)
        await renderer.handle_event(ToolEvent("s2", "k2", "webhook", "tool two"), force=True)
        assert not webhook_adapter.sent[0][1].startswith("```")

    asyncio.run(run())


def test_code_fence_escapes_internal_fences_for_supported_platforms():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"code_fence": "auto"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="discord", code_fence="auto")
        renderer.register_context(ctx)
        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "prefix ``` unsafe"), force=True
        )
        content = adapter.sent[0][1]
        assert content.startswith("```\n")
        assert content.endswith("\n```")
        assert "`\u200b`` unsafe" in content

    asyncio.run(run())


def test_telegram_code_fence_on_is_ignored_and_respects_message_limit():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"code_fence": "auto"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram", code_fence="on")
        renderer.register_context(ctx)
        huge = "prefix ``` unsafe\n" + ("x" * 5000)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", huge), force=True)
        content = adapter.sent[0][1]
        assert not content.startswith("```")
        assert not content.endswith("\n```")
        assert "``` unsafe" in content
        assert len(content) <= 4096

    asyncio.run(run())
