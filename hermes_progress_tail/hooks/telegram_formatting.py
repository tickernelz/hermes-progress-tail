from __future__ import annotations

import re
from typing import Any


def _telegram_edit_target_lost(error_text: str) -> bool:
    text = str(error_text or "").lower()
    return (
        "message to edit not found" in text
        or "message not found" in text
        or "message_id_invalid" in text
        or "unknown message" in text
        or ("message_id" in text and "not found" in text)
    )


def format_progress_tail_telegram_markdown(content: str, formatter: Any) -> str:
    text = str(content or "")
    placeholders: dict[str, str] = {}

    def stash(value: str) -> str:
        key = f"\x00HPT{len(placeholders)}\x00"
        placeholders[key] = value
        return key

    def title_repl(match):
        title = _escape_telegram_mdv2(match.group(1).strip())
        return stash(f"*__{title}__*")

    def bold_italic_repl(match):
        body = _escape_telegram_mdv2(match.group(1).strip())
        return stash(f"*_{body}_*")

    text = _replace_outside_code(text, r"\*\*__([^\n*_][^\n]*?)__\*\*", title_repl)
    text = _replace_outside_code(text, r"\*\*\*([^\n*][^\n]*?)\*\*\*", bold_italic_repl)
    formatted = formatter(text)
    for key, value in placeholders.items():
        formatted = formatted.replace(_escape_telegram_mdv2(key), value).replace(key, value)
    return formatted


def _replace_outside_code(text: str, pattern: str, repl: Any) -> str:
    parts = re.split(r"(```[\s\S]*?```|`[^`]*`)", str(text or ""))
    for index, part in enumerate(parts):
        if part.startswith("`"):
            continue
        parts[index] = re.sub(pattern, repl, part)
    return "".join(parts)


def _escape_telegram_mdv2(text: str) -> str:
    specials = r"\\_*[]()~`>#+-=|{}.!"
    return "".join("\\" + char if char in specials else char for char in str(text or ""))
