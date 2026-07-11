import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.monkeypatches import (
    install_monkeypatches,
    uninstall_monkeypatches,
)
from hermes_progress_tail.plugin import _on_pre_gateway_dispatch
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import ReasoningEvent, SessionContext
from tests.support.rendering import EditableAdapter


class Source:
    platform = type("P", (), {"value": "discord"})()
    chat_id = "chat"
    thread_id = "thread"
    user_id = "user"
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


def test_reasoning_tail_renders_section_with_latest_lines():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "reasoning": {"max_lines": 2, "min_update_chars": 1},
                    }
                }
            )
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            ReasoningEvent("s1", "k1", "discord", "first line\nsecond line\nthird line"), force=True
        )

        assert adapter.sent
        assert adapter.sent[0][1] == "▰ 💭 Reasoning\nsecond line\nthird line"

    asyncio.run(run())


def test_reasoning_and_tools_share_one_progress_bubble():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "reasoning": {"min_update_chars": 1},
                    }
                }
            )
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            ReasoningEvent("s1", "k1", "discord", "need inspect hooks"), force=True
        )
        from hermes_progress_tail.state import ToolEvent

        await renderer.handle_event(
            ToolEvent("s1", "k1", "discord", "🔎 search_files: reasoning_callback"), force=True
        )

        assert len(adapter.sent) == 1
        assert (
            adapter.edits[-1][2]
            == "▰ 💭 Reasoning\nneed inspect hooks\n\n▰ 🧰 Tools\n🔎 search_files: reasoning_callback"
        )

    asyncio.run(run())


def test_monkeypatch_captures_agent_reasoning_delta(monkeypatch):
    async def run():
        import hermes_progress_tail.plugin as plugin

        adapter = EditableAdapter()
        plugin._renderer = None
        monkeypatch.setattr(
            plugin,
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
        _on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        class FakeAgent:
            def __init__(self):
                self.session_id = "session-1"
                self.gateway_session_key = "key-1"
                self.platform = "discord"
                self.chat_id = "chat"
                self.thread_id = "thread"
                self.reasoning_callback = None

            def _fire_reasoning_delta(self, text):
                return f"original:{text}"

        uninstall_monkeypatches(FakeAgent)
        install_monkeypatches(FakeAgent)
        agent = FakeAgent()

        assert agent.reasoning_callback is not None
        assert (
            agent._fire_reasoning_delta("thinking about hooks") == "original:thinking about hooks"
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert "thinking about hooks" in adapter.sent[0][1]
        uninstall_monkeypatches(FakeAgent)

    asyncio.run(run())


def test_monkeypatch_preserves_keyword_call_shape(monkeypatch):
    async def run():
        import hermes_progress_tail.plugin as plugin

        adapter = EditableAdapter()
        plugin._renderer = None
        monkeypatch.setattr(
            plugin,
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
        _on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        class FakeAgent:
            def __init__(self):
                self.session_id = "session-1"
                self.gateway_session_key = "key-1"
                self.reasoning_callback = None

            def _fire_reasoning_delta(self, delta="", *, source="provider"):
                return f"original:{delta}:{source}"

        uninstall_monkeypatches(FakeAgent)
        install_monkeypatches(FakeAgent)
        agent = FakeAgent()

        assert (
            agent._fire_reasoning_delta(delta="keyword thinking", source="test")
            == "original:keyword thinking:test"
        )
        await asyncio.sleep(0.05)

        assert adapter.sent
        assert "keyword thinking" in adapter.sent[0][1]
        uninstall_monkeypatches(FakeAgent)

    asyncio.run(run())
