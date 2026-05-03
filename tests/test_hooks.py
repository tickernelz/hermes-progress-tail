import asyncio

import hermes_progress_tail
from hermes_progress_tail.config import load_settings
from hermes_progress_tail.state import SessionContext


class Source:
    platform = type("P", (), {"value": "discord"})()
    chat_id = "chat"
    thread_id = "thread"
    user_id = "user"
    user_id_alt = None
    chat_type = "group"


class Event:
    source = Source()


class SessionEntry:
    session_id = "session-1"
    session_key = "key-1"


class SessionStore:
    def get_or_create_session(self, source):
        return SessionEntry()


class Gateway:
    def __init__(self, adapter):
        self.adapters = {Source.platform: adapter}
        self.config = type(
            "Config", (), {"group_sessions_per_user": True, "thread_sessions_per_user": False}
        )()


class Adapter:
    name = "adapter"

    def __init__(self):
        self.sent = []

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        return type("Result", (), {"success": True, "message_id": "m1", "error": ""})()

    async def edit_message(self, chat_id, message_id, content):
        return type("Result", (), {"success": True, "message_id": message_id, "error": ""})()


def test_pre_gateway_dispatch_registers_context(monkeypatch):
    async def run():
        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin, "_load_runtime_settings", lambda: load_settings({})
        )

        result = hermes_progress_tail._on_pre_gateway_dispatch(
            Event(), Gateway(adapter), SessionStore()
        )

        assert result is None
        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1")
        assert isinstance(ctx, SessionContext)
        assert ctx.chat_id == "chat"
        assert ctx.thread_id == "thread"

    asyncio.run(run())


def test_pre_tool_call_formats_and_renders(monkeypatch):
    async def run():
        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin, "_load_runtime_settings", lambda: load_settings({})
        )
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "npm test"}, task_id="session-1"
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert adapter.sent[0][1] == "🧰 Tools\n💻 terminal: npm test"

    asyncio.run(run())
