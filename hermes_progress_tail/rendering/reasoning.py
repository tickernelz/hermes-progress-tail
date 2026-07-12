from __future__ import annotations

import re
from dataclasses import dataclass

from ..utils.redaction import redact_text
from ..utils.text import truncate_tail_text

_REASONING_TAG_NAMES = r"think|thinking|reasoning|thought|analysis|REASONING_SCRATCHPAD"
_CODE_FENCE_LINE_RE = re.compile(r"^(?P<indent> {0,3})(?P<run>`{3,}|~{3,})(?P<tail>.*)$")
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
_BOLD_HEADING_RE = re.compile(r"^(?:\*\*|__)(?P<title>[^*_\n][^\n]*?)(?:\*\*|__)\s*$")
_INLINE_BOLD_HEADING_RE = re.compile(
    r"(?P<prefix>[.!?])\s*"
    r"(?P<heading>(?:\*\*|__)[A-Z][^*_\n]{1,80}(?:\*\*|__))"
    r"[ \t]*(?=\n|$|[A-Z])"
)
_BOLD_HEADING_TOKEN_RE = re.compile(r"(?:\*\*|__)[A-Z][^*_\n]{1,80}(?:\*\*|__)")
_ADJACENT_BOLD_HEADING_RUN_RE = re.compile(
    r"(?<![`*_])"
    r"(?P<run>(?:(?:\*\*|__)[A-Z][^*_\n]{1,80}(?:\*\*|__)){2,})"
    r"(?![`*_])"
)
_MISSING_SENTENCE_SPACE_RE = re.compile(r"(?<=[a-z])([.])(?=[A-Z])")
_GLUED_NUMBERED_LIST_RE = re.compile(r'(?<=[a-zA-Z):;\]}"\'])(?=\d{1,2}[.)]\s+[A-Z])')
_EMPTY_HTML_COMMENT_SEPARATOR_RE = re.compile(
    r"^(?P<indent>[ \t]*)<!--[ \t]*-->[ \t]*"
    r"(?=(?:\*\*|__|#{1,6}\s)|$)"
)
_TRAILING_EMPTY_HTML_COMMENT_RE = re.compile(r"(?m)^[ \t]*<!--[ \t\n]*(?:-->)?[ \t\n]*\Z")
_CHANNEL_ARTIFACT_RE = re.compile(
    r"<\|(?:channel\|>\s*analysis|start\|>|end\|>|message\|>|assistant\|>|analysis\|>)",
    re.IGNORECASE,
)
_STRAY_REASONING_TAG_RE = re.compile(rf"</?(?:{_REASONING_TAG_NAMES})\b[^>]*>", re.IGNORECASE)
_CLOSED_REASONING_TAG_RE = re.compile(
    rf"<(?P<tag>{_REASONING_TAG_NAMES})\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    re.DOTALL | re.IGNORECASE,
)
_UNTERMINATED_REASONING_TAG_RE = re.compile(
    rf"(?:^|\n)[ \t]*<(?P<tag>{_REASONING_TAG_NAMES})\b[^>]*>(?P<body>.*)$",
    re.DOTALL | re.IGNORECASE,
)
_PROVIDER_DELIMITER_RE = re.compile(
    r"^[\s\[({<|]*(?:analysis|reasoning|thinking|think)[\s\])}:>|-]*$", re.IGNORECASE
)
_JUNK_LINE_RE = re.compile(
    r"^(?:signature(?:_delta)?|encrypted(?:_reasoning)?|reasoning_signature)\s*[:=]",
    re.IGNORECASE,
)
_OPAQUE_BLOB_RE = re.compile(r"^[A-Za-z0-9+/=_-]{96,}$")


@dataclass(frozen=True)
class ReasoningBlock:
    heading: str
    body: str
    heading_style: str = ""


