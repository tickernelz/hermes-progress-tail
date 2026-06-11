import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering.focused import focused_now, focused_tools, semantic_activity
from hermes_progress_tail.state import (
    DelegateEvent,
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


class SequenceSendAdapter(EditableAdapter):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        if self.errors:
            return Result(False, None, self.errors.pop(0))
        message_id = f"m{self.next_id}"
        self.next_id += 1
        return Result(True, message_id)


class ExceptionSendAdapter(SequenceSendAdapter):
    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        if self.errors:
            raise RuntimeError(self.errors.pop(0))
        message_id = f"m{self.next_id}"
        self.next_id += 1
        return Result(True, message_id)


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
                "skill_manage: patch example-version-control · running",
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
        assert "**Now** skill_manage: patch example-version-control" not in content
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
