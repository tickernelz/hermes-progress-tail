import asyncio
import time
from collections import deque

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.delegate_renderer import DelegateProgressRenderer
from hermes_progress_tail.formatter import extract_todo_items, format_tool_line
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering.focused import focused_now, focused_tools, semantic_activity
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


def test_semantic_activity_classifies_common_tool_intents():
    cases = {
        "terminal: python -m pytest tests/test_formatter.py -q": "running tests",
        "terminal: git push origin main": "publishing git changes",
        "terminal: gh release create v0.1.31": "publishing GitHub release",
        "execute_code: root = Path('…') … print(root) · 4 lines": "running Python script: root = Path('…') … print(root)",
        "🐍 execute_code: root = Path('…') … print(root) · 4 lines": "running Python script: root = Path('…') … print(root)",
        "delegate_task: Review formatter changes": "waiting on subagent: Review formatter changes",
        "patch: hermes_progress_tail/rendering/focused.py replace": "patching focused.py",
    }

    for raw, expected in cases.items():
        assert semantic_activity(raw) == expected


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


def test_focused_now_ignores_stale_tool_tail_when_no_tools_running():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused", "style": "plain"},
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
                "skill_manage: patch hmx-development-version-control · running",
                tool_call_id="bg-review-skill",
                tool_name="skill_manage",
            ),
            force=True,
        )
        ctx.tool_started_count = 20
        ctx.tool_completed_count = 25
        ctx.tool_failed_count = 0

        await renderer._render_live(ctx, force=True, ignore_backoff=True)
        content = adapter.edits[-1][2]

        assert "**State** 20 tools · 25 done · 0 running" in content
        assert "**Now** skill_manage: patch hmx-development-version-control" not in content
        assert "**Now** working" in content

    asyncio.run(run())


def test_focused_now_does_not_use_completed_delegate_when_no_activity_running():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused", "style": "emoji"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "telegram",
                "subagent-1",
                goal="Read-only review release-only bump diff",
                event_type="subagent.start",
                status="running",
            ),
            force=True,
        )
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "telegram",
                "subagent-1",
                goal="Read-only review release-only bump diff",
                event_type="subagent.complete",
                status="completed",
                summary="PASS",
            ),
            force=True,
        )
        ctx.tool_started_count = 13
        ctx.tool_completed_count = 12
        ctx.tool_failed_count = 1
        await renderer._render_live(ctx, force=True, ignore_backoff=True)

        content = adapter.edits[-1][2]
        assert "**State** 13 tools · 12 done · 1 failed · 0 running" in content
        assert "**Now** delegate · Read-only review release-only bump diff" not in content
        assert "**Now** working" in content
        assert focused_now(ctx) == "working"

    asyncio.run(run())


def test_focused_now_uses_active_delegate_before_any_tool_starts():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused", "style": "emoji"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)
        await renderer.handle_event(
            DelegateEvent(
                "s1",
                "k1",
                "telegram",
                "subagent-1",
                goal="Read-only review release-only bump diff",
                event_type="subagent.start",
                status="running",
            ),
            force=True,
        )

        assert focused_now(ctx) == "delegate · Read-only review release-only bump diff"
        assert "**Now** delegate · Read-only review release-only bump diff" in adapter.sent[0][1]

    asyncio.run(run())


def test_focused_tools_do_not_mark_completed_latest_tool_as_running():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused", "style": "emoji"},
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
                "💻 terminal: HPT_INTERACTIVE=0 install.sh · running",
                tool_call_id="install",
                tool_name="terminal",
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "✅ 💻 terminal: HPT_INTERACTIVE=0 install.sh · 20 lines · done",
                tool_call_id="install",
                tool_name="terminal",
                replace_existing=True,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "**State** 1 tools · 1 done · 0 running" in content
        assert "**Now** running HPT_INTERACTIVE=0 install.sh" not in content
        assert "**Now** working" in content
        tools = focused_tools(ctx, settings=renderer.settings)
        assert "✓ 💻 terminal: HPT_INTERACTIVE=0 install.sh" in tools
        assert "→ 💻 terminal: HPT_INTERACTIVE=0 install.sh" not in tools

    asyncio.run(run())


def test_focused_tools_mark_latest_active_tool_as_running():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused", "style": "emoji"},
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
                "💻 terminal: python -m pytest -q · running",
                tool_call_id="pytest",
                tool_name="terminal",
            ),
            force=True,
        )

        assert focused_now(ctx) == "running tests"
        assert "→ 💻 terminal: python -m pytest -q" in focused_tools(
            ctx, settings=renderer.settings
        )

    asyncio.run(run())


