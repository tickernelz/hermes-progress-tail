from hermes_progress_tail.config import load_settings
from hermes_progress_tail.monkeypatches import (
    _capture_inline_reasoning,
)
from hermes_progress_tail.state import SessionContext


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
