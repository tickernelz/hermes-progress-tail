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
        self._message_handler = None
        self._session_store = None
        self.config = type("AdapterConfig", (), {"extra": {}})()

    def set_message_handler(self, handler):
        self._message_handler = handler

    def set_session_store(self, session_store):
        self._session_store = session_store

    async def handle_message(self, event):
        if self._message_handler is not None:
            return await self._message_handler(event)
        return None

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
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"agent_label": "Akbar"},
                    }
                }
            ),
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
        assert ctx.agent_label == "Akbar"

    asyncio.run(run())


def test_pre_tool_call_formats_and_renders(monkeypatch):
    async def run():
        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "npm test"}, task_id="session-1"
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert adapter.sent[0][1] == "```\n▰ 🧰 Tools\n💻 terminal: npm test · running\n```"

    asyncio.run(run())


def test_background_review_tool_calls_are_suppressed(monkeypatch):
    async def run():
        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        monkeypatch.setattr(
            hermes_progress_tail.plugin.threading,
            "current_thread",
            lambda: type("Thread", (), {"name": "bg-review"})(),
        )
        hermes_progress_tail._on_pre_tool_call(
            "skill_manage",
            {"action": "patch", "name": "hmx-development-version-control"},
            task_id="session-1",
            session_id="session-1",
            tool_call_id="bg-skill",
        )
        hermes_progress_tail._on_post_tool_call(
            "skill_manage",
            {"action": "patch", "name": "hmx-development-version-control"},
            result='{"success": true}',
            task_id="session-1",
            session_id="session-1",
            tool_call_id="bg-skill",
        )
        await asyncio.sleep(0.05)

        assert adapter.sent == []
        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1")
        assert list(ctx.tool_lines) == []
        assert ctx.tool_started_count == 0
        assert ctx.tool_completed_count == 0

    asyncio.run(run())


def test_post_llm_finalize_with_empty_session_id_uses_active_session(monkeypatch):
    async def run():
        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "first turn"}, task_id="session-1"
        )
        await asyncio.sleep(0.05)

        hermes_progress_tail._on_post_llm_call(session_id="")
        await asyncio.sleep(0.05)

        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1")
        assert ctx.progress_state == "finalized"

        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "second turn"}, task_id="session-1"
        )
        await asyncio.sleep(0.05)

        assert len(adapter.sent) == 2
        assert "first turn" in adapter.sent[0][1]
        assert "second turn" in adapter.sent[1][1]
        assert "first turn" not in adapter.sent[1][1]

    asyncio.run(run())


def test_internal_auto_resume_message_registers_progress_context(monkeypatch):
    async def run():
        adapter = Adapter()
        adapter.set_session_store(SessionStore())

        async def noop_handler(event):
            return None

        adapter.set_message_handler(noop_handler)
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )

        from hermes_progress_tail.hooks.monkeypatches import (
            install_monkeypatches,
            uninstall_monkeypatches,
        )

        class InternalEvent(Event):
            internal = True

        # Hermes core auto-resumes restart-interrupted sessions by sending an
        # internal synthetic MessageEvent. Core intentionally skips
        # pre_gateway_dispatch for internal events, so the plugin must register
        # its progress context from the adapter message-handler monkeypatch.
        uninstall_monkeypatches(Adapter)
        install_monkeypatches(Adapter)
        try:
            await adapter.handle_message(InternalEvent())  # type: ignore[attr-defined]
        finally:
            uninstall_monkeypatches(Adapter)
        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "resume tool"}, task_id="session-1"
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert "resume tool" in adapter.sent[0][1]

    asyncio.run(run())
