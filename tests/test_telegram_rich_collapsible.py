"""Collapsible ``<details>`` rendering for long narrative sections.

Long Progress/Reasoning sections pushed Plan/Tools/Status below the fold, so a
reader had to scroll past thousands of characters of narrative to reach the
scannable sections. Collapsing the narrative keeps the full text one tap away
while the short sections stay visible.
"""

from __future__ import annotations

import re

from hermes_progress_tail.rendering.telegram_rich import (
    RichParagraph,
    format_progress_tail_telegram_rich_markdown,
)
from hermes_progress_tail.rendering.telegram_rich_details import (
    RichDetails,
    collapse_narrative,
)

LONG_PROGRESS = "\n".join(
    f"Task {index}. Reading the module that adds the field plus the existing view ext."
    for index in range(30)
)
LONG_REASONING = "\n\n".join(
    f"Paragraph {index}: tracing the generator view and the staging model fields."
    for index in range(30)
)


def _content(progress: str = LONG_PROGRESS, reasoning: str = LONG_REASONING) -> str:
    return (
        "Hermes is working\n\n"
        f"## Progress\n{progress}\n\n"
        f"## Reasoning\n{reasoning}\n\n"
        "## Plan\n- [x] Task 1\n- [ ] Task 2\n\n"
        "## Tools\n- terminal: pytest -q -> 20 passed\n\n"
        "## Status\nRunning task 3\n"
    )


def _visible(text: str) -> str:
    """The part a reader sees before expanding anything."""
    return re.sub(r"<details.*?</details>", "", text, flags=re.DOTALL)


def test_long_narrative_collapses_and_short_sections_stay_visible() -> None:
    out = format_progress_tail_telegram_rich_markdown(_content(), collapse_narrative_chars=1200)

    summaries = re.findall(r"<summary>(.*?)</summary>", out)
    assert any(summary.startswith("Progress") for summary in summaries)
    assert any(summary.startswith("Reasoning") for summary in summaries)

    visible = _visible(out)
    assert "## Plan" in visible
    assert "## Tools" in visible
    assert "## Status" in visible
    assert len(visible) < len(out) / 4


def test_collapsing_hides_nothing_from_the_payload() -> None:
    """Collapsing is a display affordance, never a truncation."""
    out = format_progress_tail_telegram_rich_markdown(_content(), collapse_narrative_chars=1200)

    assert "Task 0." in out
    assert "Task 21." in out
    assert "Paragraph 0:" in out
    assert "Paragraph 27:" in out


def test_short_sections_render_flat() -> None:
    """Below the threshold nothing changes, so short runs keep today's look."""
    out = format_progress_tail_telegram_rich_markdown(
        _content(progress="Task 1. Done.", reasoning="Short thought."),
        collapse_narrative_chars=1200,
    )

    assert "<details" not in out
    assert "Task 1. Done." in out
    assert "Short thought." in out


def test_disabled_threshold_never_collapses() -> None:
    out = format_progress_tail_telegram_rich_markdown(_content(), collapse_narrative_chars=0)

    assert "<details" not in out
    assert "Paragraph 27:" in out


def test_math_is_never_collapsed_tdesktop_crash_shape() -> None:
    """Math inside <details> crashes Telegram Desktop 6.9.1 (tdesktop#30808).

    Hermes core screens its own send path, but this plugin drives
    editMessageText directly and never consults that guard, so the crash shape
    must not be produced here at all.
    """
    math_reasoning = LONG_REASONING + "\n\nThe bound is $$\\sum_{i=0}^{n} x_i \\le 1$$ overall."
    out = format_progress_tail_telegram_rich_markdown(
        _content(reasoning=math_reasoning), collapse_narrative_chars=1200
    )

    details_blocks = re.findall(r"<details.*?</details>", out, flags=re.DOTALL)
    assert all("$$" not in block for block in details_blocks)
    assert "\\sum_{i=0}^{n} x_i" in out


def test_collapse_narrative_returns_none_below_threshold() -> None:
    assert collapse_narrative("Reasoning", [RichParagraph("tiny")], threshold=1200) is None


def test_rich_details_round_trips_summary_and_body() -> None:
    block = RichDetails(summary="Reasoning", body="body text")

    markdown = block.to_markdown()

    assert markdown.startswith("<details>")
    assert "<summary>Reasoning</summary>" in markdown
    assert "body text" in markdown
    assert markdown.endswith("</details>")


def test_rich_details_empty_body_renders_nothing() -> None:
    assert RichDetails(summary="Reasoning", body="   ").to_markdown() == ""


def test_open_details_emits_open_attribute() -> None:
    assert (
        RichDetails(summary="Tools", body="x", open=True).to_markdown().startswith("<details open>")
    )
