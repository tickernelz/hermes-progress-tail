import asyncio
import time

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.delegate_renderer import DelegateProgressRenderer
from hermes_progress_tail.formatter import extract_todo_items, format_tool_line
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import (
    AssistantEvent,
    BackgroundJobEvent,
    DelegateEvent,
    ReasoningEvent,
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


def test_focused_verbose_layout_prioritizes_now_state_and_curated_sections():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "lines": 4},
                        "assistant": {"min_update_chars": 1, "max_lines": 2, "max_chars": 220},
                        "reasoning": {"min_update_chars": 1, "max_lines": 2, "max_chars": 260},
                        "renderer": {"mode": "focused", "density": "verbose", "style": "plain"},
                        "delegates": {"lines_per_delegate": 1, "max_delegates": 2},
                        "background_jobs": {"max_jobs": 2, "head_lines": 1, "tail_lines": 1},
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
                "Gue cek formatter path dulu, jangan sampai strip code/path.",
                created_at=0,
            ),
            force=True,
        )
        await renderer.handle_event(
            ReasoningEvent(
                "s1",
                "k1",
                "telegram",
                "**Planning task execution**\nTelegram edits are plain text; sanitize markers instead of abusing finalize.",
                created_at=1,
            ),
            force=True,
        )
        todo_args = {
            "todos": [
                {"content": "inspect adapter contract", "status": "completed"},
                {"content": "inspect renderer assumptions", "status": "completed"},
                {"content": "implement plain-live sanitizer", "status": "in_progress"},
                {"content": "verify targeted tests", "status": "pending"},
                {"content": "run full suite", "status": "pending"},
            ]
        }
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                format_tool_line("todo", todo_args),
                tool_name="todo",
                todo_items=extract_todo_items(todo_args),
                created_at=2,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "telegram",
                "reviewer-1",
                task_index=0,
                task_count=1,
                goal="formatter edge cases",
                event_type="subagent.start",
                status="running",
                created_at=3,
            ),
            force=True,
        )
        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "telegram",
                "pytest-full",
                event_type="started",
                command="python -m pytest -q",
                created_at=4,
            ),
            force=True,
        )
        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "telegram",
                "pytest-full",
                event_type="output",
                output="126/214 tests\n",
                created_at=5,
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file · telegram.py:3108", created_at=6),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "search_files · edit_message", created_at=7),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "patch · rendering/formatter.py",
                tool_name="patch",
                created_at=8,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert content.startswith("Jono is working\n────────────────")
        assert "Now     patch · rendering/formatter.py" in content
        assert "Why     Gue cek formatter path dulu, jangan sampai strip code/path." in content
        assert "State   3 tools done · 1 running · 2 queued" in content
        assert "Progress\nGue cek formatter path dulu" in content
        assert "Reasoning\nPlanning task execution" in content
        assert "**Planning task execution**" not in content
        assert "Plan\n✓ inspect adapter contract" in content
        assert "→ implement plain-live sanitizer" in content
        assert "… 2 queued" in content
        assert "Delegates\n" in content
        assert "Background\n" in content
        assert "Tools\n✓ read_file · telegram.py:3108" in content
        assert "→ patch · rendering/formatter.py" in content
        assert "Changes\n~ rendering/formatter.py" in content

    asyncio.run(run())


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
        assert "**Checking formatter**" not in content
        assert "## Inspecting Markdown" not in content
        assert "__Do not__" not in content
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


def test_renderer_compact_density_and_debug_downgrade_visibility():
    async def run():
        adapter = FailingEditAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"density": "debug"},
                        "no_edit": {"min_new_events": 1},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "one"), force=True)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "two"), force=True)

        assert renderer.sessions["s1"].downgrade_reason == "edit not supported"
        assert "downgrade=edit not supported" in adapter.sent[-1][1]
        assert "🛠️ Debug" in adapter.sent[-1][1]

    asyncio.run(run())


def test_compact_density_renders_one_line_todo():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"density": "compact"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        todo_args = {
            "todos": [
                {"content": "polish doctor", "status": "in_progress"},
                {"content": "run tests", "status": "pending"},
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
            ),
            force=True,
        )

        assert adapter.sent[0][1] == "▰ 📋 Todo: active: polish doctor · 1 pending"

    asyncio.run(run())


