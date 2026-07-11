from __future__ import annotations

import time
from collections.abc import Callable

from ..models.state import AssistantLine, SessionContext, TodoItem, ToolEvent
from ..settings.config import Settings
from ..utils.redaction import redact_text
from ..utils.text import truncate_tail_text, truncate_text
from . import announcements
from .focused import compose_focused_content
from .footer import sectioned_footer


def compose_content(renderer, ctx: SessionContext) -> str:
    if renderer.settings.renderer.mode == "focused":
        return compose_focused_content(renderer, ctx)
    parts = []
    assistant = renderer._assistant_tail(ctx)
    if assistant:
        parts.append(renderer._section("Progress", "💬", assistant))
    reasoning = renderer._reasoning_tail(ctx)
    if reasoning:
        parts.append(renderer._section("Reasoning", "💭", reasoning))
    todo = renderer._todo_section(ctx)
    if todo:
        parts.append(todo)
    delegates = renderer.delegate_renderer.section(ctx)
    if delegates:
        parts.append(delegates)
    background = renderer._background_jobs_section(ctx)
    if background:
        parts.append(background)
    if ctx.tool.lines:
        parts.append(renderer._section("Tools", "🧰", "\n".join(ctx.tool.lines)))
    if renderer.settings.renderer.density == "debug":
        debug = renderer._debug_section(ctx)
        if debug:
            parts.append(debug)
    announcement = announcements.official_announcements_markdown()
    if announcement:
        parts.append(renderer._section("Announcements", "📣", announcement))
    footer = sectioned_footer(ctx, settings=renderer.settings)
    if footer:
        parts.append(footer)
    content = "\n\n".join(parts)
    return redact_text(content) if renderer.settings.renderer.redact_secrets else content


def section(title: str, emoji: str, body: str, *, style: str) -> str:
    label = f"{emoji} {title}" if style == "emoji" else title
    return f"▰ {label}\n{body}"


def assistant_tail(lines: tuple[AssistantLine, ...], *, max_lines: int, max_chars: int) -> str:
    text = "\n".join(line.text.strip() for line in lines if line.text.strip())
    if not text:
        return ""
    visible = [line for line in text.splitlines() if line.strip()]
    if max_lines > 0:
        visible = visible[-max_lines:]
    rendered = "\n".join(visible).strip()
    if max_chars > 0 and len(rendered) > max_chars:
        rendered = truncate_tail_text(rendered, max_chars)
    return rendered


def debug_section(ctx: SessionContext, *, section: Callable[[str, str, str], str]) -> str:
    lines = [f"strategy={ctx.strategy}", f"events={ctx.diagnostics.total_events}"]
    if ctx.delivery.edit_state != "editable":
        lines.append(f"edit_state={ctx.delivery.edit_state}")
    if ctx.diagnostics.downgrade_reason:
        lines.append(f"downgrade={ctx.diagnostics.downgrade_reason}")
    if ctx.diagnostics.last_error:
        lines.append(f"last_error={ctx.diagnostics.last_error}")
    return section("Debug", "🛠️", "\n".join(lines))


def format_tool_line_for_context(
    ctx: SessionContext,
    event: ToolEvent,
    *,
    timestamp_enabled: bool,
    timestamp_format: str,
) -> str:
    enabled = timestamp_enabled if ctx.timestamp is None else ctx.timestamp
    if not enabled:
        return event.line
    fmt = ctx.timestamp_format or timestamp_format
    timestamp = timestamp_text(event.created_at, fmt)
    return f"[{timestamp}] {event.line}"


def todo_section(ctx: SessionContext, *, settings: Settings) -> str:
    if not ctx.tool.todo_items:
        return ""
    timestamp_enabled = settings.tools.timestamp if ctx.timestamp is None else ctx.timestamp
    timestamp_format = ctx.timestamp_format or settings.tools.timestamp_format
    timestamp = timestamp_text(ctx.tool.todo_updated_at, timestamp_format)
    title = f"Todo [{timestamp}]" if timestamp_enabled else "Todo"
    if settings.renderer.density == "compact":
        return todo_compact(ctx.tool.todo_items, title, settings=settings)
    header = f"📋 {title}" if settings.renderer.style == "emoji" else title
    lines = todo_lines(ctx.tool.todo_items, settings=settings)
    return f"▰ {header}\n" + "\n".join(lines)


def todo_compact(items: tuple[TodoItem, ...], title: str, *, settings: Settings) -> str:
    counts = {"in_progress": 0, "pending": 0, "completed": 0, "cancelled": 0}
    current = ""
    for item in items:
        if item.status in counts:
            counts[item.status] += 1
        if item.status == "in_progress" and not current:
            current = item.content
    parts = []
    if current:
        parts.append("active: " + truncate_text(current, settings.todo.max_item_chars))
    for status, label in (
        ("pending", "pending"),
        ("completed", "done"),
        ("cancelled", "cancelled"),
    ):
        if counts[status]:
            parts.append(f"{counts[status]} {label}")
    body = " · ".join(parts) or "no tasks"
    prefix = "📋 " if settings.renderer.style == "emoji" else ""
    return f"▰ {prefix}{title}: {body}"


def timestamp_text(value: float, fmt: str) -> str:
    try:
        return time.strftime(fmt, time.localtime(value))
    except Exception:
        return time.strftime("%H:%M", time.localtime(value))


def todo_lines(items: tuple[TodoItem, ...], *, settings: Settings) -> list[str]:
    by_status = {"in_progress": [], "pending": [], "completed": [], "cancelled": []}
    for item in items:
        by_status.setdefault(item.status, []).append(item.content)
    emoji = settings.renderer.style == "emoji"
    labels = {
        "in_progress": "🔄 in progress" if emoji else "in progress",
        "pending": "⏳ pending" if emoji else "pending",
        "completed": "✅ done" if emoji else "done",
        "cancelled": "❌ cancelled" if emoji else "cancelled",
    }
    lines = []
    todo_settings = settings.todo
    for status, limit in (
        ("in_progress", 1),
        ("pending", todo_settings.max_pending),
        ("completed", todo_settings.max_completed),
        ("cancelled", todo_settings.max_cancelled),
    ):
        values = by_status[status]
        if not values:
            continue
        visible = ", ".join(
            truncate_text(value, todo_settings.max_item_chars) for value in values[:limit]
        )
        hidden = len(values) - limit
        suffix = f" +{hidden} more" if hidden > 0 else ""
        lines.append(f"{labels[status]} ({len(values)}): {visible}{suffix}")
    return lines or ["no tasks"]
