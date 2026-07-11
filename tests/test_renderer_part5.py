from tests.support.rendering import (
    EditableAdapter,
    ExceptionSendAdapter,
    FailingEditAdapter,
    NoEditAdapter,
    Result,
    SequenceEditAdapter,
    SequenceSendAdapter,
    make_live_context as make_ctx,
)

import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.formatter import extract_todo_items, format_tool_line
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import (
    DelegateEvent,
    SessionContext,
    ToolEvent,
)


















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
        assert "[1/2] ✓ completed · review renderer implementation · 2 tools · 12s" in content
        assert "├ read_file: renderer.py" in content
        assert "├ terminal: pytest tests/test_renderer.py" in content
        assert "└ result: ✓ done: PASS: renderer grouped delegates correctly" in content

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
                args={"path": "/home/example/.hermes/plugins/hermes-progress-tail/plugin.yaml"},
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
                summary="Selesai dites. - Menjalankan `pwd && date` di `/home/example` - Output path: `/home/example` - Waktu: `Mon May 4 07:46:41 AM WIB 2026`",
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "read_file: " in content
        assert "hermes-progress-tail/plugin.yaml" in content
        assert "done: Selesai dites" in content
        assert "Menjalankan `pwd && date`" not in content
        assert "├ read_file" in content
        assert "└ result: ✓ done:" in content

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
                args={"pattern": "delegate", "path": "/home/example/Projects/hermes-progress-tail"},
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
