from tests.support.rendering import Result, EditableAdapter

import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.models.state import DelegateEvent, SessionContext
from hermes_progress_tail.rendering.renderer import ProgressRenderer






def test_delegate_renderer_does_not_render_subagent_text_fragments_as_update_rows():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {"lines_per_delegate": 3, "max_line_chars": 80},
                    }
                }
            )
        )
        ctx = SessionContext(
            "session-1",
            "key-1",
            "discord",
            "chat",
            "thread",
            adapter,
            asyncio.get_running_loop(),
            "live_tail",
            timestamp=False,
        )
        renderer.register_context(ctx)
        goal = "Inspect Apollo Coworker server and agent integration"
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-text",
                event_type="subagent.start",
                task_index=1,
                task_count=3,
                goal=goal,
            ),
            force=True,
        )

        for preview in ("s", " the"):
            await renderer.handle_event(
                DelegateEvent(
                    "session-1",
                    "key-1",
                    "discord",
                    "sa-text",
                    event_type="subagent.text",
                    task_index=1,
                    task_count=3,
                    goal=goal,
                    preview=preview,
                ),
                force=True,
            )

        content = adapter.edits[-1][2] if adapter.edits else adapter.sent[-1][1]
        assert "[2/3] → running · Inspect Apollo Coworker server and agent" in content
        assert "update: s" not in content
        assert "update: the" not in content
        assert "reply: s" not in content
        assert "reply: the" not in content

    asyncio.run(run())


def test_delegate_renderer_replaces_accumulated_subagent_text_reply_line():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {"lines_per_delegate": 3, "max_line_chars": 80},
                    }
                }
            )
        )
        ctx = SessionContext(
            "session-1",
            "key-1",
            "discord",
            "chat",
            "thread",
            adapter,
            asyncio.get_running_loop(),
            "live_tail",
            timestamp=False,
        )
        renderer.register_context(ctx)
        goal = "Inspect Apollo Coworker server and agent integration"
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-text",
                event_type="subagent.start",
                task_index=1,
                task_count=3,
                goal=goal,
            ),
            force=True,
        )

        for preview in ("I found ", "the Apollo Coworker server and agent bridge"):
            await renderer.handle_event(
                DelegateEvent(
                    "session-1",
                    "key-1",
                    "discord",
                    "sa-text",
                    event_type="subagent.text",
                    task_index=1,
                    task_count=3,
                    goal=goal,
                    preview=preview,
                ),
                force=True,
            )

        content = adapter.edits[-1][2]
        assert content.count("reply:") == 1
        assert "reply: I found the Apollo Coworker server and agent bridge" in content
        assert "update:" not in content

    asyncio.run(run())


def test_delegate_terminal_event_bypasses_live_tail_throttle():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"edit_interval": 999},
                    }
                }
            )
        )
        ctx = SessionContext(
            "session-1",
            "key-1",
            "discord",
            "chat",
            "thread",
            adapter,
            asyncio.get_running_loop(),
            "live_tail",
            edit_interval=999,
            timestamp=False,
        )
        renderer.register_context(ctx)
        goal = "Inspect Apollo Coworker server and agent integration"
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-terminal",
                event_type="subagent.start",
                goal=goal,
            )
        )
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-terminal",
                event_type="subagent.complete",
                goal=goal,
                summary="No issues found.",
            )
        )

        assert adapter.edits
        assert "result: ✓ done: No issues found." in adapter.edits[-1][2]

    asyncio.run(run())


def test_delegate_renderer_suppresses_streamed_reply_when_completion_result_is_shown():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {"lines_per_delegate": 3, "max_line_chars": 80},
                    }
                }
            )
        )
        ctx = SessionContext(
            "session-1",
            "key-1",
            "discord",
            "chat",
            "thread",
            adapter,
            asyncio.get_running_loop(),
            "live_tail",
            timestamp=False,
        )
        renderer.register_context(ctx)
        goal = "Inspect Apollo Coworker server and agent integration"
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-text",
                event_type="subagent.start",
                task_index=1,
                task_count=3,
                goal=goal,
            ),
            force=True,
        )
        streamed = "I found the Apollo Coworker server and agent bridge"
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-text",
                event_type="subagent.text",
                task_index=1,
                task_count=3,
                goal=goal,
                preview=streamed,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-text",
                event_type="subagent.complete",
                task_index=1,
                task_count=3,
                goal=goal,
                summary=streamed,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "result: ✓ done: I found the Apollo Coworker server and agent bridge" in content
        assert "reply:" not in content

    asyncio.run(run())


def test_delegate_renderer_flushes_short_streamed_reply_when_completion_hidden():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "delegates": {
                            "lines_per_delegate": 3,
                            "max_line_chars": 80,
                            "show_completion": False,
                        },
                    }
                }
            )
        )
        ctx = SessionContext(
            "session-1",
            "key-1",
            "discord",
            "chat",
            "thread",
            adapter,
            asyncio.get_running_loop(),
            "live_tail",
            timestamp=False,
        )
        renderer.register_context(ctx)
        goal = "Inspect Apollo Coworker server and agent integration"
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-text",
                event_type="subagent.start",
                task_index=1,
                task_count=3,
                goal=goal,
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-text",
                event_type="subagent.text",
                task_index=1,
                task_count=3,
                goal=goal,
                preview="No issues found.",
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "session-1",
                "key-1",
                "discord",
                "sa-text",
                event_type="subagent.complete",
                task_index=1,
                task_count=3,
                goal=goal,
                summary="No issues found.",
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "reply: No issues found." in content
        assert "result:" not in content

    asyncio.run(run())
