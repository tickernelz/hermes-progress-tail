from __future__ import annotations

import re
import time

from ..models.state import SessionContext, TodoItem
from ..settings.config import Settings
from ..utils.redaction import redact_text
from ..utils.text import truncate_text

_CODE_RE = re.compile(r"(```[\s\S]*?```|`[^`]*`)")


def compose_focused_content(renderer, ctx: SessionContext) -> str:
    settings = renderer.settings
    parts: list[str] = [focused_header(renderer, ctx)]

    assistant = clean_live_markdown(renderer._assistant_tail(ctx), platform=ctx.platform)
    reasoning = clean_live_markdown(renderer._reasoning_tail(ctx), platform=ctx.platform)

    if assistant:
        parts.append(focused_block("Progress", assistant))
    if reasoning:
        parts.append(focused_block("Reasoning", reasoning))

    plan = focused_plan(ctx.todo_items, settings=settings)
    if plan:
        parts.append(focused_block("Plan", plan))

    delegates = strip_legacy_section_header(renderer.delegate_renderer.section(ctx), "Delegates")
    if delegates:
        parts.append(focused_block("Delegates", delegates))

    background = strip_legacy_section_header(
        renderer._background_jobs_section(ctx), "Background Jobs"
    )
    if background:
        parts.append(focused_block("Background", background))

    tools = focused_tools(ctx, settings=settings)
    if tools:
        parts.append(focused_block("Tools", tools))

    if settings.renderer.density == "debug":
        debug = strip_legacy_section_header(renderer._debug_section(ctx), "Debug")
        if debug:
            parts.append(focused_block("Debug", debug))

    content = "\n\n".join(part for part in parts if part.strip())
    return redact_text(content) if settings.renderer.redact_secrets else content


def focused_header(renderer, ctx: SessionContext) -> str:
    agent_label = focused_agent_label(renderer, ctx)
    now = clean_live_markdown(focused_now(ctx), platform=ctx.platform) or "working"
    why = clean_live_markdown(renderer._assistant_tail(ctx), platform=ctx.platform)
    if not why:
        why = clean_live_markdown(renderer._reasoning_tail(ctx), platform=ctx.platform)
    why = one_line(why, 76)
    if not why:
        why = "collecting progress signals"
    state = focused_state(ctx)
    elapsed = focused_elapsed(ctx)
    return "\n".join(
        [
            f"{agent_label} is working",
            "────────────────",
            f"Now     {truncate_text(now, 76)}",
            f"Why     {why}",
            f"State   {state}",
            f"Time    {elapsed}",
        ]
    )


def focused_agent_label(renderer, ctx: SessionContext) -> str:
    settings = getattr(renderer, "settings", None)
    renderer_settings = getattr(settings, "renderer", None)
    configured = getattr(renderer_settings, "agent_label", "")
    label = str(getattr(ctx, "agent_label", "") or configured or "").strip()
    return sanitize_agent_label(label) or "Hermes"


def sanitize_agent_label(label: str) -> str:
    text = " ".join(str(label or "").split())
    if not text:
        return ""
    return truncate_text(text, 32)


def focused_block(title: str, body: str) -> str:
    body = str(body or "").strip()
    if not body:
        return ""
    return f"{title}\n{body}"


def focused_now(ctx: SessionContext) -> str:
    activity = latest_activity(ctx)
    return semantic_activity(activity) if activity else "working"


def semantic_activity(activity: str) -> str:
    text = normalize_tool_line(activity)
    lowered = text.lower()
    if lowered.startswith("patch"):
        path = extract_change_path(text)
        return "patching " + short_filename(path) if path else text
    if lowered.startswith("write_file") or lowered.startswith("write file"):
        path = extract_change_path(text)
        return "writing " + short_filename(path) if path else text
    if lowered.startswith("read_file") or lowered.startswith("read file"):
        path = extract_change_path(text)
        return "reading " + short_filename(path) if path else text
    if lowered.startswith("search_files") or lowered.startswith("search files"):
        match = re.search(r'"([^"]+)"', text)
        return f'searching "{match.group(1)}"' if match else text
    if lowered.startswith("execute_code"):
        script = strip_tool_label(text)
        script = strip_duration_suffix(script)
        return (
            "running Python script: " + truncate_text(script, 56)
            if script
            else "running Python script"
        )
    if lowered.startswith("delegate_task") or lowered.startswith("delegate task"):
        goal = strip_tool_label(text)
        return "waiting on subagent: " + truncate_text(goal, 56) if goal else "waiting on subagent"
    if lowered.startswith("terminal:"):
        command = text.split(":", 1)[1].strip()
        command_lower = command.lower()
        if command_lower.startswith(("pytest", "python -m pytest", "python3 -m pytest")):
            return "running tests"
        if command_lower.startswith("git push"):
            return "publishing git changes"
        if command_lower.startswith("gh release"):
            return "publishing GitHub release"
        if "python inline script" in command_lower:
            return "running python script"
        return "running " + truncate_text(command, 64)
    return text


