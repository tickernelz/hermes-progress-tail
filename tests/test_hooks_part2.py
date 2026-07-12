import asyncio

import hermes_progress_tail
from hermes_progress_tail.settings.loading import load_settings
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


class StrictTelegramTopicAdapter:
    name = "telegram"

    def __init__(self):
        self.sent = []
        self._message_handler = None
        self._session_store = None
        self.config = type("AdapterConfig", (), {"extra": {}})()

    async def send(self, chat_id, content, metadata=None):
        metadata = metadata or {}
        has_topic_metadata = bool(metadata.get("thread_id"))
        has_dm_routing = bool(metadata.get("telegram_dm_topic_reply_fallback"))
        has_anchor = bool(metadata.get("telegram_reply_to_message_id"))
        has_direct_topic = bool(metadata.get("direct_messages_topic_id"))
        if has_topic_metadata and not (has_direct_topic or (has_dm_routing and has_anchor)):
            return type(
                "Result",
                (),
                {
                    "success": False,
                    "message_id": None,
                    "error": "Telegram DM topic delivery requires a reply anchor",
                },
            )()
        self.sent.append((chat_id, content, metadata))
        return type("Result", (), {"success": True, "message_id": "m1", "error": ""})()

    async def edit_message(self, chat_id, message_id, content):
        return type("Result", (), {"success": True, "message_id": message_id, "error": ""})()


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
            install_adapter_monkeypatches,
            uninstall_adapter_monkeypatches,
        )

        class InternalEvent(Event):
            internal = True

        # Hermes core auto-resumes restart-interrupted sessions by sending an
        # internal synthetic MessageEvent. Core intentionally skips
        # pre_gateway_dispatch for internal events, so the plugin must register
        # its progress context from the adapter message-handler monkeypatch.
        uninstall_adapter_monkeypatches(Adapter)
        install_adapter_monkeypatches(Adapter)
        try:
            await adapter.handle_message(InternalEvent())  # type: ignore[attr-defined]
        finally:
            uninstall_adapter_monkeypatches(Adapter)
        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "resume tool"}, task_id="session-1"
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert "resume tool" in adapter.sent[0][1]

    asyncio.run(run())


def test_internal_telegram_auto_resume_context_uses_gateway_topic_binding(monkeypatch):
    async def run():
        class TelegramSource:
            platform = type("P", (), {"value": "telegram"})()
            chat_id = "191060132"
            thread_id = ""
            user_id = "191060132"
            user_id_alt = None
            chat_type = "dm"
            message_id = "9001"

        class InternalTelegramEvent:
            internal = True
            source = TelegramSource()

        class BoundSessionEntry:
            session_id = "general-session"
            session_key = "agent:main:telegram:dm:191060132:77445"

        class TopicAwareSessionStore:
            def get_or_create_session(self, source):
                assert getattr(source, "thread_id", "") == "77445"
                return BoundSessionEntry()

        class BindingDB:
            def get_telegram_topic_binding(self, *, chat_id, thread_id):
                assert chat_id == "191060132"
                assert thread_id == "77445"
                return {"session_id": "bound-session"}

        class GatewayWithTopicBinding:
            _TELEGRAM_GENERAL_TOPIC_IDS = frozenset({"", "1"})

            def __init__(self, adapter):
                self.adapters = {TelegramSource.platform: adapter, "telegram": adapter}
                self.config = type(
                    "Config",
                    (),
                    {"group_sessions_per_user": True, "thread_sessions_per_user": False},
                )()
                self._session_db = BindingDB()

            def _recover_telegram_topic_thread_id(self, source):
                assert getattr(source, "thread_id", "") == ""
                return "77445"

            async def handle(self, event):
                return None

        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        adapter = Adapter()
        adapter.set_session_store(TopicAwareSessionStore())
        gateway = GatewayWithTopicBinding(adapter)

        from hermes_progress_tail.hooks.monkeypatches import (
            install_adapter_monkeypatches,
            uninstall_adapter_monkeypatches,
        )

        uninstall_adapter_monkeypatches(Adapter)
        install_adapter_monkeypatches(Adapter)
        try:
            adapter.set_message_handler(gateway.handle)
            await adapter.handle_message(InternalTelegramEvent())  # type: ignore[attr-defined]
        finally:
            uninstall_adapter_monkeypatches(Adapter)

        renderer = hermes_progress_tail._get_renderer()
        assert renderer.find_context("general-session") is None
        ctx = renderer.find_context("bound-session")
        assert isinstance(ctx, SessionContext)
        assert ctx.thread_id == "77445"

        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "internal resume"}, task_id="bound-session"
        )
        await asyncio.sleep(0.05)
        assert adapter.sent
        assert "internal resume" in adapter.sent[0][1]

    asyncio.run(run())


def test_adapter_gateway_capture_is_cleared_when_handler_is_unbound():
    class GatewayWithBoundHandler:
        async def handle(self, event):
            return None

    async def unbound_handler(event):
        return None

    from hermes_progress_tail.hooks.monkeypatches import (
        install_adapter_monkeypatches,
        uninstall_adapter_monkeypatches,
    )

    adapter = Adapter()
    gateway = GatewayWithBoundHandler()
    uninstall_adapter_monkeypatches(Adapter)
    install_adapter_monkeypatches(Adapter)
    try:
        adapter.set_message_handler(gateway.handle)
        assert getattr(adapter, "_hermes_progress_tail_gateway", None) is gateway

        adapter.set_message_handler(unbound_handler)
        assert not hasattr(adapter, "_hermes_progress_tail_gateway")
    finally:
        uninstall_adapter_monkeypatches(Adapter)
