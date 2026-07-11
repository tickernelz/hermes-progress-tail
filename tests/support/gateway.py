from __future__ import annotations


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
