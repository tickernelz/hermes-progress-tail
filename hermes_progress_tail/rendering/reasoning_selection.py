"""Budget-aware selection and capping for the reasoning tail.

Split out of ``reasoning.py`` to respect the repository 600-line rail. These
helpers decide WHICH reasoning content survives the configured line/char
budget; ``reasoning.py`` owns normalization and block parsing.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..utils.text import truncate_tail_text

if TYPE_CHECKING:
    from .reasoning import ReasoningBlock


def _render_block_tail(blocks: list[ReasoningBlock], *, max_lines: int, max_chars: int = 0) -> str:
    """Select newest-first blocks until the line OR char budget is spent.

    Both budgets are honoured: line budget keeps the bubble scannable, char
    budget is what the user actually configured to maximize. Selection stops
    only when a budget is genuinely exhausted, never after a single block.
    """
    if not blocks:
        return ""
    selected: list[ReasoningBlock] = []
    used_lines = 0
    used_chars = 0
    for block in reversed(blocks):
        block_lines = _render_block_lines(block, max_lines=max_lines)
        if not block_lines:
            continue
        line_count = len(block_lines)
        char_count = len("\n".join(block_lines))
        if selected and used_lines + line_count > max_lines:
            break
        if selected and max_chars > 0 and used_chars + char_count > max_chars:
            break
        selected.append(block)
        used_lines += line_count
        used_chars += char_count + 2
        if used_lines >= max_lines:
            break
        if max_chars > 0 and used_chars >= max_chars:
            break
    selected.reverse()
    return "\n\n".join(
        _render_latest_block(block, max_lines=max_lines) for block in selected
    ).strip()


def _render_latest_block(block: ReasoningBlock, *, max_lines: int) -> str:
    return "\n".join(_render_block_lines(block, max_lines=max_lines)).strip()


def _render_block_lines(block: ReasoningBlock, *, max_lines: int) -> list[str]:
    parts = []
    if block.heading:
        heading = block.heading
        if block.heading_style == "bold":
            heading = f"**{heading}**"
        parts.append(heading)
    if block.body:
        body_lines = [line.strip() for line in block.body.splitlines() if line.strip()]
        body_budget = max(max_lines - len(parts), 1)
        # Keep the NEWEST body lines: streaming appends at the end, so slicing
        # from the front would hide the latest thought behind stale text.
        parts.extend(body_lines[-body_budget:])
    return parts[:max_lines]


def _render_paragraph_or_line_tail(text: str, *, max_lines: int, max_chars: int = 0) -> str:
    """Keep as many newest paragraphs as the line/char budget allows.

    Returning only the latest paragraph threw away everything the user asked to
    see: a 15k-char reasoning trail collapsed to a few hundred chars while the
    configured budget sat unused. The newest paragraph is always trimmed to the
    budget rather than emitted whole, so a single oversized paragraph still
    respects max_lines.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if paragraphs:
        selected: list[str] = []
        used_lines = 0
        used_chars = 0
        for paragraph in reversed(paragraphs):
            lines = [line.strip() for line in paragraph.splitlines() if line.strip()]
            if not lines:
                continue
            if not selected:
                # The newest paragraph must fit the budget on its own terms.
                lines = lines[-max_lines:] if max_lines > 0 else lines
            line_count = len(lines)
            char_count = len("\n".join(lines))
            if selected and used_lines + line_count > max_lines:
                break
            if selected and max_chars > 0 and used_chars + char_count > max_chars:
                break
            selected.append("\n".join(lines))
            used_lines += line_count
            used_chars += char_count + 2
            if used_lines >= max_lines:
                break
            if max_chars > 0 and used_chars >= max_chars:
                break
        if selected:
            selected.reverse()
            return "\n\n".join(selected)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def _cap_chars(text: str, max_chars: int, *, preserve_first_line: bool = False) -> str:
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    lines = text.splitlines()
    if len(lines) > 1 and preserve_first_line:
        # Keep the heading plus the NEWEST body tail. Streaming appends land at
        # the end, so a head-anchored cut would freeze the visible text at stale
        # content while live reasoning keeps moving (progress-tail must show the
        # tail — that is the product's name and contract).
        heading = lines[0].strip()
        budget = max_chars - len(heading) - 1
        if budget <= 3:
            return truncate_tail_text(text, max_chars)
        body = "\n".join(lines[1:]).strip()
        return heading + "\n" + truncate_tail_text(body, budget)
    return truncate_tail_text(text, max_chars)


def truncate_to_sentence_boundary(text: str, max_chars: int) -> str:
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return "." * max_chars
    cut = text[: max_chars - 3].rstrip()
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    min_boundary = min(80, max(24, (max_chars - 3) // 2))
    if boundary >= min_boundary:
        cut = cut[: boundary + 1].rstrip()
    else:
        word_boundary = cut.rfind(" ")
        if word_boundary >= min_boundary:
            cut = cut[:word_boundary].rstrip(" ,;:-")
    return cut.rstrip() + "..."
