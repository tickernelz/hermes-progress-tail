import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.monkeypatches import (
    install_compression_status_monkeypatch,
    uninstall_compression_status_monkeypatch,
)
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import AssistantEvent, SessionContext, ToolEvent


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


def make_ctx(adapter, *, platform="discord", strategy="live_tail"):
    return SessionContext(
        "s1",
        "k1",
        platform,
        "chat",
        None,
        adapter,
        asyncio.get_running_loop(),
        strategy,
        timestamp=False,
    )


def test_compression_status_clears_when_real_progress_resumes():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "renderer": {"mode": "focused"},
                        "tools": {"timestamp": False},
                        "assistant": {"min_update_chars": 1},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            AssistantEvent(
                ctx.session_id,
                ctx.session_key,
                ctx.platform,
                "📦 Preflight compression: ~204,662 tokens >= 204,000 threshold. This may take a moment.",
                transient=True,
            ),
            force=True,
        )
        assert "Preflight compression" in adapter.sent[-1][1]

        await renderer.handle_event(
            ToolEvent(ctx.session_id, ctx.session_key, ctx.platform, "→ terminal: pytest -q"),
            force=True,
        )

        latest = adapter.edits[-1][2] if adapter.edits else adapter.sent[-1][1]
        assert "Preflight compression" not in latest
        assert "terminal: pytest -q" in latest

    asyncio.run(run())


def test_compression_status_falls_back_to_native_when_not_captured(monkeypatch):
    import hermes_progress_tail.plugin as plugin

    monkeypatch.setattr(plugin, "on_compression_status_from_agent", lambda agent, text: False)

    class FakeAgent:
        def _emit_status(self, text):
            return f"native:{text}"

    install_compression_status_monkeypatch(FakeAgent)

    try:
        agent = FakeAgent()
        result = agent._emit_status("🗜️ Compacting context — summarizing earlier conversation")
    finally:
        uninstall_compression_status_monkeypatch(FakeAgent)

    assert result == "native:🗜️ Compacting context — summarizing earlier conversation"


def test_progress_messages_never_add_code_fences():
    async def run():
        for platform in ("discord", "telegram", "webhook"):
            renderer = ProgressRenderer(
                load_settings({"progress_tail": {"tools": {"timestamp": False}}})
            )
            adapter = EditableAdapter()
            ctx = make_ctx(adapter, platform=platform)
            renderer.register_context(ctx)
            await renderer.handle_event(
                ToolEvent("s1", "k1", platform, "prefix ``` stays literal"), force=True
            )
            content = adapter.sent[0][1]
            assert not content.startswith("```")
            assert not content.endswith("\n```")
            assert "``` stays literal" in content

    asyncio.run(run())


def test_telegram_progress_still_respects_message_limit_without_code_fence():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)
        huge = "prefix ``` unsafe\n" + ("x" * 5000)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", huge), force=True)
        content = adapter.sent[0][1]
        assert not content.startswith("```")
        assert not content.endswith("\n```")
        assert "``` unsafe" in content
        assert len(content) <= 4096

    asyncio.run(run())