def normalize_reasoning_text(text: str, *, preserve_stream_suffix: bool = False) -> str:
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = _CHANNEL_ARTIFACT_RE.sub("", text)
    text = text.replace("◁think▷", "<think>").replace("◁/think▷", "</think>")
    text = text.replace("<|begin_of_thought|>", "<think>").replace("<|end_of_thought|>", "</think>")
    text = _extract_reasoning_tag_bodies(text)
    stream_suffix = ""
    # Preserve v0.2.02 API behavior: detach a terminal non-fenced separator,
    # canonicalize its whitespace, then reattach it after a blank line.
    suffix_match = _TRAILING_EMPTY_HTML_COMMENT_RE.search(text)
    if suffix_match and not _inside_code_fence(text, suffix_match.start()):
        if preserve_stream_suffix:
            stream_suffix = suffix_match.group(0).strip()
        text = text[: suffix_match.start()]
    text = _strip_empty_html_comment_separators(text)
    text = _normalize_streaming_glue(text)
    text = _normalize_adjacent_bold_heading_boundaries(text)
    text = _normalize_inline_heading_boundaries(text)
    lines = []
    for raw_line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if _PROVIDER_DELIMITER_RE.match(line):
            continue
        if _JUNK_LINE_RE.match(line):
            continue
        if _OPAQUE_BLOB_RE.match(line):
            continue
        line = _STRAY_REASONING_TAG_RE.sub("", line).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if stream_suffix:
        return f"{text}\n\n{stream_suffix}" if text else stream_suffix
    return text


def split_reasoning_stream_suffix(
    text: str, *, max_suffix_chars: int | None = None
) -> tuple[str, str]:
    """Detach an incomplete structural separator before bounded buffer trimming."""
    if not text:
        return "", ""
    line_start = text.rfind("\n") + 1
    last_line = text[line_start:]
    compact_line = "".join(last_line.split())
    empty_comment = "<!---->"
    pending_comment = bool(compact_line) and empty_comment.startswith(compact_line)
    if pending_comment:
        prefix = text[:line_start]
        core = prefix.rstrip(" \t\n")
        newline_count = prefix[len(core) :].count("\n")
        boundary = "\n" * min(2, newline_count)
        suffix = boundary + last_line
        if max_suffix_chars is not None and len(suffix) > max_suffix_chars:
            compact_line = compact_line[:max_suffix_chars]
            boundary_budget = max_suffix_chars - len(compact_line)
            boundary = boundary[-boundary_budget:] if boundary_budget > 0 else ""
            suffix = boundary + compact_line
        return core, suffix
    if not last_line.strip():
        core = text.rstrip(" \t\n")
        newline_count = text[len(core) :].count("\n")
        return core, "\n" * min(2, newline_count)
    return text, ""


def trim_reasoning_fenced_tail(text: str, max_chars: int) -> str | None:
    """Keep the latest fenced tail structurally valid across bounded trimming."""
    if not text or max_chars <= 0:
        return None
    lines = text.splitlines()
    fence_state: tuple[str, int] | None = None
    opening_index: int | None = None
    latest_complete: tuple[int, int] | None = None
    for index, line in enumerate(lines):
        previous_state = fence_state
        fence_state = _advance_code_fence(line, fence_state)
        if previous_state is None and fence_state is not None:
            opening_index = index
        elif previous_state is not None and fence_state is None and opening_index is not None:
            latest_complete = opening_index, index
            opening_index = None

    closing_index: int | None = None
    if fence_state is not None and opening_index is not None:
        start_index = opening_index
    elif latest_complete and not any(line.strip() for line in lines[latest_complete[1] + 1 :]):
        start_index, closing_index = latest_complete
    else:
        return None

    opening = lines[start_index]
    closing = lines[closing_index] if closing_index is not None else ""
    body_end = closing_index if closing_index is not None else len(lines)
    body = "\n".join(lines[start_index + 1 : body_end])
    fixed_chars = len(opening) + 1
    if closing:
        fixed_chars += len(closing) + 1
    if fixed_chars > max_chars:
        return None
    body_budget = max_chars - fixed_chars
    body_tail = body[-body_budget:].lstrip() if body and body_budget > 0 else ""
    result = opening
    if body_tail:
        result += "\n" + body_tail
    if closing:
        result += "\n" + closing
    return result


def _strip_empty_html_comment_separators(text: str) -> str:
    """Remove GPT-5.6 separator comments without touching code examples."""
    output: list[str] = []
    fence_state: tuple[str, int] | None = None
    for raw_line in text.splitlines(keepends=True):
        previous_state = fence_state
        fence_state = _advance_code_fence(raw_line, fence_state)
        if previous_state is None and fence_state is None:
            raw_line = _EMPTY_HTML_COMMENT_SEPARATOR_RE.sub(r"\g<indent>", raw_line)
        output.append(raw_line)
    return "".join(output)


def _inside_code_fence(text: str, position: int) -> bool:
    fence_state: tuple[str, int] | None = None
    for raw_line in text[:position].splitlines():
        fence_state = _advance_code_fence(raw_line, fence_state)
    return fence_state is not None


