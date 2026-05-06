import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.formatter import format_tool_line
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import ReasoningEvent, SessionContext, ToolEvent


class Result:
    def __init__(self, success=True, message_id=None, error=""):
        self.success = success
        self.message_id = message_id
        self.error = error


class SlowEditableAdapter:
    name = "slow"

    def __init__(self):
        self.sent = []
        self.edits = []
        self.next_id = 1

    async def send(self, chat_id, content, metadata=None):
        await asyncio.sleep(0.01)
        message_id = f"m{self.next_id}"
        self.next_id += 1
        self.sent.append((chat_id, content, metadata))
        return Result(True, message_id)

    async def edit_message(self, chat_id, message_id, content):
        await asyncio.sleep(0.01)
        self.edits.append((chat_id, message_id, content))
        return Result(True, message_id)


def test_renderer_redacts_raw_tool_event_at_final_boundary():
    async def run():
        adapter = SlowEditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "💻 terminal: EXAMPLE_TOKEN=tok-secret"), force=True
        )

        assert "tok-secret" not in adapter.sent[0][1]
        assert "[redacted_env]" in adapter.sent[0][1]

    asyncio.run(run())


def test_todo_preview_fallback_is_redacted():
    line = format_tool_line("todo", {}, preview="EXAMPLE_TOKEN=tok-secret", preview_length=120)

    assert "tok-secret" not in line
    assert "[redacted_env]" in line


def test_concurrent_events_do_not_send_duplicate_initial_messages():
    async def run():
        adapter = SlowEditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        await asyncio.gather(
            *(
                renderer.handle_event(ToolEvent("s1", "k1", "discord", f"tool {i}"), force=True)
                for i in range(5)
            )
        )

        assert len(adapter.sent) == 1
        assert adapter.edits[-1][2] == "🧰 Tools\ntool 2\ntool 3\ntool 4"

    asyncio.run(run())


def test_finalize_resets_turn_state_before_next_turn():
    async def run():
        adapter = SlowEditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "reasoning": {"min_update_chars": 1},
                    }
                }
            )
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            ReasoningEvent("s1", "k1", "discord", "old thought"), force=True
        )
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "old tool"), force=True)
        await renderer.finalize(session_id="s1")
        next_ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(next_ctx)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "new tool"), force=True)

        assert len(adapter.sent) == 2
        latest = adapter.sent[-1][1]
        assert latest == "🧰 Tools\nnew tool"
        assert "old thought" not in latest
        assert "old tool" not in latest

    asyncio.run(run())


def test_reasoning_updates_even_when_tail_is_at_max_chars():
    async def run():
        adapter = SlowEditableAdapter()
        settings = load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False},
                    "reasoning": {"max_chars": 10, "min_update_chars": 3},
                }
            }
        )
        renderer = ProgressRenderer(settings)
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        await renderer.handle_event(ReasoningEvent("s1", "k1", "discord", "abcdefghij"), force=True)
        renderer.sessions["s1"].last_render_at -= 2
        await renderer.handle_event(ReasoningEvent("s1", "k1", "discord", "klm"))

        assert adapter.edits[-1][2] == "💭 Reasoning\ndefghijklm"

    asyncio.run(run())
