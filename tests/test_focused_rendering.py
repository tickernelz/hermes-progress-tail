import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import ReasoningEvent, SessionContext, ToolEvent
from tests.support.rendering import EditableAdapter


def make_renderer():
    return ProgressRenderer(
        load_settings(
            {
                "progress_tail": {
                    "renderer": {"mode": "focused"},
                    "tools": {"timestamp": False},
                    "assistant": {"min_update_chars": 1},
                    "reasoning": {"min_update_chars": 1},
                }
            }
        )
    )


def make_ctx(adapter, *, platform="telegram"):
    return SessionContext(
        "s1",
        "k1",
        platform,
        "chat",
        None,
        adapter,
        asyncio.get_running_loop(),
        "live_tail",
        timestamp=False,
    )


def test_focused_section_titles_are_bold_underlined_and_progress_reasoning_bodies_are_italic():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ReasoningEvent(
                "s1",
                "k1",
                "telegram",
                "**Planning editing capabilities**\nI need to inspect delete behavior.",
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )

        content = adapter.edits[-1][2]
        assert "**__Reasoning__**" in content
        assert "**__Tools__**" in content
        assert "***Planning editing capabilities***" in content
        assert "*I need to inspect delete behavior.*" in content
        assert "_→ 📖 read_file: renderer.py_" not in content

    asyncio.run(run())


def test_focused_plain_platform_keeps_plain_titles_and_body():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter, platform="cli")
        renderer.register_context(ctx)

        await renderer.handle_event(
            ReasoningEvent("s1", "k1", "cli", "**Thinking**\nplain body"), force=True
        )

        content = adapter.sent[-1][1]
        assert "Reasoning\nThinking\nplain body" in content
        assert "**__Reasoning__**" not in content
        assert "_Thinking_" not in content

    asyncio.run(run())


def test_focused_plan_renders_all_items_without_truncating_text():
    from hermes_progress_tail.rendering.focused import focused_plan
    from hermes_progress_tail.state import TodoItem

    settings = load_settings({"progress_tail": {"todo": {"max_item_chars": 12}}})
    items = (
        TodoItem("completed item with readable full sentence", "completed"),
        TodoItem("current item with readable full sentence", "in_progress"),
        TodoItem("first pending item with readable full sentence", "pending"),
        TodoItem("second pending item with readable full sentence", "pending"),
        TodoItem("cancelled item with readable full sentence", "cancelled"),
    )

    assert focused_plan(items, settings=settings) == (
        "✓ completed item with readable full sentence\n"
        "→ current item with readable full sentence\n"
        "… first pending item with readable full sentence\n"
        "… second pending item with readable full sentence\n"
        "× cancelled item with readable full sentence"
    )
