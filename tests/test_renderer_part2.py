import asyncio  # noqa: I001 - keep shared helpers in one import
import time
from collections import deque

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.formatter import extract_todo_items, format_tool_line
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import (
    AssistantEvent,
    BackgroundJobEvent,
    DelegateEvent,
    ReasoningEvent,
    ToolEvent,
)
from tests.support.rendering import (
    EditableAdapter,
    make_live_context as make_ctx,
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
        ctx.agent_label = "Akbar"
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
        assert content.startswith("**Akbar is working**\n────────────────")
        assert "**Now** patching formatter.py" in content
        assert "**Why** Gue cek formatter path dulu, jangan sampai strip code/path." in content
        assert "**State** 3 tools · 2 done · 1 running · 2 queued" in content
        assert "**__Progress__**\n*Gue cek formatter path dulu" in content
        assert "**__Reasoning__**\n***Planning task execution***" in content
        assert "**Planning task execution**" in content
        assert "**__Plan__**\n✓ inspect adapter contract" in content
        assert "→ implement plain-live sanitizer" in content
        assert "… verify targeted tests" in content
        assert "… run full suite" in content
        assert "**__Delegates__**\n" in content
        assert "**__Background__**\n" in content
        assert "**__Tools__**\n✓ read_file · telegram.py:3108" in content
        assert "→ patch · rendering/formatter.py" in content
        assert "Changes\n" not in content
        assert "~ ~/" not in content

    asyncio.run(run())


def test_compact_renderer_mode_normalizes_to_sectioned_compact_density():
    settings = load_settings({"progress_tail": {"renderer": {"mode": "compact"}}})

    assert settings.renderer.mode == "sectioned"
    assert settings.renderer.density == "compact"


def test_focused_header_shows_semantic_now_for_execute_code():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused", "density": "verbose", "style": "plain"},
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
                "execute_code: root = Path('…') … print(root) · 4 lines",
                tool_name="execute_code",
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "**Now** running Python script: root = Path('…') … print(root)" in content
        assert "**__Tools__**\n→ execute_code: root = Path('…') … print(root) · 4 lines" in content

    asyncio.run(run())


def test_focused_tools_collapses_completed_read_file_burst():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "lines": 4},
                        "renderer": {"mode": "focused", "density": "verbose", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        ctx.lines = 4
        ctx.tool_lines = deque(
            [
                "✅ read_file: hermes_progress_tail/rendering/focused.py:1+80 · done · 0.1s",
                "✅ read_file: tests/test_renderer.py:1+60 · done · 0.1s",
                "✅ read_file: README.md:20+10 · done · 0.1s",
                "terminal: python -m pytest tests/test_renderer.py -q · running",
            ],
            maxlen=4,
        )
        ctx.tool_started_count = 4
        ctx.tool_completed_count = 3
        renderer.register_context(ctx)

        await renderer._render_for_strategy(ctx, None, force=True)

        content = adapter.sent[0][1]
        assert (
            "**__Tools__**\n✓ read_file: 3 files · focused.py, test_renderer.py, README.md"
            in content
        )
        assert "→ terminal: python -m pytest tests/test_renderer.py -q" in content
        assert "read_file: hermes_progress_tail/rendering/focused.py" not in content
        assert "read_file: tests/test_renderer.py" not in content

    asyncio.run(run())


def test_focused_tools_keeps_failed_tool_visible_inside_burst():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "lines": 4},
                        "renderer": {"mode": "focused", "density": "verbose", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        ctx.lines = 4
        ctx.tool_lines = deque(
            [
                "✅ read_file: a.py:1+10 · done · 0.1s",
                "❌ read_file: missing.py · failed · 0.1s",
                "✅ read_file: b.py:1+10 · done · 0.1s",
                "patch: hermes_progress_tail/rendering/focused.py replace · running",
            ],
            maxlen=4,
        )
        ctx.tool_started_count = 4
        ctx.tool_completed_count = 2
        ctx.tool_failed_count = 1
        renderer.register_context(ctx)

        await renderer._render_for_strategy(ctx, None, force=True)

        content = adapter.sent[0][1]
        assert "✓ read_file: a.py" in content
        assert "× read_file: missing.py" in content
        assert "✓ read_file: b.py" in content
        assert "→ patch: hermes_progress_tail/rendering/focused.py replace" in content
        assert "read_file: 3 files" not in content

    asyncio.run(run())


def test_focused_header_elapsed_uses_turn_start_not_latest_event():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused", "density": "verbose", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)
        ctx.started_at = time.monotonic() - 3600

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "terminal: pytest · running", created_at=10),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "**Time** 1h 00m" in content
        assert "**Time** just now" not in content

    asyncio.run(run())


def test_focused_state_uses_tool_lifecycle_counts_not_visible_tail_size():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "lines": 3},
                        "renderer": {"mode": "focused", "density": "verbose", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        for index in range(10):
            await renderer.handle_event(
                ToolEvent(
                    "s1",
                    "k1",
                    "telegram",
                    f"tool {index} · running",
                    tool_call_id=f"call-{index}",
                    created_at=index,
                ),
                force=True,
            )
            await renderer.handle_event(
                ToolEvent(
                    "s1",
                    "k1",
                    "telegram",
                    f"✅ tool {index} · done · 1.0s",
                    tool_call_id=f"call-{index}",
                    replace_existing=True,
                    created_at=index + 0.5,
                ),
                force=True,
            )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "terminal: pytest · running",
                tool_call_id="call-running",
                created_at=11,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert len(ctx.tool_lines) == 3
        assert "**State** 11 tools · 10 done · 1 running" in content
        assert "**State** 3 tools" not in content

    asyncio.run(run())


def test_focused_mode_does_not_render_changes_section_for_write_file():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused", "density": "verbose", "style": "plain"},
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
                "✍️ write_file: ~/Works/Acme/example-app/tools/promotion/ai_replay.py · done",
                tool_name="write_file",
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "Changes\n" not in content
        assert "write_file: ~/Works/Acme/example-app/tools/promotion/ai_replay.py" in content
        assert "~ ~/Works" not in content

    asyncio.run(run())


def test_focused_header_uses_renderer_agent_label_when_configured():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {
                            "mode": "focused",
                            "density": "verbose",
                            "style": "plain",
                            "agent_label": "Akbar",
                        },
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "terminal: pytest · running", created_at=1),
            force=True,
        )

        assert adapter.sent[0][1].startswith("**Akbar is working**\n────────────────")

    asyncio.run(run())


def test_focused_header_falls_back_to_hermes_not_jono():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused", "density": "verbose", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "terminal: pytest · running", created_at=1),
            force=True,
        )

        content = adapter.sent[0][1]
        assert content.startswith("**Hermes is working**\n────────────────")
        assert "Jono is working" not in content

    asyncio.run(run())
