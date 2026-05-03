from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any

from .redaction import redact_text, sanitize
from .state import TodoItem

EMOJI = {
    "skill_view": "📚",
    "todo": "📋",
    "terminal": "💻",
    "search_files": "🔎",
    "read_file": "📖",
    "write_file": "✍️",
    "patch": "🔧",
    "delegate_task": "🧑‍💻",
    "execute_code": "🐍",
    "multi_tool_use.parallel": "🧰",
    "parallel": "🧰",
}

STATUS_LABELS = {
    "pending": "pending",
    "in_progress": "in_progress",
    "completed": "done",
    "cancelled": "cancelled",
}


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


def _short_path(path: Any, *, keep_parent: bool = True) -> str:
    raw = redact_text(str(path or "")).strip()
    if not raw:
        return "<unknown>"
    p = PurePosixPath(raw)
    parts = [part for part in p.parts if part not in {"/", ""}]
    if keep_parent and len(parts) >= 2:
        return "/".join(parts[-2:])
    return parts[-1] if parts else raw


def extract_todo_items(args: dict[str, Any] | None) -> tuple[TodoItem, ...]:
    safe_args = sanitize(args or {})
    todos = safe_args.get("todos")
    if not isinstance(todos, list):
        return ()
    items = []
    for item in todos:
        if not isinstance(item, dict):
            continue
        content = redact_text(str(item.get("content") or "")).strip()
        if not content:
            continue
        status = str(item.get("status") or "pending").strip() or "pending"
        if status not in STATUS_LABELS:
            status = "pending"
        items.append(TodoItem(content=content, status=status))
    return tuple(items)


def summarize_todo_items(items: tuple[TodoItem, ...], limit: int = 120) -> str:
    counts = {"pending": 0, "in_progress": 0, "completed": 0, "cancelled": 0}
    current = None
    for item in items:
        if item.status in counts:
            counts[item.status] += 1
        if item.status == "in_progress" and current is None:
            current = item.content
    chunks = []
    if current:
        chunks.append("▶ " + _truncate(redact_text(current), max(20, limit // 2)))
    for status in ("pending", "completed", "cancelled"):
        count = counts[status]
        if count:
            chunks.append(f"{count} {STATUS_LABELS[status]}")
    if not chunks and counts["in_progress"]:
        chunks.append(f"{counts['in_progress']} in_progress")
    return _truncate(" · ".join(chunks) or "no tasks", limit)


def _fmt_todo(args: dict[str, Any], preview: str | None, limit: int) -> str:
    items = extract_todo_items(args)
    if not items:
        return _truncate(redact_text(preview or "updated"), limit)
    return summarize_todo_items(items, limit)


def _fmt_terminal(args: dict[str, Any], limit: int) -> str:
    cmd = str(args.get("command") or "").strip()
    cmd = redact_text(cmd) if cmd else "<empty>"
    cwd = args.get("workdir")
    text = f"{cmd} · cwd {_short_path(cwd, keep_parent=False)}" if cwd else cmd
    return _truncate(text, limit)


def _fmt_search(args: dict[str, Any], limit: int) -> str:
    pattern = redact_text(str(args.get("pattern") or args.get("q") or "")).strip()
    path = str(args.get("path") or "").strip()
    text = f'"{_truncate(pattern, 50)}"'
    if path:
        text += f" in {redact_text(path)}"
    return _truncate(text, limit)


def _fmt_read(args: dict[str, Any], limit: int) -> str:
    path = _short_path(args.get("path"))
    offset = args.get("offset")
    read_limit = args.get("limit")
    if offset is not None and read_limit is not None:
        return _truncate(f"{path}:{offset}+{read_limit}", limit)
    if offset is not None:
        return _truncate(f"{path}:{offset}", limit)
    return _truncate(path, limit)


def _fmt_write_patch(args: dict[str, Any], limit: int) -> str:
    return _truncate(_short_path(args.get("path") or args.get("file_path")), limit)


def _fmt_delegate(args: dict[str, Any], limit: int) -> str:
    goal = args.get("goal")
    if goal:
        return _truncate(redact_text(str(goal).strip()), limit)
    tasks = args.get("tasks")
    if isinstance(tasks, list):
        return f"{len(tasks)} task(s)"
    return "subagent task"


def _fmt_parallel(args: dict[str, Any]) -> str:
    tool_uses = args.get("tool_uses")
    if isinstance(tool_uses, list):
        return f"{len(tool_uses)} tool calls"
    return "parallel tools"


def _fallback(args: dict[str, Any], preview: str | None, limit: int) -> str:
    if preview:
        return _truncate(redact_text(str(preview)), limit)
    safe = sanitize(args)
    try:
        raw = json.dumps(safe, ensure_ascii=False, default=str, separators=(",", ":"))
    except TypeError:
        raw = str(safe)
    return _truncate(raw, limit)


def format_tool_line(
    tool_name: str,
    args: dict[str, Any] | None,
    preview: str | None = None,
    preview_length: int = 120,
) -> str:
    args = sanitize(args or {})
    display_name = "parallel" if tool_name == "multi_tool_use.parallel" else tool_name
    limit = max(10, int(preview_length or 120))
    if tool_name == "todo":
        summary = _fmt_todo(args, preview, limit)
    elif tool_name == "terminal":
        summary = _fmt_terminal(args, limit)
    elif tool_name == "search_files":
        summary = _fmt_search(args, limit)
    elif tool_name == "read_file":
        summary = _fmt_read(args, limit)
    elif tool_name in {"write_file", "patch"}:
        summary = _fmt_write_patch(args, limit)
    elif tool_name == "delegate_task":
        summary = _fmt_delegate(args, limit)
    elif tool_name == "multi_tool_use.parallel":
        summary = _fmt_parallel(args)
    else:
        summary = _fallback(args, preview, limit)
    line = f"{EMOJI.get(tool_name, '⚙️')} {display_name}: {summary}"
    return _truncate(line, limit + 3)
