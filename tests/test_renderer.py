import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.formatter import extract_todo_items, format_tool_line
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import DelegateEvent, SessionContext, ToolEvent


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
            timestamp_format="%M:%S",
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "terminal: npm test", created_at=0),
            force=True,
        )

        assert adapter.sent[0][1] == "🧰 Tools\n[00:00] terminal: npm test"

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
        assert "📋 Todo" not in content
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

        assert "📋 Todo" in adapter.sent[0][1]
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

        assert adapter.sent[0][1] == "📋 Todo: active: polish doctor · 1 pending"

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

        assert adapter.sent[0][1] == "🧰 Tools\nterminal: pytest · running"
        assert adapter.edits[-1][2] == "🧰 Tools\n✅ terminal: pytest · done · 2.1s"

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
        assert "[1/2] completed · review renderer implementation · 2 tools · 12s" in content
        assert "read_file: renderer.py" in content
        assert "terminal: pytest tests/test_renderer.py" in content
        assert "done: PASS: renderer grouped delegates correctly" in content

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
        assert "read_file: ~/.hermes/plugins/hermes-progress-tail/plugin.yaml" in content
        assert "done: Selesai dites" in content
        assert "Menjalankan `pwd && date`" not in content
        assert "- read_file" in content
        assert "- done:" in content

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
        assert "sk-secret1234567890" not in content
        assert "[redacted" in content

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