def test_completion_replacement_bypasses_live_tail_throttle():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "show_completed": True},
                        "renderer": {"edit_interval": 999},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "terminal: pytest · running", tool_call_id="call-1")
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "discord",
                "✅ terminal: pytest · done · 2.1s",
                tool_call_id="call-1",
                replace_existing=True,
            )
        )

        assert adapter.sent[0][1] == "▰ 🧰 Tools\nterminal: pytest · running"
        assert adapter.edits[-1][2] == "▰ 🧰 Tools\n✅ terminal: pytest · done · 2.1s"

    asyncio.run(run())


def test_delegate_progress_renders_grouped_section_and_resets_on_finalize():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {"lines_per_delegate": 2},
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
                "sa-1",
                task_index=0,
                task_count=2,
                goal="review renderer implementation",
                event_type="subagent.start",
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-1",
                task_index=0,
                task_count=2,
                goal="review renderer implementation",
                event_type="subagent.tool",
                tool_name="read_file",
                preview="renderer.py",
                tool_count=1,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-1",
                task_index=0,
                task_count=2,
                goal="review renderer implementation",
                event_type="subagent.tool",
                tool_name="terminal",
                preview="pytest tests/test_renderer.py",
                tool_count=2,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-1",
                task_index=0,
                task_count=2,
                goal="review renderer implementation",
                event_type="subagent.complete",
                status="completed",
                duration_seconds=12.3,
                summary="PASS: renderer grouped delegates correctly",
                tool_count=2,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "🔀 Delegates" in content
        assert "[1/2] ✅ completed · review renderer implementation · 2 tools · 12s" in content
        assert "├ tool: 📖 read_file: renderer.py" in content
        assert "├ tool: 💻 terminal: pytest tests/test_renderer.py" in content
        assert "└ result: ✅ done: PASS: renderer grouped delegates correctly" in content

        await renderer.finalize(session_id="s1")
        assert renderer.sessions["s1"].delegate_branches == {}
        assert list(renderer.sessions["s1"].delegate_order) == []

    asyncio.run(run())


def test_delegate_completion_does_not_replace_latest_tool_line():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {"lines_per_delegate": 1, "max_line_chars": 90},
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
                "sa-brief",
                goal="test delegate polish",
                event_type="subagent.tool",
                tool_name="read_file",
                args={"path": "/home/zhafron/.hermes/plugins/hermes-progress-tail/plugin.yaml"},
                tool_count=1,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-brief",
                goal="test delegate polish",
                event_type="subagent.complete",
                status="completed",
                duration_seconds=22,
                summary="Selesai dites. - Menjalankan `pwd && date` di `/home/zhafron` - Output path: `/home/zhafron` - Waktu: `Mon May 4 07:46:41 AM WIB 2026`",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "read_file: " in content
        assert "hermes-progress-tail/plugin.yaml" in content
        assert "done: Selesai dites" in content
        assert "Menjalankan `pwd && date`" not in content
        assert "├ tool: 📖 read_file" in content
        assert "└ result: ✅ done:" in content

    asyncio.run(run())


def test_delegate_progress_uses_args_for_tool_details():
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
                "sa-args",
                goal="inspect files",
                event_type="subagent.tool",
                tool_name="search_files",
                args={"pattern": "delegate", "path": "/home/zhafron/Projects/hermes-progress-tail"},
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert 'search_files: "delegate" in .' in content

    asyncio.run(run())


def test_delegate_patch_preview_only_renders_as_patch_path_not_empty_remove():
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
                "sa-patch",
                goal="patch renderer",
                event_type="subagent.tool",
                tool_name="patch",
                preview="renderer.py",
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "patch: renderer.py" in content
        assert "<empty>" not in content
        assert "remove" not in content

    asyncio.run(run())


def test_delegate_compact_density_prefers_completion_line():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"density": "compact"},
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
                "sa-compact",
                goal="compact completion",
                event_type="subagent.tool",
                tool_name="terminal",
                preview="pytest tests/test_renderer.py",
                tool_count=1,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-compact",
                goal="compact completion",
                event_type="subagent.complete",
                status="completed",
                summary="PASS. Extra verbose details should not dominate compact mode.",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "done: PASS" in content
        assert "pytest tests/test_renderer.py" not in content

    asyncio.run(run())


