"""Pure text helpers shared by the Telegram rich renderer.

Leaf module: these functions depend on nothing else in the rendering package,
so they are safe to import from anywhere. Split out of ``telegram_rich`` to
keep every tracked file within the repository's 600-line rail.
"""

from __future__ import annotations

import re
from collections.abc import Sequence


def strip_control_markdown(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^\*\*__([^*\n]+)__\*\*$", r"\1", value)
    value = re.sub(r"^\*\*([^*\n]+)\*\*$", r"\1", value)
    value = re.sub(r"^\*([^*\n]+)\*$", r"\1", value)
    value = re.sub(r"^__([^_\n]+)__$", r"\1", value)
    return value.strip()


def _strip_list_marker(text: str) -> str:
    return re.sub(r"^[-•]\s+", "", str(text or "").strip())


def shorten_paths(text: str, *, max_chars: int = 64) -> str:
    def repl(match: re.Match[str]) -> str:
        path = match.group(0)
        if len(path) <= max_chars:
            return path
        suffix = ""
        suffix_match = re.search(r"(:\d+(?:\+\d+)?)$", path)
        if suffix_match:
            suffix = suffix_match.group(1)
            path_body = path[: -len(suffix)]
        else:
            path_body = path
        parts = [part for part in path_body.rstrip("/").split("/") if part]
        tail = parts[-1] if parts else path_body
        return f"…/{tail}{suffix}"

    return re.sub(r"/(?:[^\s`|·]+/)+[^\s`|·]+(?::\d+(?:\+\d+)?)?", repl, str(text or ""))


def shorten_command(command: str, *, max_chars: int = 72) -> str:
    command = shorten_paths(command, max_chars=max_chars)
    if len(command) <= max_chars:
        return command
    return command[: max_chars - 1].rstrip() + "…"


def hard_break_rich_lines(text: str) -> str:
    """Use paragraph breaks for lines Telegram rich Markdown may treat as soft wraps."""
    return "\n\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())


def table_cell(value: str) -> str:
    return str(value or "").replace("\n", " ").replace("|", "\\|").strip()


def clean_body_lines(lines: Sequence[str]) -> list[str]:
    cleaned = []
    for line in lines:
        text = strip_control_markdown(line)
        text = shorten_paths(text)
        if text:
            cleaned.append(text)
    return cleaned


def clean_body_lines_preserve_rich(lines: Sequence[str]) -> list[str]:
    cleaned = []
    for line in lines:
        text = str(line or "").strip()
        text = shorten_paths(text)
        if text:
            cleaned.append(text)
    return cleaned
