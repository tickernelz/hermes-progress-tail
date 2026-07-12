import asyncio
from types import SimpleNamespace

import hermes_progress_tail
from hermes_progress_tail.monkeypatches import (
    install_gateway_interrupt_monkeypatch,
    uninstall_gateway_interrupt_monkeypatch,
)
from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.state import SessionContext, ToolEvent
from tests.support.rendering import Result


class EditableAdapter:
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


class FakeGatewayRunner:
    def __init__(self):
        self.calls = []

    async def _interrupt_and_clear_session(
        self,
        session_key,
        source,
        *,
        interrupt_reason,
        invalidation_reason,
        release_running_state=True,
    ):
        self.calls.append(
            (session_key, source, interrupt_reason, invalidation_reason, release_running_state)
        )
        return "interrupted"


def make_ctx(adapter):
    return SessionContext(
        session_id="session-1",
        session_key="key-1",
        platform="telegram",
        chat_id="chat",
        thread_id="77445",
        adapter=adapter,
        loop=asyncio.get_running_loop(),
        strategy="live_tail",
        timestamp=False,
    )


def test_gateway_stop_interrupt_retires_progress_context(monkeypatch):
    async def run():
        uninstall_gateway_interrupt_monkeypatch(FakeGatewayRunner)
        adapter = EditableAdapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "cleanup": {"auto_delete": False},
                    }
                }
            ),
        )
        renderer = hermes_progress_tail._get_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        await renderer.handle_event(
            ToolEvent("session-1", "key-1", "telegram", "old turn"), force=True
        )

        assert install_gateway_interrupt_monkeypatch(FakeGatewayRunner) is True
        gateway = FakeGatewayRunner()
        result = await gateway._interrupt_and_clear_session(
            "key-1",
            SimpleNamespace(platform="telegram"),
            interrupt_reason="Stop requested",
            invalidation_reason="stop_command",
        )
        await asyncio.sleep(0.05)

        assert result == "interrupted"
        assert ctx.progress_state == "finalized"
        assert list(ctx.tool_lines) == []

        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await renderer.handle_event(
            ToolEvent("session-1", "key-1", "telegram", "new turn"), force=True
        )

        assert len(adapter.sent) == 2
        assert adapter.sent[0][1] == "▰ 🧰 Tools\nold turn"
        assert adapter.sent[1][1] == "▰ 🧰 Tools\nnew turn"
        assert next_ctx.message_id == "m2"
        uninstall_gateway_interrupt_monkeypatch(FakeGatewayRunner)

    asyncio.run(run())


def test_non_stop_interrupt_does_not_retire_progress_context(monkeypatch):
    async def run():
        uninstall_gateway_interrupt_monkeypatch(FakeGatewayRunner)
        adapter = EditableAdapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "cleanup": {"auto_delete": False},
                    }
                }
            ),
        )
        renderer = hermes_progress_tail._get_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        await renderer.handle_event(
            ToolEvent("session-1", "key-1", "telegram", "old turn"), force=True
        )

        assert install_gateway_interrupt_monkeypatch(FakeGatewayRunner) is True
        gateway = FakeGatewayRunner()
        await gateway._interrupt_and_clear_session(
            "key-1",
            SimpleNamespace(platform="telegram"),
            interrupt_reason="Session reset requested",
            invalidation_reason="new_command",
        )
        await asyncio.sleep(0.05)

        assert ctx.progress_state == "active"
        replacement_ctx = make_ctx(adapter)
        renderer.register_context(replacement_ctx)
        await renderer.handle_event(
            ToolEvent("session-1", "key-1", "telegram", "same active turn"), force=True
        )

        assert len(adapter.sent) == 1
        assert adapter.edits[-1] == (
            "chat",
            "m1",
            "▰ 🧰 Tools\nold turn\nsame active turn",
        )
        uninstall_gateway_interrupt_monkeypatch(FakeGatewayRunner)

    asyncio.run(run())
