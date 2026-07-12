import asyncio
from datetime import datetime, timedelta

import hermes_progress_tail
from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.state import SessionContext
from tests.support.gateway import Adapter, Event, Gateway, SessionStore, StrictTelegramTopicAdapter


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