def test_focused_tools_respect_emoji_style_for_all_known_tool_icons():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "lines": 20},
                        "renderer": {"mode": "focused", "style": "emoji"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        ctx.resize(20)
        renderer.register_context(ctx)

        tool_lines = [
            ("skill_view", "📚 skill_view: hermes-agent"),
            ("todo", "📋 todo: 1 in progress"),
            ("terminal", "💻 terminal: pytest -q"),
            ("search_files", '🔎 search_files: "pattern"'),
            ("read_file", "📖 read_file: src/app.py:1+20"),
            ("write_file", "✍️ write_file: src/app.py"),
            ("patch", "🔧 patch: src/app.py replace"),
            ("delegate_task", "🧑‍💻 delegate_task: inspect renderer"),
            ("execute_code", "🐍 execute_code: print('ok') · 1 lines"),
            ("multi_tool_use.parallel", "🧰 parallel: 2 tools"),
            ("skill_manage", "⚙️ skill_manage: patch hermes-agent"),
        ]
        for index, (tool_name, line) in enumerate(tool_lines):
            await renderer.handle_event(
                ToolEvent(
                    "s1",
                    "k1",
                    "telegram",
                    f"✅ {line} · done · 0.{index}s",
                    tool_call_id=f"call-{index}",
                    tool_name=tool_name,
                    replace_existing=False,
                ),
                force=True,
            )

        content = adapter.edits[-1][2]
        for _, line in tool_lines:
            assert line in content
        assert "✓ skill_manage: patch hermes-agent" not in content

    asyncio.run(run())


def test_focused_tools_collapses_read_file_bursts_in_emoji_style():
    async def run():
        settings = load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False, "lines": 20},
                    "renderer": {"mode": "focused", "style": "emoji"},
                }
            }
        )
        ctx = make_ctx(EditableAdapter(), platform="telegram")
        ctx.resize(20)
        ctx.tool_lines.extend(
            [
                "✅ 📖 read_file: src/one.py:1+20 · done · 0.1s",
                "✅ 📖 read_file: src/two.py:1+20 · done · 0.1s",
                "✅ 📖 read_file: src/three.py:1+20 · done · 0.1s",
                "💻 terminal: pytest -q · running",
            ]
        )

        tools = focused_tools(ctx, settings=settings)

        assert tools == "✓ read_file: 3 files · one.py, two.py, three.py\n→ 💻 terminal: pytest -q"

    asyncio.run(run())


def test_focused_tools_respect_plain_style_for_all_known_tool_icons():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "lines": 20},
                        "renderer": {"mode": "focused", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        ctx.resize(20)
        renderer.register_context(ctx)

        raw_lines = [
            "✅ 📚 skill_view: hermes-agent · done · 0.1s",
            "✅ 📋 todo: 1 in progress · done · 0.1s",
            "✅ 💻 terminal: pytest -q · done · 0.1s",
            '✅ 🔎 search_files: "pattern" · done · 0.1s',
            "✅ 📖 read_file: src/app.py:1+20 · done · 0.1s",
            "✅ ✍️ write_file: src/app.py · done · 0.1s",
            "✅ 🔧 patch: src/app.py replace · done · 0.1s",
            "✅ 🧑‍💻 delegate_task: inspect renderer · done · 0.1s",
            "✅ 🐍 execute_code: print('ok') · 1 lines · done · 0.1s",
            "✅ 🧰 parallel: 2 tools · done · 0.1s",
            "✅ ⚙️ skill_manage: patch hermes-agent · done · 0.1s",
        ]
        for index, line in enumerate(raw_lines):
            await renderer.handle_event(
                ToolEvent(
                    "s1",
                    "k1",
                    "telegram",
                    line,
                    tool_call_id=f"call-{index}",
                    tool_name="tool",
                    replace_existing=False,
                ),
                force=True,
            )

        content = adapter.edits[-1][2]
        for emoji in ("📚", "📋", "💻", "🔎", "📖", "✍️", "🔧", "🧑‍💻", "🐍", "🧰", "⚙️"):
            assert emoji not in content
        assert "✓ skill_manage: patch hermes-agent" in content
        assert "→ skill_manage: patch hermes-agent" not in content

    asyncio.run(run())


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
        assert "… 2 queued" in content
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
                "✍️ write_file: ~/Works/HMX/hmx-002/tools/promotion/ai_replay.py · done",
                tool_name="write_file",
            ),
            force=True,
        )

        content = adapter.sent[0][1]
        assert "Changes\n" not in content
        assert "write_file: ~/Works/HMX/hmx-002/tools/promotion/ai_replay.py" in content
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


def test_tool_replacement_without_terminal_status_remains_running():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "show_completed": True},
                        "renderer": {"mode": "focused", "style": "plain"},
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
                "terminal: pytest · running",
                tool_call_id="call-1",
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "terminal: pytest tests/test_renderer.py · running",
                tool_call_id="call-1",
                replace_existing=True,
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "**State** 1 tools · 0 done · 1 running" in content
        assert ctx.tool_completed_count == 0
        assert "call-1" in ctx.active_tool_lines

    asyncio.run(run())


def test_terminal_tool_completion_clears_active_tracking():
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

        assert ctx.tool_completed_count == 1
        assert ctx.active_tool_lines == {}
        assert ctx.active_tool_fingerprints == {}

    asyncio.run(run())


def test_tool_replacement_changing_fingerprint_does_not_double_count_completion():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False, "show_completed": True},
                        "renderer": {"mode": "focused", "style": "plain"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "terminal: pytest · running", tool_call_id="call-1"),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "terminal: pytest tests/test_renderer.py · running",
                tool_call_id="call-1",
                replace_existing=True,
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1",
                "k1",
                "telegram",
                "✅ terminal: pytest tests/test_renderer.py --rerun · done · 1s",
                tool_call_id="call-1",
                replace_existing=True,
            ),
            force=True,
        )
        await renderer.handle_event(
            ToolEvent(
                "s1", "k1", "telegram", "terminal: ruff check · running", tool_call_id="call-2"
            ),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert ctx.tool_started_count == 2
        assert ctx.tool_completed_count == 1
        assert "**State** 2 tools · 1 done · 1 running" in content
        assert "terminal: pytest" not in ctx.active_tool_fingerprints

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
                args={"command": command, "workdir": "/home/zhafron/Projects/hermes-progress-tail"},
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