def _advance_code_fence(raw_line: str, state: tuple[str, int] | None) -> tuple[str, int] | None:
    """Track CommonMark backtick/tilde fences and their opening length."""
    line = raw_line.rstrip("\r\n")
    match = _CODE_FENCE_LINE_RE.match(line)
    if not match:
        return state
    run = match.group("run")
    marker = run[0]
    tail = match.group("tail")
    if state is None:
        if marker == "`" and "`" in tail:
            return None
        return marker, len(run)
    opening_marker, opening_length = state
    if marker == opening_marker and len(run) >= opening_length and not tail.strip():
        return None
    return state


def render_reasoning_tail(
    text: str,
    *,
    max_lines: int = 3,
    max_chars: int = 600,
    redact: bool = True,
) -> str:
    normalized = normalize_reasoning_text(text)
    if not normalized:
        return ""
    blocks = split_reasoning_blocks(normalized)
    if blocks:
        headed_blocks = [block for block in blocks if block.heading]
        if len(headed_blocks) >= 2:
            rendered = _render_block_tail(headed_blocks, max_lines=max_lines)
        else:
            rendered = _render_latest_block(blocks[-1], max_lines=max_lines)
        if max_chars > 0 and len(rendered) > max_chars and "\n\n" in rendered:
            rendered = _render_latest_block(blocks[-1], max_lines=max_lines)
        rendered = _cap_chars(rendered, max_chars, preserve_first_line=bool(blocks[-1].heading))
    else:
        rendered = _render_paragraph_or_line_tail(normalized, max_lines=max_lines)
        rendered = _cap_chars(rendered, max_chars)
    return redact_text(rendered) if redact else rendered


def split_reasoning_blocks(text: str) -> list[ReasoningBlock]:
    lines = text.splitlines()
    blocks: list[ReasoningBlock] = []
    heading = ""
    heading_style = ""
    body: list[str] = []
    saw_heading = False
    fence_state: tuple[str, int] | None = None

    def flush() -> None:
        nonlocal heading, heading_style, body
        block_body = "\n".join(body).strip()
        if heading.strip() or block_body:
            blocks.append(
                ReasoningBlock(
                    heading=heading.strip(), body=block_body, heading_style=heading_style
                )
            )
        heading = ""
        heading_style = ""
        body = []

    for raw_line in lines:
        line = raw_line.strip()
        previous_state = fence_state
        fence_state = _advance_code_fence(raw_line, fence_state)
        if previous_state is not None or fence_state is not None:
            body.append(line)
            continue
        detected = _detect_heading(line)
        if detected:
            flush()
            heading = detected[0]
            heading_style = detected[1]
            saw_heading = True
            continue
        body.append(line)
    flush()
    if saw_heading:
        return blocks
    return []


