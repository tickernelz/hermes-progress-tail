import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.monkeypatches import (
    _capture_inline_reasoning,
    install_monkeypatches,
    uninstall_monkeypatches,
)
from hermes_progress_tail.plugin import _on_pre_gateway_dispatch
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering.reasoning import normalize_reasoning_text, render_reasoning_tail
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


def test_gpt55_markdown_reasoning_tail_uses_latest_semantic_block():
    text = """
It seems like the simpler approach might be the best for now! Let's see how the curl check goes before deciding on any further steps.

**Refining command clarity**

I need to avoid weird command typos, so using larger text and fewer repetitions might help. I'll write a new prompt ensuring no misspellings or the word terminal is used.
"""

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert rendered == (
        "**Refining command clarity**\n"
        "I need to avoid weird command typos, so using larger text and fewer repetitions might help. I'll write a new prompt ensuring no misspellings or the word terminal is used."
    )
    assert "simpler approach" not in rendered


def test_gpt55_inline_adjacent_bold_heading_starts_latest_block():
    text = """**Planning implementation steps**

Some further work to complete it, which might be a bit complex. We should inspect more, maybe execute some code to ensure it's robust. We will plan in the analysis and remember to use the right tools for editing, ensuring we understand the status config and look at line 500 onwards of the mixin for that. **Implementing Tool Use**

I need to implement a careful plan using the tools available, but I should also inspect whether to continue with existing tasks or APIs. Perhaps existing tests could guide this process.
"""

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert rendered.startswith("**Implementing Tool Use**\n")
    assert "Some further work" not in rendered
    assert "ome further work" not in rendered
    assert "for that. **Implementing Tool Use**" not in rendered
    assert "I need to implement a careful plan" in rendered


def test_gpt55_inline_adjacent_bold_heading_is_own_line_after_normalization():
    text = "Previous body sentence. **Implementing Tool Use**\nI need to implement carefully."

    normalized = normalize_reasoning_text(text)

    assert "sentence.\n\n**Implementing Tool Use**\n" in normalized
    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)
    assert rendered == "**Implementing Tool Use**\nI need to implement carefully."


def test_gpt55_inline_bold_heading_without_space_is_own_line_after_normalization():
    text = (
        "**Continuing implementation steps**\n\n"
        "I need to keep going and ensure fields are correct for request types and statuses."
        "**Reviewing cancellation status logic**\n"
        "I'm examining the cancellation status."
    )

    normalized = normalize_reasoning_text(text)

    assert "statuses.\n\n**Reviewing cancellation status logic**\n" in normalized
    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)
    assert (
        rendered
        == "**Reviewing cancellation status logic**\nI'm examining the cancellation status."
    )


def test_gpt55_inline_bold_heading_with_following_body_without_newline_gets_clean_spacing():
    text = (
        "**Continuing implementation steps**\n\n"
        "I should verify statuses.**Reviewing cancellation status logic**"
        "I'm examining the cancellation status."
    )

    normalized = normalize_reasoning_text(text)

    assert "statuses.\n\n**Reviewing cancellation status logic**\nI'm examining" in normalized
    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)
    assert (
        rendered
        == "**Reviewing cancellation status logic**\nI'm examining the cancellation status."
    )


def test_gpt55_reasoning_tail_can_show_two_complete_heading_blocks_when_budget_allows():
    text = (
        "**Continuing implementation steps**\n\n"
        "I need to keep going with the implementation."
        "**Reviewing cancellation status logic**"
        "I'm examining the cancellation status."
    )

    rendered = render_reasoning_tail(text, max_lines=4, max_chars=600, redact=False)

    assert rendered == (
        "**Continuing implementation steps**\n"
        "I need to keep going with the implementation.\n\n"
        "**Reviewing cancellation status logic**\n"
        "I'm examining the cancellation status."
    )


def test_inline_bold_heading_normalization_preserves_sentence_after_non_heading_bold():
    text = "Previous body. **This is very important.**Next sentence continues."

    normalized = normalize_reasoning_text(text)

    assert normalized == text


def test_inline_bold_sentence_not_heading_does_not_drop_content():
    text = "I found a critical observation. **This is very important.**\nNow continue with implementation."

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert "**This is very important.**" in rendered
    assert "Now continue with implementation." in rendered


def test_inline_bold_overlong_heading_candidate_does_not_drop_content():
    text = "Previous body. **This bold section title is intentionally far too long to be accepted as a compact heading by the formatter rules**\nNow continue with implementation."

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert "intentionally far too long" in rendered
    assert "Now continue with implementation." in rendered


def test_inline_bold_phrase_not_heading_when_mid_sentence_continues():
    text = "I should use **safe defaults** here instead of adding config knobs. Then continue normally."

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert rendered == text


def test_gpt55_markdown_reasoning_tail_preserves_bold_heading_marker():
    text = """
**Planning cancellation metadata**

I'm considering the model-level fields for cancellation requests, such as cancellation reason, who requested it, and when. The engine might store these requests, but the business record could need to keep reasons persistently.
"""

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=600, redact=False)

    assert rendered.startswith("**Planning cancellation metadata**\n")
    assert "I'm considering the model-level fields" in rendered


