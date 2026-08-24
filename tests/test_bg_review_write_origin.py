import asyncio
import sys
import types

import hermes_progress_tail
from hermes_progress_tail.settings.loading import load_settings


def _install_fake_skill_provenance(monkeypatch):
    """Stub Hermes core's skill-provenance ContextVar for isolated tests."""
    module = types.ModuleType("tools")
    provenance = types.ModuleType("tools.skill_provenance")
    token_holder: dict[str, object] = {"origin": "foreground"}

    def set_current_write_origin(origin):
        previous = token_holder["origin"]
        token_holder["origin"] = origin or "foreground"
        return ("fake-token", previous)

    def reset_current_write_origin(token):
        token_holder["origin"] = token[1] if isinstance(token, tuple) else "foreground"

    def get_current_write_origin():
        return str(token_holder["origin"])

    provenance.set_current_write_origin = set_current_write_origin
    provenance.reset_current_write_origin = reset_current_write_origin
    provenance.get_current_write_origin = get_current_write_origin
    module.skill_provenance = provenance
    monkeypatch.setitem(sys.modules, "tools", module)
    monkeypatch.setitem(sys.modules, "tools.skill_provenance", provenance)
    return provenance


class Source:
    platform = type("P", (), {"value": "telegram"})()
    chat_id = "chat"
    thread_id = "thread"
    user_id = "user"
    user_id_alt = None
    chat_type = "dm"
    message_id = "source-message-1"


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
    name = "telegram"

    def __init__(self):
        self.sent = []
        self.edits = []
        self.config = type("AdapterConfig", (), {"extra": {}})()

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        return type("Result", (), {"success": True, "message_id": "m1", "error": ""})()

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return type("Result", (), {"success": True, "message_id": message_id, "error": ""})()


def _configure(monkeypatch):
    hermes_progress_tail.plugin._renderer = None
    monkeypatch.setattr(
        hermes_progress_tail.plugin,
        "_load_runtime_settings",
        lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
    )


def _set_pool_worker_thread(monkeypatch):
    """Latest Hermes runs bg-review tools on pool workers NOT named bg-review."""
    monkeypatch.setattr(
        hermes_progress_tail.plugin.threading,
        "current_thread",
        lambda: type("Thread", (), {"name": "ThreadPoolExecutor-1_0"})(),
    )


def test_write_origin_contextvar_marks_worker_thread_as_background_review(monkeypatch):
    """bg-review tool events must be suppressed even on pool worker threads.

    The plugin consults Hermes' skill-provenance write-origin ContextVar in
    addition to the thread-name heuristic.
    """
    provenance = _install_fake_skill_provenance(monkeypatch)

    async def run():
        adapter = Adapter()
        _configure(monkeypatch)
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        _set_pool_worker_thread(monkeypatch)

        token = provenance.set_current_write_origin("background_review")
        try:
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
        finally:
            provenance.reset_current_write_origin(token)

        await asyncio.sleep(0.05)

        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1")
        assert adapter.sent == []
        assert adapter.edits == []
        assert list(ctx.tool_lines) == []
        assert ctx.tool_started_count == 0
        assert ctx.tool_completed_count == 0

    asyncio.run(run())


def test_foreground_write_origin_still_reaches_progress(monkeypatch):
    """Foreground tool events on pool worker threads must stay visible."""
    provenance = _install_fake_skill_provenance(monkeypatch)

    async def run():
        adapter = Adapter()
        _configure(monkeypatch)
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        _set_pool_worker_thread(monkeypatch)

        token = provenance.set_current_write_origin("foreground")
        try:
            hermes_progress_tail._on_pre_tool_call(
                "terminal",
                {"command": "foreground work"},
                task_id="key-1",
                session_id="session-1",
                tool_call_id="fg-term",
            )
        finally:
            provenance.reset_current_write_origin(token)

        await asyncio.sleep(0.05)

        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1", "key-1")
        assert adapter.sent, "foreground tool event should produce a progress bubble"
        assert any("foreground work" in line for line in ctx.tool_lines)

    asyncio.run(run())
