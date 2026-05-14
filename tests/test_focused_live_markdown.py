import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import AssistantEvent, ReasoningEvent, SessionContext, ToolEvent


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


def make_renderer():
    return ProgressRenderer(
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


def make_ctx(adapter, *, platform):
    return SessionContext(
        "s1",
        "k1",
        platform,
        "chat",
        "thread",
        adapter,
        asyncio.get_running_loop(),
        "live_tail",
        timestamp=False,
    )


def test_focused_live_markdown_platforms_emit_structural_markdown():
    async def run(platform):
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter, platform=platform)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", platform, "terminal: pytest · running"), force=True
        )

        content = adapter.sent[0][1]
        assert content.startswith("**Hermes is working**\n────────────────")
        assert "**Now** running tests" in content
        assert "**State** 1 tools · 0 done · 1 running" in content
        assert "**Tools**\n→ terminal: pytest" in content

    for platform in ("telegram", "discord", "slack", "mattermost", "matrix", "feishu", "dingtalk"):
        asyncio.run(run(platform))


def test_focused_live_markdown_preserves_progress_and_reasoning_markdown():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(
            AssistantEvent("s1", "k1", "telegram", "**Progress:** checking `file_name.py`"),
            force=True,
        )
        await renderer.handle_event(
            ReasoningEvent("s1", "k1", "telegram", "## Reasoning\n__Keep__ markdown."),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert "**Progress:** checking `file_name.py`" in content
        assert "Reasoning" in content
        assert "__Keep__ markdown." in content

    asyncio.run(run())


def test_focused_plain_platforms_do_not_emit_structural_markdown_and_strip_live_markdown():
    async def run(platform):
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter, platform=platform)
        renderer.register_context(ctx)

        await renderer.handle_event(
            AssistantEvent("s1", "k1", platform, "**Progress:** checking `file_name.py`"),
            force=True,
        )
        await renderer.handle_event(
            ReasoningEvent("s1", "k1", platform, "## Reasoning\n__Keep__ path `a_b.py`."),
            force=True,
        )

        content = adapter.edits[-1][2]
        assert content.startswith("Hermes is working\n────────────────")
        assert "**Progress**" not in content
        assert "**Progress:**" not in content
        assert "## Reasoning" not in content
        assert "__Keep__" not in content
        assert "`file_name.py`" in content
        assert "`a_b.py`" in content

    for platform in ("sms", "bluebubbles", "webhook", "api_server", "whatsapp"):
        asyncio.run(run(platform))
