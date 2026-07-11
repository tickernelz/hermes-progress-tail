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
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import (
    DelegateEvent,
    SessionContext,
)


















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
        assert "thinking: checking files" in content
        assert "update: thinking:" not in content
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
        assert "✓ done: PASS" in content
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
