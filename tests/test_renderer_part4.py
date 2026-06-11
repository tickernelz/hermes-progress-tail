import asyncio
import time

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import (
    SessionContext,
    ToolEvent,
)


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


class NoEditAdapter:
    name = "noedit"

    def __init__(self):
        self.sent = []

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        return Result(True, f"m{len(self.sent)}")


class FailingEditAdapter(EditableAdapter):
    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return Result(False, message_id, "edit not supported")


class SequenceEditAdapter(EditableAdapter):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)
        self.deleted = []

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        if self.errors:
            return Result(False, message_id, self.errors.pop(0))
        return Result(True, message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        return True


class SequenceSendAdapter(EditableAdapter):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        if self.errors:
            return Result(False, None, self.errors.pop(0))
        message_id = f"m{self.next_id}"
        self.next_id += 1
        return Result(True, message_id)


class ExceptionSendAdapter(SequenceSendAdapter):
    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        if self.errors:
            raise RuntimeError(self.errors.pop(0))
        message_id = f"m{self.next_id}"
        self.next_id += 1
        return Result(True, message_id)


def make_ctx(adapter, *, strategy="live_tail", timestamp=False, platform="discord"):
    return SessionContext(
        "s1",
        "k1",
        platform,
        "chat",
        "thread",
        adapter,
        asyncio.get_running_loop(),
        strategy,
        timestamp=timestamp,
    )


def test_permanent_initial_send_failure_still_disables_context():
    async def run():
        adapter = SequenceSendAdapter(["forbidden: bot was blocked by the user"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is True
        assert ctx.last_error == "forbidden: bot was blocked by the user"

    asyncio.run(run())


def test_bad_gateway_is_classified_as_transient_edit_error():
    assert ProgressRenderer._classify_edit_error("Bad Gateway") == "transient"
    assert ProgressRenderer._classify_edit_error("502 Bad Gateway") == "transient"
    assert ProgressRenderer._classify_edit_error("Gateway Timeout 504") == "transient"


def test_edit_message_lost_recovers_with_exactly_one_new_progress_bubble():
    async def run():
        adapter = SequenceEditAdapter(["message to edit not found"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "one"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "two"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "three"), force=True)

        assert len(adapter.sent) == 2
        assert adapter.sent[-1][1] == "▰ 🧰 Tools\none\ntwo"
        assert adapter.edits[-1][1] == "m2"
        assert adapter.edits[-1][2] == "▰ 🧰 Tools\none\ntwo\nthree"
        assert ctx.strategy == "live_tail"

    asyncio.run(run())


def test_repeated_message_lost_downgrades_to_throttled_snapshot():
    async def run():
        adapter = SequenceEditAdapter(
            [
                "message to edit not found",
                "message to edit not found",
            ]
        )
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "no_edit": {"min_new_events": 1, "max_snapshots_per_turn": 2},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "one"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "two"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "three"), force=True)
        assert ctx.strategy == "snapshot"
        assert ctx.edit_state == "message_lost"
        assert len(adapter.sent) == 2

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "four"))
        assert len(adapter.sent) == 2

    asyncio.run(run())


def test_edit_too_long_downgrades_to_snapshot_instead_of_sending_live_spam():
    async def run():
        adapter = SequenceEditAdapter(["message is too long"])
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "no_edit": {"min_new_events": 1},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "one"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "two"), force=True)

        assert ctx.strategy == "snapshot"
        assert ctx.edit_state == "too_long"
        assert len(adapter.sent) == 2
        assert adapter.sent[-1][1].startswith("Progress tail — latest 2 tools")

    asyncio.run(run())


def test_telegram_live_and_snapshot_messages_are_capped():
    async def run():
        adapter = SequenceEditAdapter(["message is too long"])
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "reasoning": {"max_chars": 8000},
                        "no_edit": {"min_new_events": 1},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)
        huge = "x" * 5000

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", huge), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", huge), force=True)

        assert len(adapter.sent[0][1]) <= 4096
        assert len(adapter.sent[-1][1]) <= 4096

    asyncio.run(run())


def test_finalize_bypasses_backoff_and_cancels_delayed_flush_after_interrupt_like_reset():
    async def run():
        adapter = SequenceEditAdapter(["retry_after=5", "retry_after=5"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "one"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "two"), force=True)

        first_task = ctx.delayed_flush_task
        assert first_task is not None
        assert not first_task.done()

        ctx.edit_backoff_until = time.monotonic() + 5
        await renderer.finalize(session_id="s1")
        await asyncio.sleep(0)
        assert ctx.delayed_flush_task is None
        assert first_task.cancelled() or first_task.done()
        assert len(adapter.sent) == 1
        assert adapter.edits[-1][2] == "▰ 🧰 Tools\none\ntwo"

    asyncio.run(run())


