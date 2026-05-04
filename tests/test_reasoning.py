import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.monkeypatches import (
    _capture_inline_reasoning,
    install_monkeypatches,
    uninstall_monkeypatches,
)
from hermes_progress_tail.plugin import _on_pre_gateway_dispatch
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering.reasoning import render_reasoning_tail
from hermes_progress_tail.state import ReasoningEvent, SessionContext


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
        assert adapter.sent[0][1] == "💭 Reasoning\nsecond line\nthird line"

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
            == "💭 Reasoning\nneed inspect hooks\n\n🧰 Tools\n🔎 search_files: reasoning_callback"
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


def test_gpt55_markdown_reasoning_tail_uses_latest_semantic_block():
    text = """
It seems like the simpler approach might be the best for now! Let's see how the curl check goes before deciding on any further steps.

**Refining command clarity**

I need to avoid weird command typos, so using larger text and fewer repetitions might help. I'll write a new prompt ensuring no misspellings or the word terminal is used.
"""

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert rendered == (
        "Refining command clarity\n"
        "I need to avoid weird command typos, so using larger text and fewer repetitions might help. I'll write a new prompt ensuring no misspellings or the word terminal is used."
    )
    assert "simpler approach" not in rendered


def test_reasoning_tail_extracts_inline_think_tags_automatically():
    text = "Visible intro\n<think>Need inspect renderer.\nRun targeted tests.</think>\nFinal answer"

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert rendered == "Need inspect renderer.\nRun targeted tests."
    assert "<think>" not in rendered
    assert "Final answer" not in rendered


def test_reasoning_tail_handles_unterminated_inline_think_tags():
    text = "Normal content before.\n<thinking>Need inspect monkeypatch and parser.\nThen verify."

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert rendered == "Need inspect monkeypatch and parser.\nThen verify."
    assert "<thinking>" not in rendered


def test_reasoning_tail_strips_provider_delimiters_and_junk():
    text = """
<|channel|>analysis
signature_delta: abc123
encrypted: eyJ0aGlzLWlzLWp1bmsK
**Checking result**
Need continue carefully.
"""

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert rendered == "Checking result\nNeed continue carefully."
    assert "signature" not in rendered.lower()
    assert "encrypted" not in rendered.lower()


def test_reasoning_disabled_blocks_inline_think_capture():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "reasoning": {"enabled": False, "min_update_chars": 1},
                    }
                }
            )
        )
        ctx = SessionContext(
            "s1", "k1", "discord", "chat", None, adapter, asyncio.get_running_loop(), "live_tail"
        )
        ctx.reasoning_enabled = renderer.settings.reasoning.enabled
        renderer.register_context(ctx)

        await renderer.handle_event(
            ReasoningEvent("s1", "k1", "discord", "<think>hidden thinking</think>"), force=True
        )

        assert adapter.sent == []
        assert adapter.edits == []

    asyncio.run(run())


def test_inline_think_capture_handles_split_stream_chunks():
    class Agent:
        pass

    agent = Agent()

    captured, visible = _capture_inline_reasoning(agent, "visible <th")
    assert captured == ""
    assert visible == "visible "

    captured, visible = _capture_inline_reasoning(agent, "ink>hidden")
    assert captured == "hidden"
    assert visible == ""

    captured, visible = _capture_inline_reasoning(agent, " reasoning</think> done")
    assert captured == " reasoning"
    assert visible == " done"


def test_inline_think_capture_handles_split_closing_tag_with_visible_tail():
    class Agent:
        pass

    agent = Agent()

    assert _capture_inline_reasoning(agent, "<think>hidden</th") == ("hidden", "")
    captured, visible = _capture_inline_reasoning(agent, "ink> visible")

    assert captured == ""
    assert visible == " visible"


