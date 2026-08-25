"""Collapsible ``<details>`` support for long narrative sections.

Split out of :mod:`hermes_progress_tail.rendering.telegram_rich` to keep every
tracked file within the repository's 600-line rail.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol


class _Block(Protocol):
    def to_markdown(self) -> str: ...


@dataclass(frozen=True)
class RichDetails:
    """A Bot API 10.1 collapsible ``<details>`` block.

    Long narrative sections (Reasoning, Progress) push Plan/Tools/Status below
    the fold. Wrapping them in a collapsible block keeps the full text
    available behind a tap while the scannable sections stay visible.
    """

    summary: str
    body: str
    open: bool = False
    sanitize: Callable[[str], str] | None = None

    def to_markdown(self) -> str:
        body = str(self.body or "").strip()
        if not body:
            return ""
        raw_summary = str(self.summary or "")
        summary = (self.sanitize(raw_summary) if self.sanitize else raw_summary) or "Details"
        attr = " open" if self.open else ""
        return f"<details{attr}>\n<summary>{summary}</summary>\n\n{body}\n\n</details>"


_MATH_IN_DETAILS_RE = re.compile(
    r"(\$\$.*?\$\$|"
    r"\\\[.*?\\\]|"
    r"\\\(.*?\\\)|"
    r"\\(?:sum|frac|alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|"
    r"int|prod|sqrt|lim|infty|begin\{(?:equation|align|matrix|cases)\}))",
    re.IGNORECASE | re.DOTALL,
)


def collapse_narrative(
    title: str,
    blocks: Sequence[_Block],
    *,
    threshold: int,
    sanitize: Callable[[str], str] | None = None,
) -> RichDetails | None:
    """Wrap a long narrative section in a collapsible block.

    Returns ``None`` when collapsing is disabled or the section is short enough
    to stay inline, so short sections keep their existing flat rendering.

    Math inside a ``<details>`` block crashes Telegram Desktop 6.9.1
    (telegramdesktop/tdesktop#30808). Hermes core screens its own send path,
    but this plugin drives ``editMessageText`` directly and never consults that
    guard, so refuse to collapse math-bearing narrative here. That keeps the
    crash shape out of every delivery path rather than one of them.
    """
    if threshold <= 0:
        return None
    rendered = "\n\n".join(
        markdown for block in blocks if (markdown := block.to_markdown().strip())
    ).strip()
    if not rendered or len(rendered) <= threshold:
        return None
    if _MATH_IN_DETAILS_RE.search(rendered):
        return None
    return RichDetails(summary=f"{title} · tap to expand", body=rendered, sanitize=sanitize)
