import asyncio
from datetime import datetime, timedelta

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


def test_session_context_positional_strategy_compatibility():
    ctx = SessionContext(
        "demo-session",
        "demo-session-key",
        "telegram",
        "demo-chat",
        None,
        None,
        None,
        "live_tail",
        timestamp=False,
    )

    assert ctx.strategy == "live_tail"
    assert ctx.chat_type == ""
    assert ctx.source_message_id is None


def test_telegram_dm_metadata_omits_direct_topic_for_general_topic():
    ctx = SessionContext(
        session_id="session-1",
        session_key="key-1",
        platform="telegram",
        chat_id="chat",
        thread_id="1",
        adapter=None,
        loop=None,
        chat_type="dm",
    )

    assert ctx.metadata == {
        "thread_id": "1",
        "telegram_dm_topic_reply_fallback": True,
    }


def test_telegram_dm_metadata_supports_topic_without_reply_anchor():
    ctx = SessionContext(
        session_id="session-1",
        session_key="key-1",
        platform="telegram",
        chat_id="chat",
        thread_id="77436",
        adapter=None,
        loop=None,
        chat_type="dm",
    )

    assert ctx.metadata == {
        "thread_id": "77436",
        "telegram_dm_topic_reply_fallback": True,
        "direct_messages_topic_id": "77436",
    }


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
        assert adapter.sent[0][1] == "▰ 🧰 Tools\n💻 terminal: npm test · running"

    asyncio.run(run())


def test_telegram_dm_topic_context_uses_bound_session_id(monkeypatch):
    async def run():
        class TelegramSource:
            platform = type("P", (), {"value": "telegram"})()
            chat_id = "191060132"
            thread_id = "77445"
            user_id = "191060132"
            user_id_alt = None
            chat_type = "dm"
            message_id = "9001"

        class TelegramEvent:
            source = TelegramSource()

        class StaleSessionEntry:
            session_id = "stale-session"
            session_key = "agent:main:telegram:dm:191060132:77445"

        class StaleSessionStore:
            def get_or_create_session(self, source):
                return StaleSessionEntry()

        class BindingDB:
            def get_telegram_topic_binding(self, *, chat_id, thread_id):
                assert chat_id == "191060132"
                assert thread_id == "77445"
                return {"session_id": "bound-session"}

        class TelegramGateway:
            _TELEGRAM_GENERAL_TOPIC_IDS = frozenset({"", "1"})

            def __init__(self, adapter):
                self.adapters = {TelegramSource.platform: adapter, "telegram": adapter}
                self.config = type(
                    "Config",
                    (),
                    {"group_sessions_per_user": True, "thread_sessions_per_user": False},
                )()
                self._session_db = BindingDB()

        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )

        hermes_progress_tail._on_pre_gateway_dispatch(
            TelegramEvent(), TelegramGateway(adapter), StaleSessionStore()
        )
        renderer = hermes_progress_tail._get_renderer()

        assert renderer.find_context("stale-session") is None
        ctx = renderer.find_context("bound-session")
        assert isinstance(ctx, SessionContext)
        assert ctx.session_key == "agent:main:telegram:dm:191060132:77445"
        assert ctx.thread_id == "77445"

        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "pwd"}, task_id="bound-session"
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert adapter.sent[0][1] == "▰ 🧰 Tools\n💻 terminal: pwd · running"

    asyncio.run(run())


def test_telegram_dm_topic_context_ignores_stale_binding_after_session_split(monkeypatch):
    async def run():
        now = datetime.now()

        class TelegramSource:
            platform = type("P", (), {"value": "telegram"})()
            chat_id = "191060132"
            thread_id = "77445"
            user_id = "191060132"
            user_id_alt = None
            chat_type = "dm"
            message_id = "9002"

        class TelegramEvent:
            source = TelegramSource()

        class CurrentSessionEntry:
            session_id = "post-compression-session"
            session_key = "agent:main:telegram:dm:191060132:77445"
            updated_at = now

        class CurrentSessionStore:
            def get_or_create_session(self, source):
                return CurrentSessionEntry()

        class BindingDB:
            def get_telegram_topic_binding(self, *, chat_id, thread_id):
                assert chat_id == "191060132"
                assert thread_id == "77445"
                return {
                    "session_id": "pre-compression-session",
                    "updated_at": (now - timedelta(minutes=10)).timestamp(),
                }

        class TelegramGateway:
            _TELEGRAM_GENERAL_TOPIC_IDS = frozenset({"", "1"})

            def __init__(self, adapter):
                self.adapters = {TelegramSource.platform: adapter, "telegram": adapter}
                self.config = type(
                    "Config",
                    (),
                    {"group_sessions_per_user": True, "thread_sessions_per_user": False},
                )()
                self._session_db = BindingDB()

        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )

        hermes_progress_tail._on_pre_gateway_dispatch(
            TelegramEvent(), TelegramGateway(adapter), CurrentSessionStore()
        )
        renderer = hermes_progress_tail._get_renderer()

        assert renderer.find_context("pre-compression-session") is None
        ctx = renderer.find_context("post-compression-session")
        assert isinstance(ctx, SessionContext)
        assert ctx.session_key == "agent:main:telegram:dm:191060132:77445"
        assert ctx.source_message_id == "9002"

    asyncio.run(run())


