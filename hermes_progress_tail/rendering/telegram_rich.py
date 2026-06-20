from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class RichBlock(Protocol):
    def to_markdown(self) -> str: ...


@dataclass(frozen=True)
class RichDoc:
    blocks: Sequence[RichBlock]
    is_rtl: bool = False

    def to_markdown(self) -> str:
        return "\n\n".join(
            block.to_markdown().strip() for block in self.blocks if block.to_markdown().strip()
        ).strip()


@dataclass(frozen=True)
class RichHeading:
    text: str
    level: int = 2

    def to_markdown(self) -> str:
        title = strip_control_markdown(self.text)
        if not title:
            return ""
        level = min(6, max(1, int(self.level or 2)))
        return f"{'#' * level} {title}"


@dataclass(frozen=True)
class RichParagraph:
    text: str

    def to_markdown(self) -> str:
        return hard_break_rich_lines(normalize_rich_text(self.text))


@dataclass(frozen=True)
class RichPreformatted:
    text: str
    language: str = ""

    def to_markdown(self) -> str:
        language = re.sub(r"[^A-Za-z0-9_+-]", "", str(self.language or ""))
        return f"```{language}\n{str(self.text or '').rstrip()}\n```"


@dataclass(frozen=True)
class RichList:
    items: Sequence[str]

    def to_markdown(self) -> str:
        return "\n".join(rich_list_item_markdown(item) for item in self.items if str(item).strip())


@dataclass(frozen=True)
class RichTable:
    headers: Sequence[str]
    rows: Sequence[Sequence[str]]

    def to_markdown(self) -> str:
        headers = [table_cell(header) for header in self.headers]
        if not headers:
            return ""
        aligns = [":--" if index == 0 else ":--" for index, _ in enumerate(headers)]
        lines = [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join(aligns) + "|",
        ]
        for row in self.rows:
            cells = [table_cell(cell) for cell in row]
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            lines.append("| " + " | ".join(cells[: len(headers)]) + " |")
        return "\n".join(lines)


@dataclass(frozen=True)
class RichThinking:
    text: str

    def to_markdown(self) -> str:
        return hard_break_rich_lines(normalize_thinking_text(self.text))


@dataclass(frozen=True)
class ToolSignal:
    raw: str
    command: str
    result: str
    status: str


def telegram_rich_message_payload(
    doc: RichDoc | str, *, skip_entity_detection: bool = False
) -> dict:
    markdown = doc.to_markdown() if isinstance(doc, RichDoc) else str(doc or "")
    payload: dict[str, object] = {"markdown": markdown}
    if isinstance(doc, RichDoc) and doc.is_rtl:
        payload["is_rtl"] = True
    if skip_entity_detection:
        payload["skip_entity_detection"] = True
    return payload


def format_progress_tail_telegram_rich_markdown(
    content: str,
    *,
    max_table_rows: int = 8,
    verification_table: bool = True,
    thinking_blocks: bool = True,
    compact_success: bool = True,
    max_detail_items: int = 8,
) -> str:
    text = str(content or "")
    doc = rich_doc_from_progress_tail(
        text,
        max_table_rows=max_table_rows,
        verification_table=verification_table,
        thinking_blocks=thinking_blocks,
        compact_success=compact_success,
        max_detail_items=max_detail_items,
    )
    return doc.to_markdown()


