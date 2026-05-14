from __future__ import annotations

import re
import shlex
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from ..models.state import TodoItem
from ..utils.redaction import redact_text, sanitize

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

SKIP_FALLBACK_KEYS = {
    "api_key",
    "authorization",
    "bearer",
    "content",
    "cookie",
    "env",
    "file_content",
    "headers",
    "message",
    "new_string",
    "old_string",
    "password",
    "patch",
    "prompt",
    "secret",
    "text",
    "token",
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


def _truncate_middle(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    head = max(1, (limit - 3) // 2)
    tail = max(1, limit - 3 - head)
    return f"{text[:head]}...{text[-tail:]}"


def _project_relative_path(raw: str) -> str | None:
    if not raw.startswith("/"):
        return raw or None
    try:
        resolved = Path(raw).expanduser().resolve(strict=False)
    except Exception:
        resolved = Path(raw)
    cwd = Path.cwd().resolve(strict=False)
    candidates = [cwd]
    for marker in ("Projects", "projects"):
        parts = resolved.parts
        if marker in parts:
            idx = parts.index(marker)
            if idx + 2 <= len(parts):
                candidates.append(Path(*parts[: idx + 2]))
    for base in candidates:
        try:
            return resolved.relative_to(base).as_posix() or resolved.name
        except ValueError:
            continue
    home = Path.home().resolve(strict=False)
    try:
        return "~/" + resolved.relative_to(home).as_posix()
    except ValueError:
        return None


def _looks_like_preservable_path_component(value: str) -> bool:
    path = PurePosixPath(value)
    stem = path.stem if path.suffix else value
    if len(stem) < 80:
        return False
    if path.suffix:
        return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", value))
    if any(ch.isdigit() for ch in stem):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z_.-]*", stem))


def _redact_path_display(path: str) -> str:
    redacted_parts = []
    for part in path.split("/"):
        if part in {"", "~"}:
            redacted_parts.append(part)
            continue
        redacted = redact_text(part)
        if redacted.startswith("[redacted_blob]") and _looks_like_preservable_path_component(part):
            redacted = part
        redacted_parts.append(redacted)
    return "/".join(redacted_parts)


def _short_path(path: Any, *, keep_parent: bool = True) -> str:
    raw = str(path or "").strip()
    if not raw:
        return "<unknown>"
    if raw.startswith("[redacted_blob]") and "/" not in raw:
        return raw
    relative = _project_relative_path(raw)
    raw = _redact_path_display(relative) if relative else _redact_path_display(raw)
    p = PurePosixPath(raw)
    parts = [part for part in p.parts if part not in {"/", ""}]
    if not parts:
        return raw
    if raw.startswith("~/"):
        if len(raw) <= 80:
            return raw
        head = parts[:3]
        tail = parts[-4:] if keep_parent else parts[-1:]
        compact = "/".join([*head, "...", *tail])
        return _truncate_middle(compact, 80)
    if keep_parent:
        compact = "/".join(parts) if relative else "/".join(parts[-2:])
        if len(compact) > 80 and len(parts) > 5:
            compact = "/".join([*parts[:3], "...", *parts[-4:]])
        return _truncate_middle(compact, 80)
    return parts[-1]


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


def _shell_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _compact_command_path(token: str) -> str:
    prefix = ""
    body = token
    while body and body[0] in "<>0123456789":
        prefix += body[0]
        body = body[1:]
    if not body.startswith(("/", "~/")):
        return token
    return prefix + _short_path(body, keep_parent=True)


def _script_snippet(command: str, part_limit: int = 36, *, hide_literals: bool = False) -> str:
    body = _script_body(command)
    clean_line = _safe_script_preview_line if hide_literals else _clean_script_line
    meaningful = [clean_line(line) for line in body]
    meaningful = [line for line in meaningful if line]
    preferred = [line for line in meaningful if not _low_signal_script_line(line)]
    candidates = preferred or meaningful
    if not candidates:
        return ""
    first = _truncate(redact_text(candidates[0]), part_limit)
    last = _truncate(redact_text(candidates[-1]), part_limit)
    if first == last:
        return first
    return f"{first} … {last}"


def _script_body(command: str) -> list[str]:
    lines = [line.rstrip() for line in str(command or "").splitlines() if line.strip()]
    if len(lines) <= 1:
        return lines
    first = lines[0].strip()
    heredoc = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", first)
    if heredoc and lines[-1].strip() == heredoc.group(1):
        return lines[1:-1]
    return lines


def _clean_script_line(line: str) -> str:
    return " ".join(str(line or "").strip().split())


def _safe_script_preview_line(line: str) -> str:
    line = _clean_script_line(line)
    if not line:
        return ""
    return re.sub(
        r"(['\"])(?:\\.|(?!\1).)*\1", lambda match: match.group(1) + "…" + match.group(1), line
    )


def _low_signal_script_line(line: str) -> bool:
    stripped = line.strip()
    return (
        not stripped
        or stripped.startswith("#")
        or stripped.startswith("import ")
        or stripped.startswith("from ")
    )


def _fmt_terminal_command(command: str) -> str:
    raw_command = str(command or "")
    command = redact_text(raw_command).strip()
    if not command:
        return "<empty>"
    lines = [line for line in command.splitlines() if line.strip()]
    if len(lines) > 1:
        first = lines[0].strip()
        if re.match(r"^(python3?|python)\s+-\s*<<", first) or first.startswith("python3 - <<"):
            snippet = _script_snippet(raw_command)
            detail = f" · {snippet}" if snippet else ""
            return f"python inline script{detail} · {len(lines)} lines"
        snippet = _script_snippet(raw_command)
        detail = f" · {snippet}" if snippet else ""
        return f"shell script{detail} · {len(lines)} lines"
    words = _shell_words(command)
    joined = " ".join(words)
    if not words:
        return command
    if words[0] in {"python", "python3"} and "-" in words[:3] and "<<" in command:
        return "python inline script"
    if words[0] == "node" and len(words) >= 5 and "typescript/bin/tsc" in joined:
        return "tsc " + " ".join(word for word in words[2:] if word in {"-p", ".", "--noEmit"})
    if words[0] in {"cat", "rm", "cp", "mv", "mkdir", "touch", "stat", "chmod", "chown"}:
        return " ".join([words[0], *(_compact_command_path(word) for word in words[1:4])])
    if words[0] in {"npm", "pnpm", "yarn"}:
        if len(words) >= 3 and words[1] == "run":
            return " ".join(words[:3])
        return " ".join(words[: min(len(words), 4)])
    if words[0] in {"pytest", "ruff", "pre-commit"}:
        return " ".join(words[: min(len(words), 4)])
    if words[0] == "python" and len(words) >= 4 and words[1:3] == ["-m", "pytest"]:
        return " ".join(words[: min(len(words), 5)])
    if words[0] == "python" and len(words) >= 4 and words[1:3] == ["-m", "pre_commit"]:
        return "pre-commit " + " ".join(words[3:5])
    if words[0] == "git":
        return " ".join(words[: min(len(words), 4)])
    if re.search(r"[;&|<>]", command):
        return " ".join(_compact_command_path(word) for word in words[: min(len(words), 5)])
    return " ".join(_compact_command_path(word) for word in words)


def _fmt_terminal(args: dict[str, Any], limit: int) -> str:
    cmd = _fmt_terminal_command(str(args.get("command") or ""))
    cwd = args.get("workdir")
    text = f"{cmd} · cwd {_short_path(cwd, keep_parent=False)}" if cwd else cmd
    return _truncate(text, limit)


def _fmt_search(args: dict[str, Any], limit: int) -> str:
    pattern = redact_text(str(args.get("pattern") or args.get("q") or "")).strip()
    path = str(args.get("path") or "").strip()
    text = f'"{_truncate_middle(pattern, 50)}"'
    if path:
        text += f" in {_short_path(path, keep_parent=True)}"
    target = str(args.get("target") or "").strip()
    file_glob = str(args.get("file_glob") or "").strip()
    if target:
        text += f" · {redact_text(target)}"
    if file_glob:
        text += f" · {redact_text(file_glob)}"
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


def _preview_text(value: Any, limit: int) -> str:
    text = redact_text(str(value or "")).strip()
    text = " ".join(text.split())
    if not text:
        return "<empty>"
    return _truncate(text, limit)


def _fmt_write(args: dict[str, Any], limit: int) -> str:
    return _truncate(_short_path(args.get("path") or args.get("file_path")), limit)


def _patch_stats(raw_patch: str, max_files: int) -> str:
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in raw_patch.splitlines():
        if line.startswith("*** Update File: "):
            current = {
                "op": "update",
                "path": line.split(": ", 1)[1].strip(),
                "add": 0,
                "remove": 0,
            }
            files.append(current)
            continue
        if line.startswith("*** Add File: "):
            current = {"op": "add", "path": line.split(": ", 1)[1].strip(), "add": 0, "remove": 0}
            files.append(current)
            continue
        if line.startswith("*** Delete File: "):
            current = {
                "op": "delete",
                "path": line.split(": ", 1)[1].strip(),
                "add": 0,
                "remove": 0,
            }
            files.append(current)
            continue
        if current is None:
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+") and not line.startswith("***"):
            current["add"] += 1
        elif line.startswith("-") and not line.startswith("***"):
            current["remove"] += 1
    if not files:
        return "patch text"
    visible = files[:max_files]
    chunks = []
    for item in visible:
        path = _short_path(item["path"])
        add = int(item["add"])
        remove = int(item["remove"])
        if item["op"] == "add":
            suffix = f" +{add}" if add else " add"
        elif item["op"] == "delete":
            suffix = f" -{remove}" if remove else " delete"
        else:
            parts = []
            if add:
                parts.append(f"+{add}")
            if remove:
                parts.append(f"-{remove}")
            suffix = " " + "/".join(parts) if parts else " update"
        chunks.append(path + suffix)
    hidden = len(files) - len(visible)
    if hidden > 0:
        chunks.append(f"+{hidden} more")
    file_word = "file" if len(files) == 1 else "files"
    return f"{len(files)} {file_word} · " + " · ".join(chunks)


def _fmt_patch(
    args: dict[str, Any],
    limit: int,
    *,
    detail: str = "smart",
    preview_chars: int = 48,
    max_files: int = 3,
) -> str:
    path = _short_path(args.get("path") or args.get("file_path"))
    detail = detail if detail in {"off", "path", "smart", "stats"} else "smart"
    if detail == "off":
        return "patch"
    if detail == "path":
        return _truncate(path, limit)
    mode = str(args.get("mode") or ("patch" if args.get("patch") else "replace")).strip().lower()
    if mode == "patch" or args.get("patch"):
        summary = _patch_stats(str(args.get("patch") or ""), max_files=max(1, int(max_files or 3)))
        return _truncate(summary, limit)
    if detail == "stats":
        return _truncate(f"{path} replace", limit)
    old = _preview_text(args.get("old_string"), preview_chars)
    new_raw = args.get("new_string")
    if new_raw in {None, ""}:
        summary = f'{path} remove "{old}"'
    else:
        new = _preview_text(new_raw, preview_chars)
        action = "replace all" if bool(args.get("replace_all")) else "replace"
        summary = f'{path} {action} "{old}" → "{new}"'
    return _truncate(summary, limit)


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


def _fallback_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        item_word = "item" if len(value) == 1 else "items"
        return f"{len(value)} {item_word}"
    if isinstance(value, dict):
        key_word = "key" if len(value) == 1 else "keys"
        return f"{len(value)} {key_word}"
    return _preview_text(value, 40)


def _fallback(args: dict[str, Any], preview: str | None, limit: int) -> str:
    if preview:
        return _truncate(redact_text(str(preview)), limit)
    safe = sanitize(args)
    if not isinstance(safe, dict) or not safe:
        return "tool call"
    chunks = []
    for key, value in safe.items():
        key_text = str(key or "").strip()
        if not key_text or key_text.lower() in SKIP_FALLBACK_KEYS:
            continue
        chunks.append(f"{key_text}={_fallback_value(value)}")
        if len(chunks) >= 4:
            break
    if not chunks:
        return "tool call"
    return _truncate(" · ".join(chunks), limit)


Formatter = Callable[..., str]
SimpleFormatter = Callable[[dict[str, Any], int], str]

BROWSER_TOOLS = (
    "browser_back",
    "browser_click",
    "browser_console",
    "browser_get_images",
    "browser_navigate",
    "browser_press",
    "browser_scroll",
    "browser_snapshot",
    "browser_type",
    "browser_vision",
)

VIDEO_TOOLS = (
    "mcp_claude_video_vision_video_analyze",
    "mcp_claude_video_vision_video_configure",
    "mcp_claude_video_vision_video_detail",
    "mcp_claude_video_vision_video_info",
    "mcp_claude_video_vision_video_setup",
    "mcp_claude_video_vision_video_watch",
)


def _wrap_simple(formatter: SimpleFormatter) -> Formatter:
    def wrapped(args: dict[str, Any], preview: str | None, limit: int, **_: Any) -> str:
        return formatter(args, limit)

    return wrapped


def _fmt_patch_dispatch(
    args: dict[str, Any],
    preview: str | None,
    limit: int,
    *,
    patch_detail: str = "smart",
    patch_preview_chars: int = 48,
    patch_max_files: int = 3,
    **_: Any,
) -> str:
    return _fmt_patch(
        args,
        limit,
        detail=patch_detail,
        preview_chars=patch_preview_chars,
        max_files=patch_max_files,
    )


def _fmt_todo_dispatch(args: dict[str, Any], preview: str | None, limit: int, **_: Any) -> str:
    return _fmt_todo(args, preview, limit)


def _fmt_parallel_dispatch(args: dict[str, Any], preview: str | None, limit: int, **_: Any) -> str:
    return _fmt_parallel(args)


def _build_formatters() -> dict[str, Formatter]:
    simple_formatters: dict[str, SimpleFormatter] = {
        "clarify": _fmt_clarify,
        "cronjob": _fmt_cronjob,
        "delegate_task": _fmt_delegate,
        "execute_code": _fmt_execute_code,
        "hindsight_recall": _fmt_session_search,
        "hindsight_reflect": _fmt_session_search,
        "hindsight_retain": _fmt_hindsight_retain,
        "imagegen": _fmt_prompt_only,
        "lcm_expand": _fmt_lcm_expand,
        "lcm_expand_query": _fmt_session_search,
        "lcm_grep": _fmt_session_search,
        "lcm_load_session": _fmt_lcm_load_session,
        "memory": _fmt_memory,
        "process": _fmt_process,
        "read_file": _fmt_read,
        "search_files": _fmt_search,
        "session_search": _fmt_session_search,
        "send_message": _fmt_send_message,
        "skill_manage": _fmt_skill_manage,
        "skill_view": _fmt_skill_view,
        "skills_list": _fmt_skills_list,
        "terminal": _fmt_terminal,
        "text_to_speech": _fmt_output_path,
        "vision_analyze": _fmt_vision,
        "write_file": _fmt_write,
    }
    formatters = {name: _wrap_simple(formatter) for name, formatter in simple_formatters.items()}
    formatters["multi_tool_use.parallel"] = _fmt_parallel_dispatch
    formatters["patch"] = _fmt_patch_dispatch
    formatters["todo"] = _fmt_todo_dispatch
    formatters.update({tool_name: _wrap_simple(_fmt_browser) for tool_name in BROWSER_TOOLS})
    formatters.update({tool_name: _wrap_simple(_fmt_video) for tool_name in VIDEO_TOOLS})
    return formatters


FORMATTERS = _build_formatters()


def format_tool_line(
    tool_name: str,
    args: dict[str, Any] | None,
    preview: str | None = None,
    preview_length: int = 120,
    patch_detail: str = "smart",
    patch_preview_chars: int = 48,
    patch_max_files: int = 3,
) -> str:
    args = args or {}
    display_name = "parallel" if tool_name == "multi_tool_use.parallel" else tool_name
    limit = max(10, int(preview_length or 120))
    formatter = FORMATTERS.get(tool_name)
    if formatter:
        summary = formatter(
            args,
            preview,
            limit,
            patch_detail=patch_detail,
            patch_preview_chars=patch_preview_chars,
            patch_max_files=patch_max_files,
        )
    else:
        summary = _fallback(args, preview, limit)
    line = f"{EMOJI.get(tool_name, '⚙️')} {display_name}: {summary}"
    return _truncate(line, limit + 3)
