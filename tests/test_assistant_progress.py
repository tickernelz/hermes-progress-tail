import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.monkeypatches import install_monkeypatches, uninstall_monkeypatches
from hermes_progress_tail.plugin import _on_pre_gateway_dispatch, _on_pre_tool_call
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import AssistantEvent, ReasoningEvent, SessionContext


class Result:
    def __init__(self, success=True, message_id=None, error=""):
        self.success = success
        self.message_id = message_id
        self.error = error


class EditableAdapter:
    name = "editable"

    def __init__(self):
        self.sent = []
        self.edits = []
        self.next_id = 1

    async def send(self, chat_id, content, metadata=None):
        message_id = f"m{self.next_id}"
        self.next_id += 1
        self.sent.append((chat_id, content, metadata))
        return Result(True, message_id)

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return Result(True, message_id)


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


def test_assistant_progress_renders_separate_section_before_reasoning():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "assistant": {"min_update_chars": 1},
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
            AssistantEvent("s1", "k1", "discord", "Need inspect hooks."), force=True
        )
        await renderer.handle_event(
            ReasoningEvent("s1", "k1", "discord", "hidden thought"), force=True
        )

        assert adapter.edits[-1][2] == (
            "▰ 💬 Progress\nNeed inspect hooks.\n\n▰ 💭 Reasoning\nhidden thought"
        )

    asyncio.run(run())


def test_assistant_progress_respects_tail_limits_and_resets_on_finalize():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "assistant": {"max_lines": 2, "max_chars": 80, "min_update_chars": 1}
                    }
                }
            )
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            AssistantEvent("s1", "k1", "discord", "one\ntwo\nthree"), force=True
        )
        assert adapter.sent[0][1] == "▰ 💬 Progress\ntwo\nthree"

        await renderer.finalize(session_id="s1", success=True)
        next_ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(next_ctx)
        await renderer.handle_event(AssistantEvent("s1", "k1", "discord", "fresh"), force=True)

        assert adapter.sent[-1][1] == "▰ 💬 Progress\nfresh"

    asyncio.run(run())


def test_assistant_progress_replaces_cumulative_interim_text():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"assistant": {"min_update_chars": 1}}})
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            AssistantEvent("s1", "k1", "discord", "Need inspect"), force=True
        )
        await renderer.handle_event(
            AssistantEvent("s1", "k1", "discord", "Need inspect telegram renderer"), force=True
        )

        assert adapter.edits[-1][2] == "▰ 💬 Progress\nNeed inspect telegram renderer"
        assert "Need inspect\nNeed inspect" not in adapter.edits[-1][2]

    asyncio.run(run())


def test_assistant_progress_can_be_disabled_per_platform():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"assistant": {"enabled": False}}})
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        ctx.assistant_enabled = renderer.settings.assistant.enabled
        renderer.register_context(ctx)

        await renderer.handle_event(AssistantEvent("s1", "k1", "discord", "hidden"), force=True)

        assert adapter.sent == []
        assert adapter.edits == []

    asyncio.run(run())


def test_assistant_progress_uses_private_gateway_session_key(monkeypatch):
    import hermes_progress_tail.plugin as plugin
    from hermes_progress_tail.plugin import on_assistant_progress_from_agent

    agent = type("Agent", (), {"session_id": "s1", "_gateway_session_key": "k1"})()
    ctx = SessionContext("s1", "k1", "discord", "chat", None, EditableAdapter(), None, "live_tail")
    scheduled = []

    class Renderer:
        settings = load_settings({"progress_tail": {"assistant": {"enabled": True}}})

        def find_context(self, session_id, session_key=""):
            assert session_id == "s1"
            assert session_key == "k1"
            return ctx

    monkeypatch.setattr(plugin, "_get_renderer", lambda: Renderer())
    monkeypatch.setattr(
        plugin, "_schedule_render", lambda found_ctx, event: scheduled.append(event) or True
    )

    assert on_assistant_progress_from_agent(agent, "Need inspect private key") is True
    assert scheduled
    assert scheduled[0].session_key == "k1"
    assert scheduled[0].text == "Need inspect private key"


def test_assistant_progress_renders_already_streamed_but_does_not_suppress_native(monkeypatch):
    import hermes_progress_tail.plugin as plugin
    from hermes_progress_tail.plugin import on_assistant_progress_from_agent

    agent = type("Agent", (), {"session_id": "s1", "_gateway_session_key": "k1"})()
    ctx = SessionContext("s1", "k1", "discord", "chat", None, EditableAdapter(), None, "live_tail")
    scheduled = []

    class Renderer:
        settings = load_settings({"progress_tail": {"assistant": {"enabled": True}}})

        def find_context(self, session_id, session_key=""):
            return ctx if (session_id, session_key) == ("s1", "k1") else None

    monkeypatch.setattr(plugin, "_get_renderer", lambda: Renderer())
    monkeypatch.setattr(
        plugin, "_schedule_render", lambda found_ctx, event: scheduled.append(event) or True
    )

    assert (
        on_assistant_progress_from_agent(
            agent, "Need inspect streamed commentary", already_streamed=True
        )
        is False
    )
    assert scheduled
    assert scheduled[0].already_streamed is True
    assert scheduled[0].text == "Need inspect streamed commentary"


def test_monkeypatch_falls_back_to_original_interim_send_when_render_cannot_schedule(
    monkeypatch,
):
    import hermes_progress_tail.plugin as plugin
    from hermes_progress_tail.plugin import on_assistant_progress_from_agent

    agent = type("Agent", (), {"session_id": "s1", "gateway_session_key": "k1"})()
    ctx = SessionContext("s1", "k1", "discord", "chat", None, EditableAdapter(), None, "live_tail")

    class Renderer:
        settings = load_settings({"progress_tail": {"assistant": {"enabled": True}}})

        def find_context(self, session_id, session_key=""):
            return ctx

    monkeypatch.setattr(plugin, "_get_renderer", lambda: Renderer())

    assert on_assistant_progress_from_agent(agent, "lost if suppressed") is False