def rich_doc_from_progress_tail(
    content: str,
    *,
    max_table_rows: int = 8,
    verification_table: bool = True,
    thinking_blocks: bool = True,
    compact_success: bool = True,
    max_detail_items: int = 8,
) -> RichDoc:
    lines = [line.rstrip() for line in str(content or "").splitlines()]
    blocks: list[RichBlock] = []
    header_lines: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_body: list[str] = []

    def flush_section() -> None:
        nonlocal current_title, current_body
        if current_title:
            sections.append((current_title, current_body))
        current_title = ""
        current_body = []

    for raw_line in lines:
        if _is_code_fence(raw_line):
            continue
        if _is_retired_rich_subheading(raw_line):
            continue
        if not raw_line.strip() or set(raw_line.strip()) <= {"─", "-"}:
            if current_title:
                current_body.append("")
            continue
        title = progress_section_title(raw_line)
        if title:
            flush_section()
            current_title = title
            continue
        if current_title:
            current_body.append(raw_line)
        else:
            header_lines.append(raw_line)
    flush_section()

    blocks.extend(header_blocks(header_lines))
    for title, body_lines in sections:
        section_blocks = section_to_blocks(
            title,
            body_lines,
            max_table_rows=max_table_rows,
            verification_table=verification_table,
            thinking_blocks=thinking_blocks,
            compact_success=compact_success,
            max_detail_items=max_detail_items,
        )
        blocks.extend(section_blocks)
    if not blocks:
        blocks.append(RichParagraph(shorten_paths(strip_control_markdown(content))))
    return RichDoc(blocks)


def header_blocks(lines: Sequence[str]) -> list[RichBlock]:
    blocks: list[RichBlock] = []
    body: list[str] = []
    for line in lines:
        stripped = strip_control_markdown(line)
        if not stripped:
            continue
        heading = focused_heading(line)
        if heading and not blocks:
            blocks.append(RichHeading(heading, level=2))
        else:
            body.append(stripped)
    status_rows = focused_status_rows(body)
    if status_rows:
        blocks.append(RichTable(headers=("Field", "Value"), rows=status_rows))
        leftovers = [line for line in body if focused_status_pair(line) is None]
        if leftovers:
            blocks.append(RichParagraph("\n".join(leftovers)))
    elif body:
        blocks.append(RichParagraph("\n".join(body)))
    return blocks


def focused_status_pair(line: str) -> tuple[str, str] | None:
    text = strip_control_markdown(line)
    match = re.match(r"^(?:\*\*)?(Now|Why|State|Time)(?:\*\*)?\s+(.+)$", text)
    if not match:
        return None
    return match.group(1), strip_control_markdown(match.group(2)).strip()


def focused_status_rows(lines: Sequence[str]) -> tuple[tuple[str, str], ...]:
    rows = []
    for line in lines:
        pair = focused_status_pair(line)
        if pair:
            rows.append(pair)
    return tuple(rows)


def section_to_blocks(
    title: str,
    body_lines: Sequence[str],
    *,
    max_table_rows: int,
    verification_table: bool,
    thinking_blocks: bool,
    compact_success: bool,
    max_detail_items: int,
) -> list[RichBlock]:
    body = clean_body_lines(body_lines)
    if not body:
        return []
    title = strip_control_markdown(title)
    if title.lower() == "reasoning" and thinking_blocks:
        rich_body = clean_body_lines_preserve_rich(body_lines)
        return [RichHeading(title, level=2), *reasoning_rich_blocks(rich_body)]
    if title.lower() == "plan":
        return [RichHeading(title, level=2), RichList(plan_detail_lines(body_lines))]
    if title.lower() == "tools":
        blocks: list[RichBlock] = [RichHeading(title, level=2)]
        signals = tool_signals(body)
        failed = [signal for signal in signals if signal.status == "failed"]
        rows = verification_rows(body, max_rows=max_table_rows) if verification_table else []
        if failed:
            blocks.extend(
                [
                    RichHeading("Failed tools", level=2),
                    RichTable(headers=("Command", "Result"), rows=tool_signal_rows(failed)),
                ]
            )
        if rows:
            blocks.extend(
                [
                    RichHeading("Verification evidence", level=2),
                    RichTable(headers=("Command", "Result"), rows=rows),
                ]
            )
        detail_lines = [
            shorten_paths(_strip_list_marker(strip_control_markdown(line)))
            for line in body
            if line.strip()
        ]
        if compact_success and signals and not failed:
            detail_lines = []
        detail_lines = clamp_detail_lines(detail_lines, max_detail_items)
        if detail_lines:
            blocks.append(RichList(detail_lines))
        return blocks
    return [RichHeading(title, level=2), RichParagraph("\n".join(body))]


