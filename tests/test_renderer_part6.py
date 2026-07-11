import asyncio  # noqa: I001 - keep shared helpers in one import

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.delegate_renderer import DelegateProgressRenderer
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import (
    DelegateEvent,
)
from tests.support.rendering import (
    EditableAdapter,
    make_live_context as make_ctx,
)


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
        assert "✓ completed" in content
        assert "terminal: pytest tests/test_renderer.py" in content
        assert "✓ done: PASS" in content

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
        assert "├ terminal: python inline script" in content
        assert "├ update: terminal: <empty>" in content
        assert '└ result: ✓ done: {"passed":true}' in content
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
        assert "└ result: ✓ done: PASS" in content

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
        assert "→ running" in content
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
                args={"command": command, "workdir": "/home/example/Projects/hermes-progress-tail"},
                tool_count=1,
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "└ terminal: python inline script" in content
        assert "· 4 lines" in content
        assert "   cwd: ." in content
        assert "   first: python - <<'PY'" in content
        assert "print('safe first') … print('safe second')" in content

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
