import asyncio  # noqa: I001 - keep shared helpers in one import
import time

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.formatter import extract_todo_items, format_tool_line
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import (
    AssistantEvent,
    ReasoningEvent,
    SessionContext,
    ToolEvent,
)
from tests.support.rendering import (
    EditableAdapter,
    ExceptionSendAdapter,
    FailingEditAdapter,
    NoEditAdapter,
    SequenceEditAdapter,
    SequenceSendAdapter,
    make_live_context as make_ctx,
)


def test_focused_telegram_plain_sanitizer_preserves_code_and_paths():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "assistant": {"min_update_chars": 1},
                        "reasoning": {"min_update_chars": 1},
                        "renderer": {"mode": "focused", "density": "verbose", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(
            AssistantEvent(
                "s1",
                "k1",
                "telegram",
                "**Checking formatter**\nUse `path/to/file_name.py` and keep snake_case intact.",
            ),
            force=True,
        )
        await renderer.handle_event(
            ReasoningEvent(
                "s1",
                "k1",
                "telegram",
                "## Inspecting Markdown\n__Do not__ break `/tmp/a_b/file.py` or `foo_bar`.",
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "Checking formatter" in content
        assert "Inspecting Markdown" in content
        assert "**Checking formatter**" in content
        assert "Inspecting Markdown" in content
        assert "__Do not__" in content
        assert "`path/to/file_name.py`" in content
        assert "`/tmp/a_b/file.py`" in content
        assert "foo_bar" in content

    asyncio.run(run())


def test_live_tail_keeps_latest_three_and_edits_one_message():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        for i in range(5):
            await renderer.handle_event(ToolEvent("s1", "k1", "discord", f"tool {i}"), force=True)

        assert len(adapter.sent) == 1
        assert adapter.sent[0][2] == {"thread_id": "thread"}
        assert adapter.edits[-1][2] == "▰ 🧰 Tools\ntool 2\ntool 3\ntool 4"

    asyncio.run(run())


def test_live_tail_finalizes_latest_lines_after_throttled_events():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        for i in range(5):
            await renderer.handle_event(ToolEvent("s1", "k1", "discord", f"tool {i}"))

        assert adapter.sent[0][1] == "▰ 🧰 Tools\ntool 0"
        assert adapter.edits == []
        await renderer.finalize(session_id="s1")
        assert adapter.edits[-1][2] == "▰ 🧰 Tools\ntool 2\ntool 3\ntool 4"

    asyncio.run(run())


def test_tool_tail_adds_compact_event_timestamp():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(load_settings({}))
        ctx = SessionContext(
            "s1",
            "k1",
            "discord",
            "chat",
            None,
            adapter,
            asyncio.get_running_loop(),
            "live_tail",
            timestamp=True,
            timestamp_format="%M:%S",
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "terminal: npm test", created_at=0),
            force=True,
        )

        assert adapter.sent[0][1] == "▰ 🧰 Tools\n[00:00] terminal: npm test"

    asyncio.run(run())


def test_sticky_todo_survives_latest_tool_tail_and_resets_on_finalize():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        todo_args = {
            "todos": [
                {"content": "inspect repo", "status": "completed"},
                {"content": "implement sticky todo", "status": "in_progress"},
                {"content": "write tests", "status": "pending"},
                {"content": "push tag", "status": "pending"},
            ]
        }
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "discord",
                format_tool_line("todo", todo_args),
                tool_name="todo",
                todo_items=extract_todo_items(todo_args),
                created_at=0,
            ),
            force=True,
        )
        for i in range(5):
            await renderer.handle_event(ToolEvent("s1", "k1", "discord", f"tool {i}"), force=True)

        content = adapter.edits[-1][2]
        assert "▰ 📋 Todo" in content
        assert "🔄 in progress (1): implement sticky todo" in content
        assert "⏳ pending (2): write tests, push tag" in content
        assert "✅ done (1): inspect repo" in content
        assert "📋 todo:" not in content
        assert "tool 2\ntool 3\ntool 4" in content

        await renderer.finalize(session_id="s1")
        assert renderer.sessions["s1"].todo_items == ()

    asyncio.run(run())


def test_plain_style_removes_section_emojis():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        todo_args = {"todos": [{"content": "ship clean UX", "status": "in_progress"}]}

        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "discord",
                format_tool_line("todo", todo_args),
                tool_name="todo",
                todo_items=extract_todo_items(todo_args),
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "terminal: pytest"), force=True
        )

        content = adapter.edits[-1][2]
        assert "Todo" in content
        assert "Tools" in content
        assert "in progress (1): ship clean UX" in content
        assert "▰ 📋 Todo" not in content
        assert "🔄" not in content
        assert "🧰 Tools" not in content

    asyncio.run(run())


def test_todo_tool_line_can_be_kept_when_configured():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "todo": {"hide_tool_line": False},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        todo_args = {"todos": [{"content": "keep line", "status": "in_progress"}]}

        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "discord",
                format_tool_line("todo", todo_args),
                tool_name="todo",
                todo_items=extract_todo_items(todo_args),
            ),
            force=True,
        )

        assert "▰ 📋 Todo" in adapter.sent[0][1]
        assert "📋 todo:" in adapter.sent[0][1]

    asyncio.run(run())