_PROGRESS_SECTION_TITLES = {
    "progress",
    "reasoning",
    "plan",
    "delegates",
    "background",
    "background jobs",
    "tools",
    "debug",
    "announcements",
    "status",
    "failed tools",
    "verification evidence",
}


def progress_section_title(line: str) -> str:
    text = str(line or "").strip()
    patterns = (
        (r"^\*\*__([^*\n]+)__\*\*$", False),
        (r"^##\s+(.+?)\s*$", True),
        (r"^▰\s*(?:[\w\W]️?\s+)?(.+?)$", False),
    )
    for pattern, require_known_title in patterns:
        match = re.match(pattern, text)
        if match:
            title = strip_control_markdown(match.group(1))
            title = re.sub(r"^[^A-Za-z0-9]+\s*", "", title).strip()
            if require_known_title and title.lower() not in _PROGRESS_SECTION_TITLES:
                continue
            return title
    return ""


def _is_code_fence(line: str) -> bool:
    text = strip_control_markdown(str(line or "").strip())
    return text.startswith("```")


def _is_retired_rich_subheading(line: str) -> bool:
    text = strip_control_markdown(str(line or "").strip().lstrip("#").strip())
    return text.lower() in {"thinking", "recent tool details"}


def focused_heading(line: str) -> str:
    text = str(line or "").strip()
    match = re.match(r"^\*\*([^*\n]+?)\*\*$", text)
    return strip_control_markdown(match.group(1)) if match else ""


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


def plan_detail_lines(lines: Sequence[str]) -> list[str]:
    return [
        _strip_list_marker(shorten_paths(str(line or "").strip()))
        for line in lines
        if str(line or "").strip()
    ]


def rich_list_item_markdown(item: str) -> str:
    body = normalize_rich_text(item)
    if not body:
        return ""
    lines = body.splitlines()
    first, *rest = lines
    if not rest:
        return f"- {first}"
    return "\n".join([f"- {first}", *(f"  {line}" for line in rest)])


def reasoning_rich_blocks(lines: Sequence[str]) -> list[RichBlock]:
    text = separate_inline_rich_headings("\n".join(lines))
    blocks: list[RichBlock] = []
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        paragraph = "\n".join(line for line in paragraph_lines if line.strip()).strip()
        if paragraph:
            blocks.append(RichThinking(paragraph))
        paragraph_lines = []

    for raw_line in text.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            flush_paragraph()
            continue
        title = inline_rich_heading_title(line)
        if title:
            flush_paragraph()
            blocks.append(RichHeading(title, level=3))
            continue
        paragraph_lines.append(line)
    flush_paragraph()
    return blocks or [RichParagraph("\n".join(lines))]


def inline_rich_heading_title(line: str) -> str:
    text = str(line or "").strip()
    patterns = (
        r"^\*\*\*([^*\n][\s\S]*?)\*\*\*$",
        r"^\*\*([^*\n][\s\S]*?)\*\*$",
        r"^__([^_\n][\s\S]*?)__$",
    )
    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            title = strip_control_markdown(match.group(1)).strip("*_ ").strip()
            return title
    return ""


def verification_rows(lines: Sequence[str], *, max_rows: int) -> tuple[tuple[str, str], ...]:
    rows: list[tuple[str, str]] = []
    for signal in tool_signals(lines):
        rows.append((f"`{shorten_command(signal.command)}`", signal.result))
        if len(rows) >= max(1, int(max_table_rows_safe(max_rows))):
            break
    return tuple(rows)


def tool_signals(lines: Sequence[str]) -> tuple[ToolSignal, ...]:
    signals = []
    for line in lines:
        parsed = parse_terminal_line(line)
        if not parsed:
            continue
        command, result, status = parsed
        signals.append(
            ToolSignal(
                raw=shorten_paths(strip_control_markdown(line)),
                command=command,
                result=result,
                status=status,
            )
        )
    return tuple(signals)


def tool_signal_rows(signals: Sequence[ToolSignal]) -> tuple[tuple[str, str], ...]:
    return tuple((f"`{shorten_command(signal.command)}`", signal.result) for signal in signals)