def test_inline_think_wrapper_fails_open_when_reasoning_context_missing(monkeypatch):
    from hermes_progress_tail.monkeypatches import _wrap_stream_delta_callback

    class Agent:
        session_id = "missing"
        gateway_session_key = "missing"

    seen = []
    wrapped = _wrap_stream_delta_callback(Agent(), lambda text: seen.append(text))

    wrapped("<think>should stay visible</think> hello")

    assert seen == ["<think>should stay visible</think> hello"]


def test_inline_think_wrapper_fails_open_when_capture_schedule_fails(monkeypatch):
    import hermes_progress_tail.plugin as plugin
    from hermes_progress_tail.monkeypatches import _wrap_stream_delta_callback

    class Agent:
        session_id = "session-1"
        gateway_session_key = "key-1"

    class Renderer:
        settings = load_settings({"progress_tail": {"reasoning": {"enabled": True}}})

        def find_context(self, session_id, session_key=""):
            ctx = SessionContext("session-1", "key-1", "discord", "chat", None, None, None)
            ctx.reasoning_enabled = True
            return ctx

    seen = []
    monkeypatch.setattr(plugin, "_get_renderer", lambda: Renderer())
    monkeypatch.setattr(
        plugin,
        "on_reasoning_delta_from_agent",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("schedule failed")),
    )
    wrapped = _wrap_stream_delta_callback(Agent(), lambda text: seen.append(text))

    wrapped("<think>hidden</think> visible")

    assert seen == ["<think>hidden</think> visible"]


def test_inline_think_capture_fails_open_after_large_unterminated_block():
    class Agent:
        pass

    agent = Agent()

    captured, visible = _capture_inline_reasoning(agent, "<think>" + "x" * 8001)

    assert captured == ""
    assert visible == "<think>" + "x" * 8001
    assert agent._hermes_progress_tail_inline_think_state["inside"] is False


def test_inline_think_wrapper_fails_open_with_split_opening_tag_when_schedule_fails(monkeypatch):
    import hermes_progress_tail.plugin as plugin
    from hermes_progress_tail.monkeypatches import _wrap_stream_delta_callback

    class Agent:
        session_id = "session-1"
        gateway_session_key = "key-1"

    class Renderer:
        settings = load_settings({"progress_tail": {"reasoning": {"enabled": True}}})

        def find_context(self, session_id, session_key=""):
            ctx = SessionContext("session-1", "key-1", "discord", "chat", None, None, None)
            ctx.reasoning_enabled = True
            return ctx

    seen = []
    agent = Agent()
    monkeypatch.setattr(plugin, "_get_renderer", lambda: Renderer())
    monkeypatch.setattr(
        plugin,
        "on_reasoning_delta_from_agent",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("schedule failed")),
    )
    wrapped = _wrap_stream_delta_callback(agent, lambda text: seen.append(text))

    wrapped("visible <th")
    wrapped("ink>hidden visible")

    assert seen == ["visible ", "<think>hidden visible"]


def test_inline_think_wrapper_fails_open_with_split_opening_tag_when_context_disappears(
    monkeypatch,
):
    import hermes_progress_tail.plugin as plugin
    from hermes_progress_tail.monkeypatches import _wrap_stream_delta_callback

    class Agent:
        session_id = "session-1"
        gateway_session_key = "key-1"

    class Renderer:
        settings = load_settings({"progress_tail": {"reasoning": {"enabled": True}}})
        enabled = True

        def find_context(self, session_id, session_key=""):
            if not self.enabled:
                return None
            ctx = SessionContext("session-1", "key-1", "discord", "chat", None, None, None)
            ctx.reasoning_enabled = True
            return ctx

    renderer = Renderer()
    seen = []
    monkeypatch.setattr(plugin, "_get_renderer", lambda: renderer)
    wrapped = _wrap_stream_delta_callback(Agent(), lambda text: seen.append(text))

    wrapped("visible <th")
    renderer.enabled = False
    wrapped("ink>hidden visible")

    assert seen == ["visible ", "<think>hidden visible"]
