from __future__ import annotations

import re
from dataclasses import dataclass

from ..utils.redaction import redact_text

_REASONING_TAG_NAMES = r"think|thinking|reasoning|thought|analysis|REASONING_SCRATCHPAD"
_CODE_FENCE_RE = re.compile(r"^`{3,}")
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*#*$")
_BOLD_HEADING_RE = re.compile(r"^(?:\*\*|__)(?P<title>[^*_\n][^\n]*?)(?:\*\*|__)\s*$")
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


def normalize_reasoning_text(text: str) -> str:
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = _CHANNEL_ARTIFACT_RE.sub("", text)
    text = text.replace("◁think▷", "<think>").replace("◁/think▷", "</think>")
    text = text.replace("<|begin_of_thought|>", "<think>").replace("<|end_of_thought|>", "</think>")
    text = _extract_reasoning_tag_bodies(text)
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
    return re.sub(r"\n{3,}", "\n\n", text)


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
    in_fence = False

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
        if _CODE_FENCE_RE.match(line):
            in_fence = not in_fence
            body.append(line)
            continue
        detected = None if in_fence else _detect_heading(line)
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


def _render_latest_block(block: ReasoningBlock, *, max_lines: int) -> str:
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
    return "\n".join(parts).strip()


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
            return text[-max_chars:].lstrip()
        body = "\n".join(lines[1:]).strip()
        return heading + "\n" + truncate_to_sentence_boundary(body, budget)
    return text[-max_chars:].lstrip()


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
