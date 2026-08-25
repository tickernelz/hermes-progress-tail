"""Regression: reasoning/progress selection must fill the configured budget.

Bug: selection collapsed to a single latest paragraph/block regardless of
max_chars, so a 15k-char reasoning trail rendered as a few hundred chars while
the configured 12000-char budget sat unused. The user saw a stub like
"Sekarang a..." in the Reasoning section.
"""

from hermes_progress_tail.models.state_records import AssistantLine
from hermes_progress_tail.rendering.reasoning import render_reasoning_tail
from hermes_progress_tail.rendering.sections import assistant_tail

MAX_LINES = 80
MAX_CHARS = 12000


def _fills_budget(rendered: str, budget: int, *, floor: float = 0.7) -> bool:
    return budget * floor <= len(rendered) <= budget


def test_many_plain_paragraphs_fill_the_char_budget():
    paragraphs = [
        f"Paragraph {index}: " + ("analisis mendalam tentang HMSI Backends. " * 12)
        for index in range(30)
    ]
    text = "\n\n".join(paragraphs)

    rendered = render_reasoning_tail(text, max_lines=MAX_LINES, max_chars=MAX_CHARS, redact=False)

    assert _fills_budget(rendered, MAX_CHARS), f"only {len(rendered)} of {MAX_CHARS} chars used"
    assert "Paragraph 29:" in rendered, "newest paragraph must survive"
    assert "Paragraph 0:" not in rendered, "oldest paragraph is what gets dropped"


def test_many_headed_blocks_fill_the_char_budget():
    blocks = [f"**Step {index}**\n\n" + ("detail analisis mendalam. " * 20) for index in range(30)]
    text = "\n\n".join(blocks)

    rendered = render_reasoning_tail(text, max_lines=MAX_LINES, max_chars=MAX_CHARS, redact=False)

    assert _fills_budget(rendered, MAX_CHARS), f"only {len(rendered)} of {MAX_CHARS} chars used"
    assert "Step 29" in rendered, "newest block must survive"


def test_single_headed_block_keeps_heading_and_newest_body_lines():
    text = "**Deep analysis**\n\n" + "\n".join(
        f"line {index} with some real content here" for index in range(200)
    )

    rendered = render_reasoning_tail(text, max_lines=MAX_LINES, max_chars=MAX_CHARS, redact=False)

    assert rendered.startswith("**Deep analysis**")
    assert len(rendered.splitlines()) <= MAX_LINES
    assert "line 199" in rendered, "newest body line must survive the line budget"
    assert "line 0 with" not in rendered, "oldest body line is what gets dropped"


def test_single_long_paragraph_still_fills_budget():
    text = "Sekarang aku perlu memeriksa apakah menu HMSI Backends bocor. " * 400

    rendered = render_reasoning_tail(text, max_lines=MAX_LINES, max_chars=MAX_CHARS, redact=False)

    assert _fills_budget(rendered, MAX_CHARS), f"only {len(rendered)} of {MAX_CHARS} chars used"


def test_assistant_progress_fills_budget_and_keeps_newest():
    lines = tuple(
        AssistantLine(text=f"progress note {index}: " + ("detail " * 30), created_at=float(index))
        for index in range(200)
    )

    rendered = assistant_tail(lines, max_lines=MAX_LINES, max_chars=9000)

    assert _fills_budget(rendered, 9000), f"only {len(rendered)} of 9000 chars used"
    assert "progress note 199" in rendered, "newest progress line must survive"
    assert "progress note 0:" not in rendered, "oldest progress line is what gets dropped"


def test_small_budget_still_respected():
    text = "\n\n".join(f"Paragraph {index}: " + ("word " * 40) for index in range(20))

    rendered = render_reasoning_tail(text, max_lines=6, max_chars=400, redact=False)

    assert len(rendered) <= 400
    assert len(rendered.splitlines()) <= 6
