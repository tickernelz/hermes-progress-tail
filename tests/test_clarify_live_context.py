"""Regression tests: newest-tail capping and live context in clarify checkpoints."""

from collections import deque

from hermes_progress_tail.models.state import SessionContext
from hermes_progress_tail.rendering.clarify_checkpoint import compose_checkpoint
from hermes_progress_tail.rendering.reasoning import render_reasoning_tail


def test_capped_headed_reasoning_keeps_newest_tail_not_frozen_head():
    """Streaming appends land at the end; the cap must show them.

    Regression: _cap_chars used truncate_to_sentence_boundary(body, budget),
    which keeps body[:budget] — the OLD head. Long single-block reasoning froze
    at stale text (user-visible as 'stuck at Unles...') while the stream moved on.
    """
    heading = "**Debugging**\n"
    old_body = "Hold on maybe the error is real and comes from a different src file. " * 100
    newest = "first SECOND THIRD Unless......"

    out = render_reasoning_tail(
        heading + old_body + newest, max_lines=30, max_chars=6000, redact=False
    )

    assert out.startswith("**Debugging**")
    assert len(out) <= 6000
    assert "Unless......" in out, "newest streamed tail must survive the cap"


def test_capped_plain_reasoning_still_keeps_tail():
    text = ("old analysis. " * 500) + "NEWEST Unless......"
    out = render_reasoning_tail(text, max_lines=30, max_chars=6000, redact=False)
    assert len(out) <= 6000
    assert "Unless......" in out


def test_checkpoint_includes_live_reasoning_and_progress_context():
    ctx = SessionContext("s", "k", "discord", "chat", None, None, None, agent_label="Jono")
    from hermes_progress_tail.models.decision import DecisionState

    ctx.decision = DecisionState(records=deque(maxlen=24))
    ctx.reasoning.text = (
        "**Why I am asking**\n\n" + ("analysis step. " * 200) + "conclusion Unless......"
    )

    from hermes_progress_tail.models.state_records import AssistantLine

    for i in range(6):
        ctx.assistant.lines.append(
            AssistantLine(text=f"progress note {i}: investigating handlers", created_at=i)
        )

    text = compose_checkpoint(ctx, {"question": "A or B?", "choices": ["A", "B"]}, 3)

    assert "Reasoning\n" in text
    assert "Unless" in text, "latest reasoning must appear in the checkpoint"
    assert "Progress\n" in text
    assert "progress note" in text, "assistant progress history must appear"


def test_checkpoint_without_live_context_keeps_legacy_shape():
    ctx = SessionContext("s", "k", "discord", "chat", None, None, None, agent_label="Ada")
    from hermes_progress_tail.models.decision import DecisionState

    ctx.decision = DecisionState(records=deque(maxlen=24))
    text = compose_checkpoint(ctx, {"question": "Choose?", "choices": ["one"]}, 2)
    assert "Reasoning" not in text
    assert "Progress" not in text
    assert text.startswith("Ada needs your input")