def test_gpt55_long_body_reasoning_tail_keeps_heading_and_sentence_boundary():
    text = """
**Planning cancellation metadata**

I'm considering the model-level fields for cancellation requests, such as cancellation reason, who requested it, and when. The engine might store these requests, but the business record could need to keep reasons persistently. I think approvals should log comments only for rejections, while cancellations should definitely include reasons submitted by users. Maybe I'll plan to add a metadata field for the request, like request_reason or request_payload in JSON. Though, I should keep the schema minimal and not overfit the first workflow.
"""

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=260, redact=False)

    assert rendered.startswith("**Planning cancellation metadata**\n")
    assert rendered.endswith("...")
    assert not rendered.endswith("I ")
    assert "The engine might store" not in rendered or not rendered.endswith("I ")


def test_gpt55_live_reasoning_buffer_preserves_heading_when_max_chars_truncates():
    text = """
**Planning cancellation metadata**

I'm considering the model-level fields for cancellation requests, such as cancellation reason, who requested it, and when. The engine might store these requests, but the business record could need to keep reasons persistently. I think approvals should log comments only for rejections, while cancellations should definitely include reasons submitted by users. Maybe I'll plan to add a metadata field for the request, like request_reason or request_payload in JSON. Though, I should keep the schema minimal and not overfit the first workflow.
"""
    event = ReasoningEvent("s1", "k1", "discord", text)
    renderer = ProgressRenderer(
        load_settings({"progress_tail": {"reasoning": {"max_chars": 260, "min_update_chars": 1}}})
    )
    ctx = SessionContext("s1", "k1", "discord", "chat", None, EditableAdapter(), None, "live_tail")

    renderer._append_reasoning(ctx, event)

    rendered = render_reasoning_tail(ctx.reasoning_text, max_lines=3, max_chars=260, redact=False)
    assert rendered.startswith("**Planning cancellation metadata**\n")
    assert not rendered.endswith("I ")


def test_capped_markdown_heading_reasoning_preserves_heading():
    text = """
## Planning cancellation metadata

The first sentence is intentionally long enough that it needs capping but the markdown heading must remain visible. The second sentence should not cause the renderer to fall back into raw tail mode.
"""

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=105, redact=False)

    assert rendered.startswith("Planning cancellation metadata\n")
    assert rendered.endswith("...")


def test_capped_colon_heading_reasoning_preserves_heading():
    text = """
Planning cancellation metadata:

The first sentence is intentionally long enough that it needs capping but the colon heading must remain visible. The second sentence should not cause the renderer to fall back into raw tail mode.
"""

    rendered = render_reasoning_tail(text, max_lines=3, max_chars=105, redact=False)

    assert rendered.startswith("Planning cancellation metadata\n")
    assert rendered.endswith("...")


def test_plain_multiline_reasoning_keeps_raw_tail_when_capped():
    text = """Need inspect hooks
first chunk should be old
second chunk should remain
third chunk is newest"""

    rendered = render_reasoning_tail(text, max_lines=4, max_chars=55, redact=False)

    assert rendered == "... second chunk should remain\nthird chunk is newest"
    assert "Need inspect hooks" not in rendered


def test_very_long_gpt55_live_reasoning_buffer_preserves_latest_heading():
    heading = "**Planning cancellation metadata**"
    long_body = " ".join(f"body{i}" for i in range(180))
    renderer = ProgressRenderer(
        load_settings({"progress_tail": {"reasoning": {"max_chars": 80, "min_update_chars": 1}}})
    )
    ctx = SessionContext("s1", "k1", "discord", "chat", None, EditableAdapter(), None, "live_tail")

    renderer._append_reasoning(
        ctx, ReasoningEvent("s1", "k1", "discord", f"{heading}\n\n{long_body}")
    )

    assert ctx.reasoning_text.startswith(heading)
    rendered = render_reasoning_tail(ctx.reasoning_text, max_lines=3, max_chars=80, redact=False)
    assert rendered.startswith(f"{heading}\n")


def test_very_long_colon_heading_live_reasoning_buffer_preserves_latest_heading():
    heading = "Planning cancellation metadata:"
    long_body = " ".join(f"body{i}" for i in range(180))
    renderer = ProgressRenderer(
        load_settings({"progress_tail": {"reasoning": {"max_chars": 80, "min_update_chars": 1}}})
    )
    ctx = SessionContext("s1", "k1", "discord", "chat", None, EditableAdapter(), None, "live_tail")

    renderer._append_reasoning(
        ctx, ReasoningEvent("s1", "k1", "discord", f"{heading}\n\n{long_body}")
    )

    assert ctx.reasoning_text.startswith("Planning cancellation metadata:\n")
    rendered = render_reasoning_tail(ctx.reasoning_text, max_lines=3, max_chars=80, redact=False)
    assert rendered.startswith("Planning cancellation metadata\n")


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

    assert rendered == "**Checking result**\nNeed continue carefully."
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