def strip_tool_label(text: str) -> str:
    return str(text or "").split(":", 1)[1].strip() if ":" in str(text or "") else ""


def strip_duration_suffix(text: str) -> str:
    return re.sub(r"\s+·\s+\d+\s+lines?$", "", str(text or "")).strip()


def short_filename(path: str) -> str:
    cleaned = str(path or "").strip()
    if not cleaned:
        return ""
    return cleaned.rstrip("/").rsplit("/", 1)[-1]


def latest_activity(ctx: SessionContext) -> str:
    if ctx.tool_lines:
        return normalize_tool_line(ctx.tool_lines[-1])
    for branch_key in reversed(ctx.delegate_order):
        branch = ctx.delegate_branches.get(branch_key)
        if branch:
            return f"delegate · {branch.goal or branch.subagent_id}"
    if ctx.background_order:
        job = ctx.background_jobs.get(ctx.background_order[-1])
        if job:
            return f"background · {job.command or job.process_id}"
    if ctx.assistant_latest_text:
        return "assistant progress"
    if ctx.reasoning_text:
        return "reasoning"
    return "working"


def focused_state(ctx: SessionContext) -> str:
    total_tools = ctx.tool_started_count or len(ctx.tool_lines)
    completed = ctx.tool_completed_count
    failed = ctx.tool_failed_count
    if not ctx.tool_started_count:
        running = 1 if ctx.tool_lines else 0
        completed = max(0, len(ctx.tool_lines) - running)
    else:
        running = max(0, total_tools - completed - failed)
    queued = sum(1 for item in ctx.todo_items if item.status == "pending")
    parts = [f"{total_tools} tools", f"{completed} done"]
    if failed:
        parts.append(f"{failed} failed")
    parts.append(f"{running} running")
    if queued:
        parts.append(f"{queued} queued")
    return " · ".join(parts)


