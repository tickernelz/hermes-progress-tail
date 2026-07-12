import asyncio

from hermes_progress_tail.monkeypatches import _capture_inline_reasoning
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering.reasoning import (
    normalize_reasoning_text,
    render_reasoning_tail,
)
from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.state import ReasoningEvent, SessionContext
from tests.support.rendering import EditableAdapter


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


def test_streaming_glue_missing_sentence_space_is_restored():
    """GPT-5.x reasoning deltas can arrive glued: ``word.Next`` → ``word. Next``."""
    text = "Let me profile the ORM layer to understand where time is spent.Let me plan next steps."
    normalized = normalize_reasoning_text(text)

    assert "spent. Let" in normalized
    assert "spent.Let" not in normalized


def test_streaming_glue_numbered_list_markers_get_newlines():
    """Glued numbered list items ``scenario2.`` must split onto separate lines."""
    text = (
        "Let me:1. Profile actual ORM operations in a real scenario"
        "2. Look at the ORM internals"
        "3. Identify hotspots in the ORM execution path"
        "4. Create a concrete plan with measurable targets"
    )
    normalized = normalize_reasoning_text(text)

    assert "me:\n1. Profile" in normalized
    assert "scenario\n2. Look" in normalized
    assert "path\n4. Create" in normalized
    assert "scenario2." not in normalized
    assert ")3." not in normalized


def test_streaming_glue_full_screenshot_text_renders_structured_list():
    """Exact text from user screenshot: full reasoning with glued list + sentence."""
    text = (
        "The user wants me to analyze ORM performance deeply - all read, search_read, "
        "call_kw operations - and come up with a comprehensive optimization plan. This "
        "is a significant architectural analysis task. Let me start by profiling the ORM "
        "layer to understand where time is spent.Let me:1. Profile actual ORM operations "
        "(read, search_read, call_kw) in a real scenario2. Look at the ORM internals "
        "(models.py, fields.py, query.py)3. Identify hotspots in the ORM execution path"
        "4. Create a concrete plan with measurable targetsI should start with profiling "
        "to get real data, then dive into code analysis."
    )
    rendered = render_reasoning_tail(text, max_lines=6, max_chars=800, redact=False)

    assert "spent. Let" in rendered
    assert "me:\n1. Profile" in rendered
    assert "scenario\n2. Look" in rendered
    assert "query.py)\n3. Identify" in rendered
    assert "path\n4. Create" in rendered
    assert "spent.Let" not in rendered
    assert "scenario2." not in rendered
    assert ")3." not in rendered
    assert "path4." not in rendered


def test_streaming_glue_adjacent_bold_headings_get_separate_lines():
    """GPT-5.6 can concatenate complete bold summaries without separators."""
    text = (
        "**Planning selective history rewrite for plan files**"
        "**Storing canonical preference for planning docs**"
        "**Planning git history rewrite**"
        "**Planning git rebase to drop docs commits**"
        "**Planning ignore commit insertion post-rebase**"
        "**Planning git commit reorder strategy**"
    )

    normalized = normalize_reasoning_text(text)

    assert normalized == (
        "**Planning selective history rewrite for plan files**\n\n"
        "**Storing canonical preference for planning docs**\n\n"
        "**Planning git history rewrite**\n\n"
        "**Planning git rebase to drop docs commits**\n\n"
        "**Planning ignore commit insertion post-rebase**\n\n"
        "**Planning git commit reorder strategy**"
    )


def test_streaming_glue_adjacent_bold_heading_examples_stay_literal():
    text = (
        "Literal: `**Planning one****Planning two**`\n"
        "```md\n**Planning three****Planning four**\n```"
    )

    assert normalize_reasoning_text(text) == text


def test_streaming_glue_does_not_break_camelcase_or_version_numbers():
    """False positive guards: camelCase identifiers and version numbers stay intact."""
    text = "Using callKw to read data from v0.1.93. The searchRead method at step1 should work."
    normalized = normalize_reasoning_text(text)

    # Version number must not get split
    assert "v0.1.93" in normalized
    # camelCase must stay intact (lowercase-lowercase, not sentence boundary)
    assert "callKw" in normalized
    assert "searchRead" in normalized