def test_monkeypatch_captures_interim_assistant_commentary_and_suppresses_default_send(monkeypatch):
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
                        "assistant": {"min_update_chars": 1},
                    }
                }
            ),
        )
        _on_pre_gateway_dispatch(Event(), Gateway(adapter), SessionStore())

        callback_seen = []

        class FakeAgent:
            def __init__(self):
                self.session_id = "session-1"
                self._gateway_session_key = "key-1"
                self.platform = "discord"
                self.chat_id = "chat"
                self.thread_id = "thread"
                self.interim_assistant_callback = lambda text, **kwargs: callback_seen.append(
                    (text, kwargs)
                )
                self.reasoning_callback = None
                self.stream_delta_callback = None

            def _fire_reasoning_delta(self, text):
                return None

            def _emit_interim_assistant_message(self, assistant_msg):
                self.interim_assistant_callback(
                    assistant_msg["content"],
                    already_streamed=assistant_msg.get("already_streamed", False),
                )
                return "original"

        uninstall_monkeypatches(FakeAgent)
        install_monkeypatches(FakeAgent)
        agent = FakeAgent()

        assert (
            agent._emit_interim_assistant_message({"content": "Need read more around telegram."})
            is None
        )
        await asyncio.sleep(0.05)

        assert callback_seen == []
        assert adapter.sent
        assert "▰ 💬 Progress\nNeed read more around telegram." in adapter.sent[0][1]
        uninstall_monkeypatches(FakeAgent)

    asyncio.run(run())


def test_monkeypatch_computes_already_streamed_from_agent_checker(monkeypatch):
    import hermes_progress_tail.plugin as plugin

    events = []

    class FakeAgent:
        def __init__(self):
            self.session_id = "s1"
            self._gateway_session_key = "k1"
            self.reasoning_callback = None
            self.stream_delta_callback = None

        def _fire_reasoning_delta(self, text):
            return None

        def _interim_content_was_streamed(self, visible):
            return visible == "already visible"

        def _emit_interim_assistant_message(self, assistant_msg):
            events.append(("original", assistant_msg))
            return "original"

    monkeypatch.setattr(
        plugin,
        "on_assistant_progress_from_agent",
        lambda agent, text, *, already_streamed=False: (
            events.append(("captured", text, already_streamed)) or False
        ),
    )

    uninstall_monkeypatches(FakeAgent)
    install_monkeypatches(FakeAgent)
    agent = FakeAgent()

    assert agent._emit_interim_assistant_message({"content": "already visible"}) == "original"

    assert events[0] == ("captured", "already visible", True)
    assert events[1][0] == "original"
    uninstall_monkeypatches(FakeAgent)


def test_assistant_progress_falls_back_to_unique_session_context(monkeypatch):
    import hermes_progress_tail.plugin as plugin
    from hermes_progress_tail.plugin import on_assistant_progress_from_agent

    agent = type("Agent", (), {"session_id": "s1", "_gateway_session_key": "stale-key"})()
    ctx = SessionContext(
        "s1", "fresh-key", "discord", "chat", None, EditableAdapter(), None, "live_tail"
    )
    scheduled = []

    class Renderer:
        settings = load_settings({"progress_tail": {"assistant": {"enabled": True}}})
        sessions = {"s1": ctx}
        session_keys = {"fresh-key": "s1"}

        def find_context(self, session_id, session_key=""):
            return ctx if (session_id, session_key) == ("s1", "fresh-key") else None

    monkeypatch.setattr(plugin, "_get_renderer", lambda: Renderer())
    monkeypatch.setattr(
        plugin, "_schedule_render", lambda found_ctx, event: scheduled.append(event) or True
    )

    assert on_assistant_progress_from_agent(agent, "Need fallback context") is True
    assert scheduled
    assert scheduled[0].session_key == "fresh-key"


def test_pre_tool_call_does_not_emit_fake_assistant_progress(monkeypatch):
    import hermes_progress_tail.plugin as plugin

    ctx = SessionContext("s1", "k1", "discord", "chat", None, EditableAdapter(), None, "live_tail")
    scheduled = []

    class Renderer:
        settings = load_settings({"progress_tail": {"assistant": {"enabled": True}}})
        sessions = {"s1": ctx}
        session_keys = {"k1": "s1"}

        def find_context(self, session_id, session_key=""):
            return ctx if session_id == "s1" or session_key == "k1" else None

    monkeypatch.setattr(plugin, "_get_renderer", lambda: Renderer())
    monkeypatch.setattr(
        plugin, "_schedule_render", lambda found_ctx, event: scheduled.append(event) or True
    )

    _on_pre_tool_call("search_files", {"pattern": "foo"}, session_id="s1", task_id="k1")

    assert not [event for event in scheduled if isinstance(event, AssistantEvent)]


def test_status_reports_assistant_capture_diagnostics(monkeypatch):
    import hermes_progress_tail.plugin as plugin

    plugin._renderer = None
    plugin._record_assistant_capture(
        "scheduled",
        session_id="s1",
        session_key="k1",
        text="Need inspect token abc123",
        already_streamed=False,
    )
    monkeypatch.setattr(
        plugin, "_load_runtime_config", lambda: {"plugins": {"enabled": ["hermes-progress-tail"]}}
    )
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings({}))

    status = plugin._command("status")

    assert "assistant_capture=scheduled" in status
    assert "synthetic=" not in status
    assert "key_present=True" in status
