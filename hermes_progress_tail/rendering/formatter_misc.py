from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ..utils.redaction import redact_text
from .formatter import (
    _fmt_terminal_command,
    _preview_text,
    _script_snippet,
    _short_path,
    _truncate,
    _truncate_middle,
)


def _fmt_delegate(args: dict[str, Any], limit: int) -> str:
    goal = args.get("goal")
    if goal:
        return _truncate(redact_text(str(goal).strip()), limit)
    tasks = args.get("tasks")
    if isinstance(tasks, list):
        task_word = "task" if len(tasks) == 1 else "tasks"
        return f"{len(tasks)} {task_word}"
    return "subagent task"


def _fmt_execute_code(args: dict[str, Any], limit: int) -> str:
    code = str(args.get("code") or "")
    lines = [line for line in code.splitlines() if line.strip()]
    snippet = _script_snippet(code, part_limit=48, hide_literals=True)
    if snippet:
        summary = f"{snippet} · {len(lines)} lines"
    else:
        summary = f"Python script · {len(lines)} lines" if lines else "Python script"
    return _truncate(summary, limit)


def _fmt_process(args: dict[str, Any], limit: int) -> str:
    action = str(args.get("action") or "").strip() or "inspect"
    session_id = str(args.get("session_id") or "").strip()
    text = f"{action} {session_id}" if session_id else action
    return _truncate(text, limit)


def _fmt_cronjob(args: dict[str, Any], limit: int) -> str:
    action = str(args.get("action") or "").strip() or "manage"
    name = str(args.get("name") or args.get("job_id") or "").strip()
    schedule = str(args.get("schedule") or "").strip()
    parts = [action]
    if name:
        parts.append(redact_text(name))
    text = " ".join(parts)
    if schedule:
        text += f" · {redact_text(schedule)}"
    return _truncate(text, limit)


def _fmt_clarify(args: dict[str, Any], limit: int) -> str:
    question = _preview_text(args.get("question"), 64)
    choices = args.get("choices")
    if isinstance(choices, list) and choices:
        question += f" · {len(choices)} choices"
    return _truncate(question, limit)


def _fmt_skill_view(args: dict[str, Any], limit: int) -> str:
    name = str(args.get("name") or "").strip()
    file_path = str(args.get("file_path") or "").strip()
    text = name or "skill"
    if file_path:
        text += f" · {_short_path(file_path)}"
    return _truncate(redact_text(text), limit)


def _fmt_skills_list(args: dict[str, Any], limit: int) -> str:
    category = str(args.get("category") or "").strip()
    return _truncate(redact_text(category or "all skills"), limit)


def _fmt_skill_manage(args: dict[str, Any], limit: int) -> str:
    action = str(args.get("action") or "").strip() or "manage"
    name = str(args.get("name") or "").strip()
    text = f"{action} {name}" if name else action
    return _truncate(redact_text(text), limit)


def _fmt_memory(args: dict[str, Any], limit: int) -> str:
    action = str(args.get("action") or "").strip() or "manage"
    target = str(args.get("target") or "").strip()
    text = f"{action} {target}" if target else action
    return _truncate(redact_text(text), limit)


def _fmt_session_search(args: dict[str, Any], limit: int) -> str:
    query = _preview_text(args.get("query"), 72)
    return _truncate(f'"{query}"' if query != "<empty>" else "recent sessions", limit)


def _short_url(raw: Any) -> str:
    text = redact_text(str(raw or "").strip())
    if not text:
        return "<unknown>"
    parsed = urlsplit(text)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.rstrip("/")
        return parsed.netloc + (path or "")
    return _short_path(text)


def _fmt_send_message(args: dict[str, Any], limit: int) -> str:
    return _truncate(redact_text(str(args.get("target") or "default").strip()), limit)


def _fmt_output_path(args: dict[str, Any], limit: int) -> str:
    return _truncate(
        _short_path(args.get("output_path") or args.get("path"), keep_parent=False), limit
    )


def _fmt_vision(args: dict[str, Any], limit: int) -> str:
    return _truncate(
        _short_url(args.get("image_url") or args.get("url") or args.get("path")), limit
    )


def _fmt_prompt_only(args: dict[str, Any], limit: int) -> str:
    prompt = str(args.get("prompt") or "")
    return _truncate(f"prompt · {len(prompt)} chars" if prompt else "prompt", limit)


def _fmt_browser(args: dict[str, Any], limit: int) -> str:
    if args.get("url"):
        return _truncate(_short_url(args.get("url")), limit)
    if args.get("ref"):
        return _truncate(redact_text(str(args.get("ref")).strip()), limit)
    if bool(args.get("full")):
        return "full"
    return "browser"


def _fmt_video(args: dict[str, Any], limit: int) -> str:
    text = _short_url(args.get("path") or args.get("url"))
    start = str(args.get("start_time") or "").strip()
    end = str(args.get("end_time") or "").strip()
    if start or end:
        text += f" · {start or '?'}-{end or '?'}"
    return _truncate(text, limit)


def _fmt_hindsight_retain(args: dict[str, Any], limit: int) -> str:
    context = _preview_text(args.get("context"), 72)
    return _truncate(context if context != "<empty>" else "retain memory", limit)


def _fmt_lcm_expand(args: dict[str, Any], limit: int) -> str:
    for key in ("store_id", "node_id", "externalized_ref"):
        value = args.get(key)
        if value not in {None, ""}:
            return _truncate(f"{key}={redact_text(str(value))}", limit)
    return "expand context"


def _fmt_lcm_load_session(args: dict[str, Any], limit: int) -> str:
    session_id = str(args.get("session_id") or "").strip()
    return _truncate(redact_text(session_id or "session"), limit)


def _fmt_parallel(args: dict[str, Any]) -> str:
    tool_uses = args.get("tool_uses")
    if not isinstance(tool_uses, list):
        return "parallel tools"
    summaries = []
    for item in tool_uses[:4]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("recipient_name") or item.get("name") or "tool")
        name = name.rsplit(".", 1)[-1]
        params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
        summaries.append(_parallel_tool_summary(name, params))
    hidden = len(tool_uses) - len(summaries)
    if hidden > 0:
        summaries.append(f"+{hidden} more")
    return " · ".join(summaries) if summaries else f"{len(tool_uses)} tool calls"


def _parallel_tool_summary(name: str, params: dict[str, Any]) -> str:
    if name == "read_file":
        return f"read_file {_short_path(params.get('path'), keep_parent=False)}"
    if name == "search_files":
        pattern = _truncate_middle(
            redact_text(str(params.get("pattern") or params.get("q") or "")).strip(), 40
        )
        return f'search_files "{pattern}"' if pattern else "search_files"
    if name == "terminal":
        command = _fmt_terminal_command(str(params.get("command") or ""))
        return "terminal " + _truncate(command, 48)
    if name in {"write_file", "patch"}:
        path = params.get("path") or params.get("file_path")
        return f"{name} {_short_path(path, keep_parent=False)}" if path else name
    return name