def test_snapshot_strategy_does_not_spam_until_threshold():
    async def run():
        adapter = NoEditAdapter()
        settings = load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False},
                    "no_edit": {"interval_seconds": 30, "min_new_events": 3},
                }
            }
        )
        renderer = ProgressRenderer(settings)
        ctx = SessionContext(
            "s1", "k1", "signal", "chat", None, adapter, asyncio.get_running_loop(), "snapshot"
        )
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "signal", "one"))
        await renderer.handle_event(ToolEvent("s1", "k1", "signal", "two"))
        assert adapter.sent == []

        await renderer.handle_event(ToolEvent("s1", "k1", "signal", "three"), force=True)
        assert len(adapter.sent) == 1
        assert "latest 3" in adapter.sent[0][1]
        assert "one\ntwo\nthree" in adapter.sent[0][1]

    asyncio.run(run())


def test_edit_unsupported_failure_downgrades_to_snapshot():
    async def run():
        adapter = FailingEditAdapter()
        settings = load_settings(
            {"progress_tail": {"tools": {"timestamp": False}, "no_edit": {"min_new_events": 1}}}
        )
        renderer = ProgressRenderer(settings)
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "one"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "two"), force=True)

        assert renderer.sessions["s1"].strategy == "snapshot"
        assert len(adapter.sent) == 2

    asyncio.run(run())


def test_method_not_found_is_unsupported_not_message_lost_recovery():
    async def run():
        adapter = SequenceEditAdapter(["edit_message method not found"])
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
        assert ctx.edit_state == "unsupported"
        assert ctx.edit_recovery_sends == 0
        assert len(adapter.sent) == 2

    asyncio.run(run())


def test_edit_transient_failure_backs_off_without_sending_new_message():
    async def run():
        adapter = SequenceEditAdapter(["flood_control:5"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "one"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "two"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "three"), force=True)

        assert len(adapter.sent) == 1
        assert len(adapter.edits) == 1
        assert ctx.strategy == "live_tail"
        assert ctx.can_edit is True
        assert ctx.edit_state == "rate_limited"
        assert ctx.edit_backoff_until > 0

        ctx.edit_backoff_until = 0
        await renderer.finalize(session_id="s1")
        assert len(adapter.sent) == 1
        assert adapter.edits[-1][2] == "▰ 🧰 Tools\none\ntwo\nthree"

    asyncio.run(run())


def test_edit_timeout_failure_backs_off_without_sending_new_message():
    async def run():
        adapter = SequenceEditAdapter(["Timed out while editing message"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "one"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "two"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "three"), force=True)

        assert len(adapter.sent) == 1
        assert len(adapter.edits) == 1
        assert ctx.edit_state == "transient"
        assert ctx.edit_backoff_until > 0

        ctx.edit_backoff_until = 0
        await renderer.finalize(session_id="s1")
        assert len(adapter.sent) == 1
        assert adapter.edits[-1][2] == "▰ 🧰 Tools\none\ntwo\nthree"

    asyncio.run(run())


def test_initial_send_bad_gateway_backs_off_without_disabling_context():
    async def run():
        adapter = SequenceSendAdapter(["Bad Gateway"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.message_id is None
        assert ctx.edit_state == "transient"
        assert ctx.edit_backoff_until > 0
        assert len(adapter.sent) == 1

        ctx.edit_backoff_until = 0
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "two"), force=True)

        assert ctx.disabled is False
        assert ctx.message_id == "m1"
        assert len(adapter.sent) == 2
        assert adapter.sent[-1][1] == "▰ 🧰 Tools\none\ntwo"

    asyncio.run(run())


def test_initial_send_exception_bad_gateway_backs_off_without_disabling_context():
    async def run():
        adapter = ExceptionSendAdapter(["Bad Gateway"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.message_id is None
        assert ctx.edit_state == "transient"
        assert ctx.edit_backoff_until > 0
        assert len(adapter.sent) == 1

        ctx.edit_backoff_until = 0
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "two"), force=True)

        assert ctx.disabled is False
        assert ctx.message_id == "m1"
        assert len(adapter.sent) == 2
        assert adapter.sent[-1][1] == "▰ 🧰 Tools\none\ntwo"

    asyncio.run(run())


def test_initial_send_flood_control_uses_backoff_without_disabling_context():
    async def run():
        adapter = SequenceSendAdapter(["flood_control:5"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.edit_state == "rate_limited"
        assert ctx.edit_backoff_until > time.monotonic()
        assert ctx.edit_backoff_until - time.monotonic() <= 5.5

    asyncio.run(run())


def test_initial_send_flood_control_retry_in_format_uses_server_backoff():
    """Telegram sendRichMessage flood errors say 'Retry in N seconds'.

    The old regex only matched 'retry after' / 'flood_control:' / 'retry_after='.
    'Retry in' was missed, so the plugin retried every 30s against a multi-hour
    penalty and spiraled into worse flood control.
    """

    async def run():
        adapter = SequenceSendAdapter(["Flood control exceeded. Retry in 120 seconds"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.edit_state == "rate_limited"
        assert ctx.edit_backoff_until > time.monotonic()
        # Should honor the 120s server request, capped at 600s max.
        assert ctx.edit_backoff_until - time.monotonic() >= 100.0
        assert ctx.edit_backoff_until - time.monotonic() <= 120.5

    asyncio.run(run())


def test_flood_control_severe_backoff_is_capped_at_600s_not_30s():
    """A multi-hour penalty must not be capped to 30s — that causes a spiral."""

    async def run():
        adapter = SequenceSendAdapter(["Flood control exceeded. Retry in 11220 seconds"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.edit_state == "rate_limited"
        # Must be capped at 600s (10 min), not 30s.
        assert ctx.edit_backoff_until - time.monotonic() > 60.0

    asyncio.run(run())