def test_parallel_sessions_do_not_cross_edit():
    async def run():
        adapter1 = EditableAdapter()
        adapter2 = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        renderer.register_context(
            SessionContext(
                "s1",
                "k1",
                "discord",
                "chat1",
                None,
                adapter1,
                asyncio.get_running_loop(),
                "live_tail",
            )
        )
        renderer.register_context(
            SessionContext(
                "s2",
                "k2",
                "discord",
                "chat2",
                None,
                adapter2,
                asyncio.get_running_loop(),
                "live_tail",
            )
        )

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "one"))
        await renderer.handle_event(ToolEvent("s2", "k2", "discord", "two"))

        assert adapter1.sent[0][0] == "chat1"
        assert adapter1.sent[0][1] == "▰ 🧰 Tools\none"
        assert adapter2.sent[0][0] == "chat2"
        assert adapter2.sent[0][1] == "▰ 🧰 Tools\ntwo"

    asyncio.run(run())


def test_tool_completion_updates_existing_line_when_tool_call_id_matches():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {"progress_tail": {"tools": {"timestamp": False, "show_completed": True}}}
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "terminal: pytest · running", tool_call_id="call-1"),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "discord",
                "✅ terminal: pytest · done · 2.1s",
                tool_call_id="call-1",
                replace_existing=True,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "terminal: pytest · running" not in content
        assert "✅ terminal: pytest · done · 2.1s" in content

    asyncio.run(run())


def test_tool_completion_replaces_running_line_by_fingerprint_without_tool_call_id():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {"progress_tail": {"tools": {"timestamp": False, "show_completed": True}}}
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "patch: installer.py replace x → y · running"),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "discord",
                "✅ patch: installer.py replace x → y · done · 1.3s",
                replace_existing=True,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "patch: installer.py replace x → y · running" not in content
        assert "✅ patch: installer.py replace x → y · done · 1.3s" in content
        assert len(ctx.tool_lines) == 1

    asyncio.run(run())


def test_tool_completion_replaces_emoji_running_line_by_fingerprint_without_tool_call_id():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {"progress_tail": {"tools": {"timestamp": False, "show_completed": True}}}
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "💻 terminal: pytest tests/a.py · running"),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "discord",
                "✅ 💻 terminal: pytest tests/a.py · done · 1.3s",
                replace_existing=True,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "💻 terminal: pytest tests/a.py · running" not in content
        assert "✅ 💻 terminal: pytest tests/a.py · done · 1.3s" in content
        assert len(ctx.tool_lines) == 1

    asyncio.run(run())


def test_tool_replacement_without_terminal_status_remains_running():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "show_completed": True},
                        "renderer": {"mode": "focused", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "terminal: pytest · running",
                tool_call_id="call-1",
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "terminal: pytest tests/test_renderer.py · running",
                tool_call_id="call-1",
                replace_existing=True,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "**State** 1 tools · 0 done · 1 running" in content
        assert ctx.tool_completed_count == 0
        assert "call-1" in ctx.active_tool_lines

    asyncio.run(run())


def test_terminal_tool_completion_clears_active_tracking():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {"progress_tail": {"tools": {"timestamp": False, "show_completed": True}}}
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "terminal: pytest · running", tool_call_id="call-1"),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "discord",
                "✅ terminal: pytest · done · 2.1s",
                tool_call_id="call-1",
                replace_existing=True,
            ),
            force=True,
        )

        assert ctx.tool_completed_count == 1
        assert ctx.active_tool_lines == {}
        assert ctx.active_tool_fingerprints == {}

    asyncio.run(run())


def test_tool_replacement_changing_fingerprint_does_not_double_count_completion():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "show_completed": True},
                        "renderer": {"mode": "focused", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "terminal: pytest · running", tool_call_id="call-1"),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "terminal: pytest tests/test_renderer.py · running",
                tool_call_id="call-1",
                replace_existing=True,
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "✅ terminal: pytest tests/test_renderer.py --rerun · done · 1s",
                tool_call_id="call-1",
                replace_existing=True,
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1", "k1", "telegram", "terminal: ruff check · running", tool_call_id="call-2"
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert ctx.tool_started_count == 2
        assert ctx.tool_completed_count == 1
        assert "**State** 2 tools · 1 done · 1 running" in content
        assert "terminal: pytest" not in ctx.active_tool_fingerprints

    asyncio.run(run())