def test_delegate_progress_redacts_secrets_at_renderer_boundary():
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
                "sa-secret",
                goal="inspect auth",
                event_type="subagent.tool",
                tool_name="terminal",
                preview="curl -H 'Authorization: Bearer sk-secret1234567890' https://example.test",
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "sk-sec...7890" not in content
        assert "[redacted" in content

    asyncio.run(run())


def test_delegate_reused_branch_resets_completed_lifecycle_on_new_start():
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
                goal="first delegate",
                event_type="subagent.complete",
                status="completed",
                duration_seconds=191,
                summary="first pass",
                tool_count=3,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "task-0",
                goal="second delegate",
                event_type="subagent.start",
                status="running",
                tool_count=0,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "second delegate" in content
        assert "running" in content
        assert "191s" not in content
        assert "first pass" not in content
        assert "3 tools" not in content

    asyncio.run(run())


def test_delegate_spawn_requested_start_preserves_queued_elapsed_origin():
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
                "sa-queued",
                goal="queued delegate",
                event_type="subagent.spawn_requested",
                created_at=100.0,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-queued",
                goal="queued delegate",
                event_type="subagent.start",
                created_at=105.0,
            ),
            force=True,
        )

        branch = renderer.sessions["s1"].delegate_branches["sa-queued"]
        assert branch.started_at == 100.0
        assert branch.status == "running"

    asyncio.run(run())


def test_delegate_section_respects_emoji_style_for_status_and_tool_lines():
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
                "sa-emoji",
                goal="emoji delegate",
                event_type="subagent.tool",
                tool_name="terminal",
                preview="pytest tests/test_renderer.py",
                tool_count=1,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-emoji",
                goal="emoji delegate",
                event_type="subagent.complete",
                status="completed",
                summary="PASS",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "🔀 Delegates" in content
        assert "✅ completed" in content
        assert "💻 terminal: pytest tests/test_renderer.py" in content
        assert "✅ done: PASS" in content

    asyncio.run(run())


def test_delegate_section_respects_plain_style_without_emoji():
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

        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-plain",
                goal="plain delegate",
                event_type="subagent.tool",
                tool_name="terminal",
                preview="pytest tests/test_renderer.py",
                tool_count=1,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-plain",
                goal="plain delegate",
                event_type="subagent.complete",
                status="completed",
                summary="PASS",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "Delegates" in content
        assert "🔀" not in content
        assert "✅" not in content
        assert "💻" not in content
        assert "[1/1] completed" in content
        assert "terminal: pytest tests/test_renderer.py" in content
        assert "done: PASS" in content

    asyncio.run(run())


def test_delegate_grouped_rendering_labels_events_without_fake_tool_children():
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
                "sa-grouped",
                goal="grouped delegate",
                event_type="subagent.tool",
                tool_name="terminal",
                preview="python inline script",
                tool_count=1,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-grouped",
                goal="grouped delegate",
                event_type="subagent.progress",
                preview="terminal: <empty>",
                tool_count=1,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-grouped",
                goal="grouped delegate",
                event_type="subagent.complete",
                status="completed",
                summary='{"passed":true}',
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "├ tool: 💻 terminal: python inline script" in content
        assert "├ update: terminal: <empty>" in content
        assert '└ result: ✅ done: {"passed":true}' in content
        assert "  - terminal:" not in content
        assert "  - done:" not in content

    asyncio.run(run())


def test_delegate_unknown_tool_details_are_suppressed_in_normal_density():
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
                "sa-unknown",
                goal="unknown delegate",
                event_type="subagent.tool",
                tool_name="read_file",
                tool_count=1,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-unknown",
                goal="unknown delegate",
                event_type="subagent.complete",
                status="completed",
                summary="PASS",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "<unknown>" not in content
        assert "read_file" not in content
        assert "└ result: ✅ done: PASS" in content

    asyncio.run(run())


def test_delegate_suppressed_unknown_tool_still_marks_branch_running():
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
                "sa-unknown-running",
                goal="unknown running delegate",
                event_type="subagent.tool",
                tool_name="read_file",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "🔄 running" in content
        assert "pending" not in content
        assert "<unknown>" not in content

    asyncio.run(run())


