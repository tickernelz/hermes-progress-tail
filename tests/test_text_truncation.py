from hermes_progress_tail.models.state import AssistantLine
from hermes_progress_tail.rendering.reasoning import render_reasoning_tail
from hermes_progress_tail.rendering.sections import assistant_tail
from hermes_progress_tail.utils.text import truncate_text


def test_truncate_text_uses_word_boundary():
    assert truncate_text("alpha bravo charlie delta", 18) == "alpha bravo..."


def test_assistant_tail_trims_from_word_boundary():
    rendered = assistant_tail(
        (
            AssistantLine(
                "Progress update should preserve readable words when trimming this visible assistant progress message"
            ),
        ),
        max_lines=1,
        max_chars=32,
    )

    assert rendered == "... assistant progress message"


def test_reasoning_tail_trims_from_word_boundary():
    rendered = render_reasoning_tail(
        "Reasoning should keep coherent words when the visible tail is constrained by the max char budget",
        max_lines=1,
        max_chars=32,
        redact=False,
    )

    assert rendered == "... by the max char budget"
