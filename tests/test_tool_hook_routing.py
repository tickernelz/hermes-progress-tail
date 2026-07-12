import asyncio

import hermes_progress_tail
from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.state import SessionContext
from tests.support.gateway import Adapter, Event, Gateway, SessionStore


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


def test_tool_hook_uses_agent_session_key_when_hook_session_id_is_stale(monkeypatch):
    async def run():
        from hermes_progress_tail.hooks.agent import (
            _pop_tool_agent_context,
            _push_tool_agent_context,
        )

        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        agent = type(
            "Agent",
            (),
            {"session_id": "stale-after-interrupt", "gateway_session_key": "key-1"},
        )()
        item = {
            "agent": agent,
            "tool_name": "read_file",
            "task_id": "turn-uuid",
            "session_id": "stale-after-interrupt",
            "session_key": "key-1",
            "tool_call_id": "call-1",
            "messages": [],
        }
        _push_tool_agent_context(item)
        try:
            hermes_progress_tail._on_pre_tool_call(
                "read_file",
                {"path": "README.md"},
                task_id="turn-uuid",
                session_id="stale-after-interrupt",
                tool_call_id="call-1",
            )
        finally:
            _pop_tool_agent_context(item)
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert "read_file: README.md" in adapter.sent[0][1]

    asyncio.run(run())


def test_tool_hook_prefers_stable_session_key_over_stale_existing_session_id(monkeypatch):
    async def run():
        from hermes_progress_tail.hooks.agent import (
            _pop_tool_agent_context,
            _push_tool_agent_context,
        )

        current_adapter = Adapter()
        stale_adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        hermes_progress_tail._on_pre_gateway_dispatch(
            Event(), Gateway(current_adapter), SessionStore()
        )
        renderer = hermes_progress_tail._get_renderer()
        renderer.register_context(
            SessionContext(
                session_id="stale-after-interrupt",
                session_key="",
                platform="discord",
                chat_id="stale-chat",
                thread_id="thread",
                adapter=stale_adapter,
                loop=asyncio.get_running_loop(),
                tools_enabled=True,
                timestamp=False,
            )
        )

        item = {
            "agent": type(
                "Agent",
                (),
                {"session_id": "stale-after-interrupt", "gateway_session_key": "key-1"},
            )(),
            "tool_name": "search_files",
            "task_id": "turn-uuid",
            "session_id": "stale-after-interrupt",
            "session_key": "key-1",
            "tool_call_id": "call-1",
            "messages": [],
        }
        _push_tool_agent_context(item)
        try:
            hermes_progress_tail._on_pre_tool_call(
                "search_files",
                {"pattern": "needle"},
                task_id="turn-uuid",
                session_id="stale-after-interrupt",
                tool_call_id="call-1",
            )
        finally:
            _pop_tool_agent_context(item)
        await asyncio.sleep(0.05)

        assert current_adapter.sent
        assert stale_adapter.sent == []
        assert "search_files" in current_adapter.sent[0][1]

    asyncio.run(run())


def test_tool_hook_reactivates_finalized_context_after_interrupt_followup(monkeypatch):
    async def run():
        adapter = Adapter()
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        )
        hermes_progress_tail._on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())
        renderer = hermes_progress_tail._get_renderer()
        ctx = renderer.find_context("session-1", "key-1")

        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "before interrupt"}, task_id="key-1", session_id="session-1"
        )
        await asyncio.sleep(0.05)
        await renderer.finalize(session_id="session-1", session_key="key-1", success=False)
        assert ctx.progress_state == "finalized"
        assert list(ctx.tool_lines) == []

        hermes_progress_tail._on_pre_tool_call(
            "terminal", {"command": "after follow-up"}, task_id="key-1", session_id="session-1"
        )
        await asyncio.sleep(0.05)

        assert ctx.progress_state == "active"
        assert any("after follow-up" in line for line in ctx.tool_lines)

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
