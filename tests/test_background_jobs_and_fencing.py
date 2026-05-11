import asyncio
import time

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
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
                output="\x1b[31mOPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\x1b[0m",
            ),
            force=True,
        )
        content = adapter.sent[0][1]
        assert "\x1b" not in content
        assert "sk-abcdefghijklmnopqrstuvwxyz" not in content
        assert "[redacted" in content

    asyncio.run(run())


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