def _normalize_inline_heading_boundaries(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        heading = match.group("heading")
        if not _detect_heading(heading):
            return match.group(0)
        return f"{match.group('prefix')}\n\n{heading}\n"

    return _INLINE_BOLD_HEADING_RE.sub(replace, text)


def _normalize_adjacent_bold_heading_boundaries(text: str) -> str:
    """Split complete bold heading runs that arrived without separators."""

    def replace(match: re.Match[str]) -> str:
        run = match.group("run")
        headings = _BOLD_HEADING_TOKEN_RE.findall(run)
        if "".join(headings) != run or any(not _detect_heading(item) for item in headings):
            return run
        return "\n\n".join(headings)

    output: list[str] = []
    fence_state: tuple[str, int] | None = None
    for raw_line in text.splitlines(keepends=True):
        previous_state = fence_state
        fence_state = _advance_code_fence(raw_line, fence_state)
        if previous_state is None and fence_state is None:
            raw_line = _ADJACENT_BOLD_HEADING_RUN_RE.sub(replace, raw_line)
        output.append(raw_line)
    return "".join(output)


def _normalize_streaming_glue(text: str) -> str:
    """Restore structure lost when reasoning deltas arrive token-by-token.

    GPT-5.x reasoning summaries stream as discrete token deltas. Sentence
    boundaries and numbered list markers arrive glued to the previous/next
    token without any newline or space:

        ``spent.Let me:1. Profile``
        ``scenario2. Look at``
        ``)3. Identify``

    This mirrors ``_normalize_inline_heading_boundaries`` (which fixes glued
    **bold headings**) but targets plain-prose glue: missing sentence spaces
    and glued numbered list starts.  It runs before heading detection so the
    restored newlines create real block boundaries.
    """
    text = _MISSING_SENTENCE_SPACE_RE.sub(r"\1 ", text)
    text = _GLUED_NUMBERED_LIST_RE.sub("\n", text)
    return text


def _extract_reasoning_tag_bodies(text: str) -> str:
    bodies: list[str] = []

    def closed(match: re.Match[str]) -> str:
        body = match.group("body").strip()
        if body:
            bodies.append(body)
        return "\n"

    remainder = _CLOSED_REASONING_TAG_RE.sub(closed, text)
    match = _UNTERMINATED_REASONING_TAG_RE.search(remainder)
    if match:
        body = match.group("body").strip()
        prefix = remainder[: match.start()].strip()
        parts = []
        if prefix:
            parts.append(prefix)
        if bodies:
            parts.extend(bodies)
        if body:
            parts.append(body)
        return "\n\n".join(parts)
    if bodies:
        remainder = remainder.strip()
        parts = [remainder] if remainder else []
        parts.extend(bodies)
        return "\n\n".join(parts)
    return remainder


def _detect_heading(line: str) -> tuple[str, str] | None:
    if not line:
        return None
    bold = _BOLD_HEADING_RE.match(line)
    if bold:
        heading = _clean_heading(bold.group("title"))
        return (heading, "bold") if heading else None
    markdown = _MARKDOWN_HEADING_RE.match(line)
    if markdown:
        heading = _clean_heading(markdown.group(1))
        return (heading, "markdown") if heading else None
    if line.endswith(":"):
        heading = _clean_heading(line[:-1])
        return (heading, "colon") if heading else None
    return None


def _clean_heading(value: str) -> str:
    value = value.strip().strip("*_").strip()
    if not value or len(value) > 80:
        return ""
    lower = value.lower()
    if lower.startswith(("tool:", "result:", "cwd:", "first:", "terminal:", "read_file:")):
        return ""
    if re.search(r"[.!?]$", value):
        return ""
    if value.count("|") > 1 or value.count("{") or value.count("}"):
        return ""
    words = value.split()
    if len(words) > 9:
        return ""
    return value


def _render_block_tail(blocks: list[ReasoningBlock], *, max_lines: int) -> str:
    if not blocks:
        return ""
    selected: list[ReasoningBlock] = []
    used_lines = 0
    for block in reversed(blocks):
        block_lines = _render_block_lines(block, max_lines=max_lines)
        if not block_lines:
            continue
        line_count = len(block_lines)
        if selected and used_lines + line_count > max_lines:
            break
        selected.append(block)
        used_lines += line_count
        if used_lines >= max_lines:
            break
    selected.reverse()
    return "\n\n".join(
        _render_latest_block(block, max_lines=max_lines) for block in selected
    ).strip()


def _render_latest_block(block: ReasoningBlock, *, max_lines: int) -> str:
    return "\n".join(_render_block_lines(block, max_lines=max_lines)).strip()


def _render_block_lines(block: ReasoningBlock, *, max_lines: int) -> list[str]:
    parts = []
    if block.heading:
        heading = block.heading
        if block.heading_style == "bold":
            heading = f"**{heading}**"
        parts.append(heading)
    if block.body:
        body_lines = [line.strip() for line in block.body.splitlines() if line.strip()]
        body_budget = max(max_lines - len(parts), 1)
        parts.extend(body_lines[:body_budget])
    return parts[:max_lines]


def _render_paragraph_or_line_tail(text: str, *, max_lines: int) -> str:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if paragraphs:
        latest = paragraphs[-1]
        lines = [line.strip() for line in latest.splitlines() if line.strip()]
        if len(lines) <= max_lines:
            return "\n".join(lines)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def _cap_chars(text: str, max_chars: int, *, preserve_first_line: bool = False) -> str:
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    lines = text.splitlines()
    if len(lines) > 1 and preserve_first_line:
        heading = lines[0].strip()
        budget = max_chars - len(heading) - 1
        if budget <= 3:
            return truncate_tail_text(text, max_chars)
        body = "\n".join(lines[1:]).strip()
        return heading + "\n" + truncate_to_sentence_boundary(body, budget)
    return truncate_tail_text(text, max_chars)


def truncate_to_sentence_boundary(text: str, max_chars: int) -> str:
    text = text.strip()
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return "." * max_chars
    cut = text[: max_chars - 3].rstrip()
    boundary = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    min_boundary = min(80, max(24, (max_chars - 3) // 2))
    if boundary >= min_boundary:
        cut = cut[: boundary + 1].rstrip()
    else:
        word_boundary = cut.rfind(" ")
        if word_boundary >= min_boundary:
            cut = cut[:word_boundary].rstrip(" ,;:-")
    return cut.rstrip() + "..."