def test_delegate_write_file_file_path_is_not_suppressed():
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
                "sa-write-file-path",
                goal="write delegate",
                event_type="subagent.tool",
                tool_name="write_file",
                args={"file_path": "/Users/alice/project/out.txt"},
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "write_file:" in content
        assert "out.txt" in content
        assert "<unknown>" not in content

    asyncio.run(run())


def test_delegate_partial_args_use_preview_for_missing_formatter_detail():
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
                "sa-partial-read",
                goal="partial read delegate",
                event_type="subagent.tool",
                tool_name="read_file",
                args={"limit": 20},
                preview="plugin.yaml",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "read_file: plugin.yaml" in content
        assert "<unknown>" not in content

    asyncio.run(run())


def test_delegate_normal_density_terminal_renders_safe_multiline_details():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"density": "normal"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        command = "python - <<'PY'\nprint('safe first')\nprint('safe second')\nPY"

        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-terminal-detail",
                goal="terminal detail delegate",
                event_type="subagent.tool",
                tool_name="terminal",
                args={"command": command, "workdir": "/home/zhafron/Projects/hermes-progress-tail"},
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "└ tool: 💻 terminal: python inline script · 4 lines" in content
        assert "   cwd: ." in content
        assert "   first: python - <<'PY'" in content
        assert "safe first" not in content
        assert "safe second" not in content

    asyncio.run(run())


def test_delegate_cwd_home_relative_paths_are_cross_platform(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/alice")
    monkeypatch.setenv("USERPROFILE", r"C:\\Users\\Alice")

    assert DelegateProgressRenderer._delegate_cwd("/Users/alice/projects/app") == "~/projects/app"
    assert (
        DelegateProgressRenderer._delegate_cwd(r"C:\\Users\\Alice\\projects\\app")
        == "~/projects/app"
    )
    assert DelegateProgressRenderer._delegate_cwd("/opt/app") == "/opt/app"


def test_delegate_compact_density_active_tool_renders_text_not_internal_repr():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"density": "compact"},
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
                "sa-compact-active",
                goal="compact active delegate",
                event_type="subagent.tool",
                tool_name="terminal",
                preview="pytest tests/test_renderer.py",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "terminal: pytest tests/test_renderer.py" in content
        assert "DelegateLine(" not in content
        assert "details=" not in content
        assert "tool_name=" not in content
        assert "├" not in content
        assert "└" not in content

    asyncio.run(run())


def test_delegate_thinking_summary_uses_structured_line():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {"thinking": "summary"},
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
                goal="thinking delegate",
                event_type="subagent.thinking",
                preview="checking files",
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "update: thinking: checking files" in content
        assert "DelegateLine(" not in content

    asyncio.run(run())


def test_delegate_compact_density_omits_timeline_details():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"density": "compact"},
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
                "sa-compact-shape",
                goal="compact shape delegate",
                event_type="subagent.tool",
                tool_name="terminal",
                args={"command": "python - <<'PY'\nprint('x')\nPY", "workdir": "/tmp"},
                tool_count=1,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-compact-shape",
                goal="compact shape delegate",
                event_type="subagent.complete",
                status="completed",
                summary="PASS",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "compact shape delegate" in content
        assert "✅ done: PASS" in content
        assert "├" not in content
        assert "└" not in content
        assert "│  cwd:" not in content
        assert "first:" not in content

    asyncio.run(run())


def test_delegate_completion_summary_skips_empty_heading_to_next_line():
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
                "sa-summary",
                goal="summary delegate",
                event_type="subagent.complete",
                status="completed",
                summary="Ringkasan singkat:\n- Versi hermes-progress-tail: 0.1.7\n- Tidak ada file dimodifikasi.",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "Ringkasan singkat: Versi hermes-progress-tail: 0.1.7" in content
        assert "Ringkasan singkat:\n" not in content

    asyncio.run(run())


def test_delegate_progress_can_be_disabled_per_platform():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(load_settings({}))
        ctx = make_ctx(adapter)
        ctx.delegates_enabled = False
        renderer.register_context(ctx)

        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "discord",
                "sa-1",
                goal="hidden delegate",
                event_type="subagent.tool",
                tool_name="terminal",
                preview="pytest",
            ),
            force=True,
        )

        assert adapter.sent == []

    asyncio.run(run())
