import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.formatter import extract_todo_items, format_tool_line
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import SessionContext, ToolEvent


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


def make_ctx(adapter, *, strategy="live_tail", timestamp=False):
    return SessionContext(
        "s1",
        "k1",
        "discord",
        "chat",
        "thread",
        adapter,
        asyncio.get_running_loop(),
        strategy,
        timestamp=timestamp,
    )


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
        assert adapter.edits[-1][2] == "🧰 Tools\ntool 2\ntool 3\ntool 4"

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

        assert adapter.sent[0][1] == "🧰 Tools\ntool 0"
        assert adapter.edits == []
        await renderer.finalize(session_id="s1")
        assert adapter.edits[-1][2] == "🧰 Tools\ntool 2\ntool 3\ntool 4"

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
            timestamp_format="%H:%M",
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "terminal: npm test", created_at=0),
            force=True,
        )

        assert adapter.sent[0][1] == "🧰 Tools\n[07:00] terminal: npm test"

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
        assert "📋 Todo" in content
        assert "▶ implement sticky todo" in content
        assert "pending: write tests, push tag" in content
        assert "done: inspect repo" in content
        assert "tool 2\ntool 3\ntool 4" in content

        await renderer.finalize(session_id="s1")
        assert renderer.sessions["s1"].todo_items == ()

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


def test_edit_failure_downgrades_to_snapshot():
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
        assert adapter1.sent[0][1] == "🧰 Tools\none"
        assert adapter2.sent[0][0] == "chat2"
        assert adapter2.sent[0][1] == "🧰 Tools\ntwo"

    asyncio.run(run())