def test_telegram_dm_topic_context_uses_recovered_topic_binding(monkeypatch):
    async def run():
        class TelegramSource:
            platform = type("P", (), {"value": "telegram"})()
            chat_id = "191060132"
            thread_id = ""
            user_id = "191060132"
            user_id_alt = None
            chat_type = "dm"
            message_id = "9001"

        class TelegramEvent:
            source = TelegramSource()

        class StaleSessionEntry:
            session_id = "general-session"
            session_key = "agent:main:telegram:dm:191060132:77445"

        class StaleSessionStore:
            def get_or_create_session(self, source):
                assert getattr(source, "thread_id", "") == "77445"
                return StaleSessionEntry()

        class BindingDB:
            def get_telegram_topic_binding(self, *, chat_id, thread_id):
                assert chat_id == "191060132"
                assert thread_id == "77445"
                return {"session_id": "bound-session"}

        class RecoveringGateway:
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

        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )

        hermes_progress_tail._on_pre_gateway_dispatch(
            TelegramEvent(), RecoveringGateway(adapter), StaleSessionStore()
        )
        renderer = hermes_progress_tail._get_renderer()

        assert renderer.find_context("general-session") is None
        ctx = renderer.find_context("bound-session")
        assert isinstance(ctx, SessionContext)
        assert ctx.thread_id == "77445"
        assert ctx.source_message_id == "9001"

    asyncio.run(run())


def test_telegram_dm_topic_context_uses_reply_anchor_metadata(monkeypatch):
    async def run():
        class TelegramSource:
            platform = type("P", (), {"value": "telegram"})()
            chat_id = "191060132"
            thread_id = "77436"
            user_id = "user"
            user_id_alt = None
            chat_type = "dm"
            message_id = "9001"

        class TelegramEvent:
            source = TelegramSource()

        class TelegramGateway:
            def __init__(self, adapter):
                self.adapters = {TelegramSource.platform: adapter, "telegram": adapter}
                self.config = type(
                    "Config",
                    (),
                    {"group_sessions_per_user": True, "thread_sessions_per_user": False},
                )()

        adapter = StrictTelegramTopicAdapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        hermes_progress_tail._on_pre_gateway_dispatch(
            TelegramEvent(), TelegramGateway(adapter), SessionStore()
        )

        hermes_progress_tail._on_pre_tool_call("terminal", {"command": "pwd"}, task_id="session-1")
        await asyncio.sleep(0.05)

        assert adapter.sent
        metadata = adapter.sent[0][2]
        assert metadata["thread_id"] == "77436"
        assert metadata["telegram_dm_topic_reply_fallback"] is True
        assert metadata["telegram_reply_to_message_id"] == "9001"
        assert metadata["direct_messages_topic_id"] == "77436"

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
            {"action": "patch", "name": "example-version-control"},
            task_id="session-1",
            session_id="session-1",
            tool_call_id="bg-skill",
        )
        hermes_progress_tail._on_post_tool_call(
            "skill_manage",
            {"action": "patch", "name": "example-version-control"},
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


def test_foreground_tool_hooks_work_from_worker_thread(monkeypatch):
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
            lambda: type("Thread", (), {"name": "Thread-6 (_call)"})(),
        )
        hermes_progress_tail._on_pre_tool_call(
            "terminal",
            {"command": "worker thread tool"},
            task_id="key-1",
            session_id="session-1",
            tool_call_id="fg-terminal",
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert "worker thread tool" in adapter.sent[0][1]

    asyncio.run(run())


def test_background_review_finalize_does_not_finalize_foreground_context(monkeypatch):
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
            "terminal", {"command": "foreground"}, task_id="key-1", session_id="session-1"
        )
        await asyncio.sleep(0.05)

        monkeypatch.setattr(
            hermes_progress_tail.plugin.threading,
            "current_thread",
            lambda: type("Thread", (), {"name": "bg-review:123"})(),
        )
        hermes_progress_tail._on_post_llm_call(session_id="session-1")
        await asyncio.sleep(0.05)

        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1")
        assert ctx.progress_state == "active"

    asyncio.run(run())