def focused_elapsed(ctx: SessionContext) -> str:
    elapsed = max(0, int(time.monotonic() - ctx.started_at))
    if elapsed <= 0:
        return "just now"
    if elapsed < 60:
        return f"{elapsed}s"
    minutes, seconds = divmod(elapsed, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def focused_plan(items: tuple[TodoItem, ...], *, settings: Settings) -> str:
    if not items:
        return ""
    lines: list[str] = []
    completed = [item for item in items if item.status == "completed"]
    in_progress = [item for item in items if item.status == "in_progress"]
    pending = [item for item in items if item.status == "pending"]
    cancelled = [item for item in items if item.status == "cancelled"]

    for item in completed[:2]:
        lines.append("✓ " + truncate_text(item.content, settings.todo.max_item_chars))
    for item in in_progress[:1]:
        lines.append("→ " + truncate_text(item.content, settings.todo.max_item_chars))
    if pending:
        lines.append(f"… {len(pending)} queued")
    for item in cancelled[:1]:
        lines.append("× " + truncate_text(item.content, settings.todo.max_item_chars))
    return "\n".join(lines)


def focused_tools(ctx: SessionContext, *, settings: Settings) -> str:
    if not ctx.tool_lines:
        return ""
    visible = list(ctx.tool_lines)[-settings.tools.lines :]
    rows = []
    for index, raw in enumerate(visible):
        marker = "→" if index == len(visible) - 1 else tool_done_marker(raw)
        rows.append((marker, normalize_tool_line(raw), raw))
    return "\n".join(collapse_tool_rows(rows))


def collapse_tool_rows(rows: list[tuple[str, str, str]]) -> list[str]:
    output: list[str] = []
    index = 0
    while index < len(rows):
        marker, line, raw = rows[index]
        if marker == "✓" and is_collapsible_read_file(line, raw):
            run: list[tuple[str, str, str]] = []
            while index < len(rows):
                candidate_marker, candidate_line, candidate_raw = rows[index]
                if candidate_marker != "✓" or not is_collapsible_read_file(
                    candidate_line, candidate_raw
                ):
                    break
                run.append((candidate_marker, candidate_line, candidate_raw))
                index += 1
            if len(run) >= 3:
                output.append(format_read_file_burst(run))
            else:
                output.extend(f"{item_marker} {item_line}" for item_marker, item_line, _ in run)
            continue
        output.append(f"{marker} {line}")
        index += 1
    return output


def is_collapsible_read_file(line: str, raw: str) -> bool:
    text = str(line or "")
    raw_text = str(raw or "").lower()
    return text.startswith("read_file:") and "failed" not in raw_text


def format_read_file_burst(rows: list[tuple[str, str, str]]) -> str:
    names = [read_file_display_name(line) for _, line, _ in rows]
    shown = names[:3]
    hidden = len(names) - len(shown)
    suffix = ", ".join(shown)
    if hidden:
        suffix += f", +{hidden}"
    return f"✓ read_file: {len(rows)} files · {suffix}"


def read_file_display_name(line: str) -> str:
    body = strip_tool_label(line)
    body = body.split(" · ", 1)[0]
    body = re.sub(r":\d+(?:\+\d+)?$", "", body)
    return short_filename(body)


def tool_done_marker(raw: str) -> str:
    text = str(raw or "")
    if "failed" in text.lower() or text.lstrip().startswith("❌"):
        return "×"
    return "✓"


def extract_change_path(line: str) -> str:
    cleaned = re.sub(r"^[^·:]+[·:]\s*", "", line).strip()
    if not cleaned:
        cleaned = line.strip()
    match = re.search(r"(?:[\w./~-]+/)?[\w.-]+\.py\b", cleaned)
    if match:
        return match.group(0)
    return cleaned.split()[0] if cleaned.split() else ""


def normalize_tool_line(raw: str) -> str:
    text = str(raw or "").strip()
    text = re.sub(r"^\[[^\]]+\]\s*", "", text)
    for prefix in (
        "✅ ",
        "❌ ",
        "🔎 ",
        "📖 ",
        "✍️ ",
        "🔧 ",
        "💻 ",
        "📋 ",
        "🧑‍💻 ",
        "🐍 ",
        "🧰 ",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.replace(" · running", "").replace(" · done", "").replace(" · failed", "")
    return text.strip()


def strip_legacy_section_header(text: str, title: str) -> str:
    body = str(text or "").strip()
    if not body:
        return ""
    lines = body.splitlines()
    first = lines[0].strip()
    plain_first = re.sub(r"^▰\s*", "", first)
    plain_first = re.sub(r"^[^\w\s]+\s*", "", plain_first).strip()
    if plain_first in {title, title.replace(" Jobs", "")}:
        return "\n".join(lines[1:]).strip()
    return body


def clean_live_markdown(text: str, *, platform: str = "") -> str:
    if not text:
        return ""
    if platform != "telegram":
        return str(text).strip()
    parts = _CODE_RE.split(str(text))
    cleaned: list[str] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("`"):
            cleaned.append(part)
        else:
            cleaned.append(clean_plain_markdown_segment(part))
    return "".join(cleaned).strip()


def clean_plain_markdown_segment(text: str) -> str:
    value = str(text)
    value = re.sub(r"^#{1,6}\s+(.+)$", r"\1", value, flags=re.MULTILINE)
    value = re.sub(r"\*\*([^*\n][\s\S]*?[^*\n])\*\*", r"\1", value)
    value = re.sub(r"__([^_\n][\s\S]*?[^_\n])__", r"\1", value)
    value = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", value)
    value = re.sub(r"~~([^~\n]+)~~", r"\1", value)
    return value


def one_line(text: str, limit: int) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return truncate_text(line, limit)
    return ""
