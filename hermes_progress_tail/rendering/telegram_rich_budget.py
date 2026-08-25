"""UTF-8 byte-budget guard for Telegram rich progress cards.

Telegram's client collapses a rich message behind a "Show more" button once the
payload passes roughly 8 KiB, hiding the lower sections (Plan / Tools / Status).
Content parked inside ``<details>`` still counts toward that limit, so only real
truncation shrinks a card.

The guard runs at the single seam every rich send/edit path funnels through and
keeps the byte-identity fast path: a card already inside budget is returned
completely unchanged.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from ..utils.text import truncate_tail_text
from .telegram_rich import format_progress_tail_telegram_rich_markdown, progress_section_title

DEFAULT_RICH_BUDGET_BYTES = 7500
DEFAULT_MIN_NARRATIVE_CHARS = 300

# Reasoning degrades first: Progress carries the "what am I doing right now"
# signal the reader most needs at a glance, so it shrinks last.
SHRINK_ORDER: tuple[str, ...] = ("reasoning", "progress")


@dataclass
class _Section:
    """One progress-tail section: its raw heading line plus its body lines."""

    title: str
    heading: str
    body: list[str] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(self.body)

    def render(self) -> list[str]:
        return [self.heading, *self.body] if self.heading else list(self.body)


def markdown_byte_length(markdown: str) -> int:
    return len(str(markdown or "").encode("utf-8"))


def rich_renderer_for_settings(settings: object) -> Callable[[str], str]:
    """Bind Telegram rich-render options so the guard can re-render cheaply."""

    def render(source: str) -> str:
        return format_progress_tail_telegram_rich_markdown(
            source,
            max_table_rows=getattr(settings, "max_table_rows", 8),
            verification_table=getattr(settings, "verification_table", True),
            thinking_blocks=getattr(settings, "thinking_blocks", True),
            compact_success=getattr(settings, "compact_success", True),
            max_detail_items=getattr(settings, "max_detail_items", 8),
            collapse_narrative_chars=getattr(settings, "collapse_narrative_chars", 1200),
        )

    return render


def guarded_rich_markdown(content: str, settings: object) -> str:
    """Render a Telegram rich card and enforce the UTF-8 byte budget."""

    return enforce_rich_budget(
        content,
        rich_renderer_for_settings(settings),
        budget_bytes=getattr(settings, "rich_budget_bytes", DEFAULT_RICH_BUDGET_BYTES),
        min_narrative_chars=getattr(settings, "min_narrative_chars", DEFAULT_MIN_NARRATIVE_CHARS),
    )


def enforce_rich_budget(
    content: str,
    render: Callable[[str], str],
    *,
    budget_bytes: int = DEFAULT_RICH_BUDGET_BYTES,
    min_narrative_chars: int = DEFAULT_MIN_NARRATIVE_CHARS,
) -> str:
    """Render ``content`` and guarantee the result fits ``budget_bytes``.

    ``render`` maps progress-tail source text to rich markdown. Narrative source
    sections are shrunk tail-anchored (newest text survives) and re-rendered
    until the payload fits; a non-positive budget disables the guard.
    """

    rendered = render(content)
    budget = _safe_int(budget_bytes, DEFAULT_RICH_BUDGET_BYTES)
    if budget <= 0 or markdown_byte_length(rendered) <= budget:
        return rendered

    floor = max(0, _safe_int(min_narrative_chars, DEFAULT_MIN_NARRATIVE_CHARS))
    sections = _split_sections(str(content or ""))
    fitted = _shrink_sections(sections, render, budget=budget, floor=floor)
    if fitted is not None:
        return fitted
    return _truncate_markdown_to_budget(render(_join_sections(sections)), budget)


def _shrink_sections(
    sections: list[_Section],
    render: Callable[[str], str],
    *,
    budget: int,
    floor: int,
) -> str | None:
    """Shrink narrative sections in order; return the first rendering that fits."""

    for name in SHRINK_ORDER:
        target = _find_section(sections, name)
        if target is None:
            continue
        fitted = _shrink_one(target, sections, render, budget=budget, floor=floor)
        if fitted is not None:
            return fitted
    return None


def _shrink_one(
    target: _Section,
    sections: Sequence[_Section],
    render: Callable[[str], str],
    *,
    budget: int,
    floor: int,
) -> str | None:
    """Binary-search the largest tail-anchored limit for ``target`` that fits."""

    text = target.text()
    if len(text) <= floor:
        return None
    low, high = floor, len(text)
    best: str | None = None
    best_body: list[str] | None = None
    while low <= high:
        mid = (low + high) // 2
        target.body = _truncated_body(text, mid)
        candidate = render(_join_sections(sections))
        if markdown_byte_length(candidate) <= budget:
            best, best_body = candidate, target.body
            low = mid + 1
        else:
            high = mid - 1
    if best is not None and best_body is not None:
        target.body = best_body
        return best
    # Nothing fits even at the floor: leave the section parked at the floor so
    # the next section in SHRINK_ORDER shrinks against an already-reduced card.
    target.body = _truncated_body(text, floor)
    return None


def _truncated_body(text: str, limit: int) -> list[str]:
    truncated = truncate_tail_text(text, max(0, int(limit)))
    return truncated.splitlines() if truncated else []


def _find_section(sections: Sequence[_Section], name: str) -> _Section | None:
    for section in sections:
        if section.title.strip().lower() == name:
            return section
    return None


def _split_sections(content: str) -> list[_Section]:
    """Split progress-tail source text into preamble plus titled sections.

    Section detection reuses :func:`progress_section_title` so the guard sees the
    same boundaries the renderer does, across ``## Progress``, ``**__Progress__**``
    and ``▰ 💭 Reasoning`` heading styles.
    """

    sections: list[_Section] = [_Section(title="", heading="")]
    for raw_line in str(content or "").splitlines():
        title = progress_section_title(raw_line)
        if title:
            sections.append(_Section(title=title, heading=raw_line))
        else:
            sections[-1].body.append(raw_line)
    return sections


def _join_sections(sections: Sequence[_Section]) -> str:
    lines: list[str] = []
    for section in sections:
        lines.extend(section.render())
    return "\n".join(lines)


def _truncate_markdown_to_budget(markdown: str, budget: int) -> str:
    """Last resort: cut the rendered markdown itself so the cap always holds."""

    text = str(markdown or "")
    if markdown_byte_length(text) <= budget:
        return text
    low, high = 0, len(text)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = truncate_tail_text(text, mid)
        if markdown_byte_length(candidate) <= budget:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    if markdown_byte_length(best) <= budget:
        return best
    return text.encode("utf-8")[:budget].decode("utf-8", errors="ignore")


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
