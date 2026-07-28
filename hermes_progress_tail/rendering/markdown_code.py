from __future__ import annotations

import re
from collections.abc import Callable


def sub_outside_inline_code(
    text: str,
    pattern: re.Pattern[str],
    replace: Callable[[re.Match[str]], str],
    *,
    marker_length: int = 0,
    protected_group: str = "",
) -> tuple[str, int]:
    """Apply a regex replacement without rewriting Markdown code spans."""
    spans, marker_length = _inline_code_spans(text, marker_length=marker_length)
    output: list[str] = []
    cursor = 0
    for match in pattern.finditer(text):
        protected_start = match.start(protected_group) if protected_group else match.start()
        protected_end = match.end(protected_group) if protected_group else match.end()
        if any(start < protected_end and protected_start < end for start, end in spans):
            continue
        output.append(text[cursor : match.start()])
        output.append(replace(match))
        cursor = match.end()
    output.append(text[cursor:])
    return "".join(output), marker_length


def _inline_code_spans(text: str, *, marker_length: int = 0) -> tuple[list[tuple[int, int]], int]:
    """Return code spans and the unmatched marker carried to the next line."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    if marker_length:
        closing = _matching_backtick_run(text, cursor, marker_length)
        if closing is None:
            return [(0, len(text))], marker_length
        cursor = closing + marker_length
        spans.append((0, cursor))
        marker_length = 0
    while cursor < len(text):
        opening = text.find("`", cursor)
        if opening < 0:
            break
        opening_end = opening + 1
        while opening_end < len(text) and text[opening_end] == "`":
            opening_end += 1
        marker_length = opening_end - opening
        closing = _matching_backtick_run(text, opening_end, marker_length)
        if closing is None:
            spans.append((opening, len(text)))
            return spans, marker_length
        closing_end = closing + marker_length
        spans.append((opening, closing_end))
        cursor = closing_end
    return spans, 0


def _matching_backtick_run(text: str, cursor: int, marker_length: int) -> int | None:
    while cursor < len(text):
        candidate = text.find("`", cursor)
        if candidate < 0:
            return None
        run_end = candidate + 1
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        if run_end - candidate == marker_length:
            return candidate
        cursor = run_end
    return None
