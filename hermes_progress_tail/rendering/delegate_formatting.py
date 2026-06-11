from __future__ import annotations

import re
from typing import Any

from ..models.state import DelegateEvent
from ..utils.text import truncate_text


def middle_truncate_text(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if limit <= 0 or len(value) <= limit:
        return value
    if limit <= 12:
        return truncate_text(value, limit)
    separator = " … "
    remaining = max(1, limit - len(separator))
    head_len = max(1, int(remaining * 0.58))
    tail_len = max(1, remaining - head_len)
    head = value[:head_len].rstrip(" ,.;:-")
    tail = value[-tail_len:].lstrip(" ,.;:-")
    return f"{head}{separator}{tail}"


def event_preview_args(event: DelegateEvent) -> dict[str, Any]:
    preview = str(event.preview or "").strip()
    args = dict(event.args) if isinstance(event.args, dict) else {}
    if event.tool_name == "terminal" and preview:
        command = str(args.get("command") or "").strip()
        if not command or len(preview) > len(command):
            args["command"] = preview
    if args:
        if preview:
            if event.tool_name in {"read_file", "write_file"} and not (
                args.get("path") or args.get("file_path")
            ):
                args["path"] = preview
            elif event.tool_name == "search_files" and not (args.get("pattern") or args.get("q")):
                args["pattern"] = preview
        return args
    if not preview:
        return {}
    if event.tool_name == "terminal":
        return {"command": preview}
    if event.tool_name in {"read_file", "write_file"}:
        return {"path": preview}
    if event.tool_name == "search_files":
        return {"pattern": preview}
    if event.tool_name == "patch":
        if "*** " in preview:
            return {"mode": "patch", "patch": preview}
        return {"path": preview, "old_string": "", "new_string": ""}
    return {}


def status_symbol(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "done"}:
        return "✓"
    if normalized in {"failed", "cancelled"}:
        return "✗"
    if normalized in {"queued", "pending"}:
        return "…"
    return "→"


def duration(seconds: float) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return ""
    if value < 10:
        return f"{value:.1f}s"
    return f"{value:.0f}s"
