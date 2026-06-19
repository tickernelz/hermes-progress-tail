import asyncio

import hermes_progress_tail
from hermes_progress_tail.config import load_settings


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


def _background_review_agent():
    return type(
        "Agent",
        (),
        {
            "session_id": "session-1",
            "gateway_session_key": "key-1",
            "_memory_write_origin": "background_review",
            "_memory_write_context": "background_review",
            "model": "gpt-5.5",
            "provider": "custom",
        },
    )()


def _foreground_agent():
    return type(
        "Agent",
        (),
        {
            "session_id": "session-1",
            "gateway_session_key": "key-1",
            "model": "gpt-5.5",
            "provider": "custom",
        },
    )()


def _set_worker_thread(monkeypatch):
    monkeypatch.setattr(
        hermes_progress_tail.plugin.threading,
        "current_thread",
        lambda: type("Thread", (), {"name": "Thread-73 (_call)"})(),
    )


def _configure(monkeypatch):
    hermes_progress_tail.plugin._renderer = None
    monkeypatch.setattr(
        hermes_progress_tail.plugin,
        "_load_runtime_settings",
        lambda: load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False},
                    "reasoning": {"min_update_chars": 1},
                }
            }
        ),
    )


def test_background_review_reasoning_on_worker_thread_is_suppressed(monkeypatch):
    async def run():
        adapter = Adapter()
        _configure(monkeypatch)
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        _set_worker_thread(monkeypatch)

        hermes_progress_tail.plugin.on_reasoning_delta_from_agent(
            _background_review_agent(), "Updating verification commands"
        )
        await asyncio.sleep(0.05)

        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1", "key-1")
        assert adapter.sent == []
        assert adapter.edits == []
        assert ctx.reasoning_text == ""

    asyncio.run(run())


def test_background_review_tool_context_on_worker_thread_does_not_reactivate_context(
    monkeypatch,
):
    async def run():
        from hermes_progress_tail.hooks.agent import (
            _pop_tool_agent_context,
            _push_tool_agent_context,
        )

        adapter = Adapter()
        _configure(monkeypatch)
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "foreground"}, task_id="key-1", session_id="session-1"
        )
        await asyncio.sleep(0.05)
        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1", "key-1")
        await renderer.finalize(session_id="session-1", session_key="key-1", success=False)
        assert ctx.progress_state == "finalized"
        adapter.sent.clear()
        adapter.edits.clear()
        _set_worker_thread(monkeypatch)

        item = {
            "agent": _background_review_agent(),
            "tool_name": "skill_manage",
            "task_id": "key-1",
            "session_id": "session-1",
            "session_key": "key-1",
            "tool_call_id": "bg-skill",
            "messages": [],
        }
        _push_tool_agent_context(item)
        try:
            hermes_progress_tail._on_pre_tool_call(
                "skill_manage",
                {"action": "patch", "name": "hermes-webui-development"},
                task_id="key-1",
                session_id="session-1",
                tool_call_id="bg-skill",
            )
        finally:
            _pop_tool_agent_context(item)
        await asyncio.sleep(0.05)

        assert ctx.progress_state == "finalized"
        assert adapter.sent == []
        assert adapter.edits == []
        assert not any("hermes-webui-development" in line for line in ctx.tool_lines)

    asyncio.run(run())


def test_background_review_post_tool_on_worker_thread_does_not_reactivate_context(
    monkeypatch,
):
    async def run():
        from hermes_progress_tail.hooks.agent import (
            _pop_tool_agent_context,
            _push_tool_agent_context,
        )

        adapter = Adapter()
        _configure(monkeypatch)
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "foreground"}, task_id="key-1", session_id="session-1"
        )
        await asyncio.sleep(0.05)
        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1", "key-1")
        await renderer.finalize(session_id="session-1", session_key="key-1", success=False)
        assert ctx.progress_state == "finalized"
        adapter.sent.clear()
        adapter.edits.clear()
        _set_worker_thread(monkeypatch)

        item = {
            "agent": _background_review_agent(),
            "tool_name": "skill_manage",
            "task_id": "key-1",
            "session_id": "session-1",
            "session_key": "key-1",
            "tool_call_id": "bg-skill",
            "messages": [],
        }
        _push_tool_agent_context(item)
        try:
            hermes_progress_tail._on_post_tool_call(
                "skill_manage",
                {"action": "patch", "name": "hermes-webui-development"},
                result='{"success": true}',
                task_id="key-1",
                session_id="session-1",
                tool_call_id="bg-skill",
                duration_ms=10,
            )
        finally:
            _pop_tool_agent_context(item)
        await asyncio.sleep(0.05)

        assert ctx.progress_state == "finalized"
        assert adapter.sent == []
        assert adapter.edits == []
        assert not any("hermes-webui-development" in line for line in ctx.tool_lines)

    asyncio.run(run())


def test_background_review_finalize_on_worker_thread_does_not_finalize_foreground(
    monkeypatch,
):
    async def run():
        adapter = Adapter()
        _configure(monkeypatch)
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "foreground"}, task_id="key-1", session_id="session-1"
        )
        await asyncio.sleep(0.05)
        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1", "key-1")
        assert ctx.progress_state == "active"
        adapter.sent.clear()
        adapter.edits.clear()
        _set_worker_thread(monkeypatch)

        hermes_progress_tail.plugin._on_session_finalize(
            session_id="session-1", platform="telegram", agent=_background_review_agent()
        )
        hermes_progress_tail.plugin._on_post_llm_call(
            session_id="session-1", agent=_background_review_agent()
        )
        await asyncio.sleep(0.05)

        assert ctx.progress_state == "active"
        assert adapter.sent == []
        assert adapter.edits == []

    asyncio.run(run())


def test_background_review_assistant_progress_on_worker_thread_is_suppressed(monkeypatch):
    async def run():
        adapter = Adapter()
        _configure(monkeypatch)
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        _set_worker_thread(monkeypatch)

        handled = hermes_progress_tail.plugin.on_assistant_progress_from_agent(
            _background_review_agent(), "I patched the skill."
        )
        await asyncio.sleep(0.05)

        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1", "key-1")
        assert handled is False
        assert adapter.sent == []
        assert adapter.edits == []
        assert ctx.assistant_latest_text == ""

    asyncio.run(run())


def test_background_review_agent_reasoning_availability_is_false(monkeypatch):
    from hermes_progress_tail.hooks.agent import _agent_reasoning_enabled

    adapter = Adapter()
    _configure(monkeypatch)
    hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
    _set_worker_thread(monkeypatch)

    assert _agent_reasoning_enabled(_background_review_agent()) is False


def test_foreground_reasoning_worker_thread_still_renders(monkeypatch):
    async def run():
        adapter = Adapter()
        _configure(monkeypatch)
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        _set_worker_thread(monkeypatch)

        hermes_progress_tail.plugin.on_reasoning_delta_from_agent(
            _foreground_agent(), "Foreground reasoning"
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert "Foreground reasoning" in adapter.sent[0][1]

    asyncio.run(run())
