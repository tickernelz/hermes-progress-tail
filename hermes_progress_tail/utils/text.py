from __future__ import annotations


def truncate_text(text: str, limit: int) -> str:
    value = str(text or "")
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit <= 3:
        return "." * limit
    cut = value[: limit - 3].rstrip()
    word_boundary = cut.rfind(" ")
    if word_boundary > 0:
        cut = cut[:word_boundary].rstrip(" ,;:-")
    return cut + "..."


def truncate_tail_text(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    if limit <= 4:
        return "." * limit
    budget = limit - 4
    start = max(0, len(value) - budget)
    tail = value[start:].lstrip()
    if start > 0 and not value[start - 1].isspace():
        first_space = next((idx for idx, char in enumerate(tail) if char.isspace()), -1)
        if first_space >= 0:
            tail = tail[first_space + 1 :].lstrip()
        else:
            return value[-limit:].lstrip()
    return "... " + (tail or value[-budget:].lstrip())