def clamp_detail_lines(lines: Sequence[str], max_items: int) -> list[str]:
    try:
        limit = max(0, int(max_items))
    except (TypeError, ValueError):
        limit = 8
    if limit <= 0 or len(lines) <= limit:
        return list(lines)
    omitted = len(lines) - limit
    return [*list(lines)[:limit], f"{omitted} more tool events"]


def max_table_rows_safe(value: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 8


def _strip_list_marker(text: str) -> str:
    return re.sub(r"^[-•]\s+", "", str(text or "").strip())


def parse_terminal_line(line: str) -> tuple[str, str, str] | None:
    text = strip_control_markdown(line)
    text = re.sub(r"^\[[^\]]+\]\s*", "", text).strip()
    text = _strip_list_marker(text)
    marker = "→"
    status = "running"
    if text.startswith(("✅", "✓")):
        marker = "✅"
        status = "success"
        text = text[1:].strip()
    elif text.startswith(("❌", "×")):
        marker = "❌"
        status = "failed"
        text = text[1:].strip()
    elif text.startswith(("→", "⏳")):
        marker = "→"
        status = "running"
        text = text[1:].strip()
    if not text.lower().startswith("terminal:"):
        return None
    parts = [part.strip() for part in text.split(":", 1)[1].split(" · ")]
    command = parts[0] if parts else ""
    if not command:
        return None
    suffix_parts = [part for part in parts[1:] if part]
    if suffix_parts and suffix_parts[0].lower() not in {
        "done",
        "failed",
        "running",
        "completed",
        "success",
        "error",
        "cancelled",
        "killed",
    }:
        inferred = "failed" if marker == "❌" else ("running" if marker == "→" else "done")
        suffix_parts.insert(0, inferred)
    if suffix_parts:
        first_suffix = suffix_parts[0].lower()
        if first_suffix in {"failed", "error", "cancelled", "killed"}:
            status = "failed"
        elif first_suffix in {"done", "completed", "success"}:
            status = "success"
        elif first_suffix == "running":
            status = "running"
    suffix = " · ".join(suffix_parts)
    result = f"{marker} {suffix}" if suffix else marker
    return command, result.strip(), status


def strip_control_markdown(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^\*\*__([^*\n]+)__\*\*$", r"\1", value)
    value = re.sub(r"^\*\*([^*\n]+)\*\*$", r"\1", value)
    value = re.sub(r"^\*([^*\n]+)\*$", r"\1", value)
    value = re.sub(r"^__([^_\n]+)__$", r"\1", value)
    return value.strip()


def normalize_rich_text(text: str) -> str:
    separated = separate_inline_rich_headings(str(text or ""))
    lines = [shorten_paths(strip_control_markdown(line)) for line in separated.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()


def hard_break_rich_lines(text: str) -> str:
    """Use paragraph breaks for lines Telegram rich Markdown may treat as soft wraps."""
    return "\n\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())


def normalize_thinking_text(text: str) -> str:
    separated = separate_inline_rich_headings(str(text or ""))
    lines = [shorten_paths(line.strip()) for line in separated.splitlines()]
    return "\n".join(line for line in lines if line.strip()).strip()


def separate_inline_rich_headings(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        heading = match.group("heading").strip()
        body = match.group("body").lstrip()
        return f"{prefix}{heading}\n{body}" if body else f"{prefix}{heading}"

    return re.sub(
        r"(?m)^(?P<prefix>(?:[→✓×✅❌•-]\s+)?)"
        r"(?P<heading>(?:\*\*\*[^*\n][^\n]*?\*\*\*|\*\*[^*\n][^\n]*?\*\*|__[^_\n][^\n]*?__))"
        r"(?P<body>[ \t]*\S.*)$",
        replace,
        text,
    )


def table_cell(value: str) -> str:
    return str(value or "").replace("\n", " ").replace("|", "\\|").strip()


def shorten_command(command: str, *, max_chars: int = 72) -> str:
    command = shorten_paths(command, max_chars=max_chars)
    if len(command) <= max_chars:
        return command
    return command[: max_chars - 1].rstrip() + "…"


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
