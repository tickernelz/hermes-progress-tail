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


def test_saturated_checkpoint_keeps_newest_reasoning_progress_and_fits_rich_limit():
    """A fully saturated checkpoint must still deliver newest context.

    Regression: budgets sized for the legacy 4096 cap truncated the question and
    dropped live context, so the user could not tell what was being asked.
    """
    import time
    from types import SimpleNamespace

    from hermes_progress_tail.models.decision import DecisionRecord, DecisionState
    from hermes_progress_tail.models.state_records import AssistantLine
    from hermes_progress_tail.rendering.clarify_checkpoint import _SOURCE_BUDGET
    from hermes_progress_tail.rendering.delivery import _fit_message, _message_limit

    ctx = SessionContext("s", "k", "telegram", "chat", None, None, None, agent_label="Jono")
    ctx.decision = DecisionState(records=deque(maxlen=48))
    ctx.reasoning.text = (
        "**Deep debug**\n\n"
        + ("The typecheck error points at src/index.ts lines 422-429. " * 200)
        + "FINAL THOUGHT: the overload changed upstream."
    )
    for i in range(60):
        ctx.assistant.lines.append(
            AssistantLine(
                text=f"progress {i}: compared merged tree with origin baseline", created_at=float(i)
            )
        )
    for i in range(40):
        ctx.decision.records.append(
            DecisionRecord(
                "tool",
                f"terminal: typecheck · failed · TS2345 at line {400 + i} " * 3,
                f"tool:{i}",
                30,
                time.monotonic() + i,
            )
        )

    text = compose_checkpoint(
        ctx,
        {
            "question": "Which fix should I apply? " * 60,
            "choices": ["Operation lock ordering " * 8, "Rewrite handler signature " * 8],
        },
        7,
    )

    assert len(text) <= _SOURCE_BUDGET
    assert "FINAL THOUGHT" in text, "newest reasoning must reach the checkpoint"
    assert "progress 59" in text, "newest progress line must reach the checkpoint"
    assert "Reasoning\n" in text and "Progress\n" in text

    limit = _message_limit(SimpleNamespace(platform="telegram"))
    assert len(_fit_message(text, limit)) == len(text), "checkpoint must fit the rich bubble uncut"


def test_hard_saturation_drops_oldest_context_not_newest():
    """At the hard cap the OLDEST history is dropped, never the newest detail.

    User contract: maximization targets the latest detail, so a genuinely
    saturated checkpoint head-truncates (drops old history) while preserving
    the mandatory header and the most recent reasoning/progress.
    """
    import time

    from hermes_progress_tail.models.decision import DecisionRecord, DecisionState
    from hermes_progress_tail.models.state_records import AssistantLine
    from hermes_progress_tail.rendering.clarify_checkpoint import _SOURCE_BUDGET

    ctx = SessionContext("s", "k", "telegram", "chat", None, None, None, agent_label="Jono")
    ctx.decision = DecisionState(records=deque(maxlen=48))
    ctx.reasoning.text = (
        "**Deep debug**\n\n" + ("analysis sentence. " * 3000) + "FINAL NEWEST THOUGHT here."
    )
    for i in range(60):
        ctx.assistant.lines.append(
            AssistantLine(text=f"progress {i}: " + ("detail " * 40), created_at=float(i))
        )
    for i in range(48):
        ctx.decision.records.append(
            DecisionRecord(
                "tool", f"record {i} " + ("evidence " * 150), f"t{i}", 30, time.monotonic() + i
            )
        )

    text = compose_checkpoint(
        ctx,
        {"question": "Which fix? " * 500, "choices": ["Option A " * 200, "Option B " * 200]},
        9,
    )

    assert len(text) <= _SOURCE_BUDGET
    assert text.startswith("Jono needs your input"), "mandatory header must survive"
    assert "Checkpoint #9" in text
    assert "FINAL NEWEST THOUGHT" in text, "newest reasoning must survive saturation"
    assert "progress 59" in text, "newest progress must survive saturation"
    assert "progress 0:" not in text, "oldest progress is what gets dropped"


def test_checkpoint_without_live_context_keeps_legacy_shape():
    ctx = SessionContext("s", "k", "discord", "chat", None, None, None, agent_label="Ada")
    from hermes_progress_tail.models.decision import DecisionState

    ctx.decision = DecisionState(records=deque(maxlen=24))
    text = compose_checkpoint(ctx, {"question": "Choose?", "choices": ["one"]}, 2)
    assert "Reasoning" not in text
    assert "Progress" not in text
    assert text.startswith("Ada needs your input")
