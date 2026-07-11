from __future__ import annotations

import re
from typing import Any

from ..models.state import DelegateBranch, DelegateLine, SessionContext
from ..utils.text import truncate_text
from . import delegate_helpers
from .delegate_formatting import middle_truncate_text


class DelegateSections:
    def __init__(self, renderer: Any):
        self._renderer = renderer

    @property
    def settings(self):
        return self._renderer.settings

    def _status_symbol(self, status):
        return self._renderer._status_symbol(status)

    def _duration(self, seconds):
        return self._renderer._duration(seconds)

    def prune_completed(self, ctx, *, now=None):
        return self._renderer.prune_completed(ctx, now=now)

    def section(self, ctx: SessionContext) -> str:
        if not ctx.delegate.branches:
            return ""
        self.prune_completed(ctx)
        if not ctx.delegate.branches:
            return ""
        settings = self.settings.delegates
        visible_keys = list(ctx.delegate.order)[-settings.max_delegates :]
        lines: list[str] = []
        visible_branches = [
            branch for key in visible_keys if (branch := ctx.delegate.branches.get(key)) is not None
        ]
        inferred_task_count = max(
            [len(visible_branches), *(branch.task_index + 1 for branch in visible_branches)],
            default=1,
        )
        for branch in visible_branches:
            title = self._delegate_title(branch, inferred_task_count=inferred_task_count)
            if self.settings.renderer.density == "compact":
                if self._delegate_result_only(branch):
                    current = self._simplify_completion_line(
                        self._completion_result_text(branch), branch=branch
                    )
                else:
                    current = branch.completion_line or (
                        self._delegate_compact_line(branch.lines[-1])
                        if branch.lines
                        else branch.status or "running"
                    )
                lines.append(f"{title}: {current}")
                continue
            lines.append(title)
            delegate_lines = self._delegate_display_lines(branch)
            has_result = bool(branch.completion_line)
            if self._delegate_result_only(branch):
                delegate_lines = []
            total = len(delegate_lines) + (1 if has_result else 0)
            for index, item in enumerate(delegate_lines):
                connector = self._delegate_connector(index, total)
                lines.append(f"{connector} {self._delegate_event_label(item)}")
                detail_connector = "   " if connector == "└" else "│  "
                for detail in item.details:
                    lines.append(f"{detail_connector}{detail}")
            if branch.completion_line:
                connector = self._delegate_connector(len(delegate_lines), total)
                result_text = self._completion_result_text(branch)
                if self._delegate_result_only(branch):
                    lines.append(f"{connector} result")
                    lines.extend(self._completion_result_lines(result_text))
                else:
                    lines.append(
                        f"{connector} result: {self._simplify_completion_line(result_text, branch=branch)}"
                    )
        hidden = len(ctx.delegate.order) - len(visible_keys)
        if hidden > 0:
            lines.append(f"+{hidden} older delegate{'s' if hidden != 1 else ''}")
        if not lines:
            return ""
        header = "🔀 Delegates" if self.settings.renderer.style == "emoji" else "Delegates"
        return header + "\n" + "\n".join(lines)

    def _completion_result_text(self, branch: DelegateBranch) -> str:
        if self._delegate_result_only(branch):
            return branch.completion_summary or branch.completion_line
        return branch.completion_line

    def _completion_result_lines(self, text: str) -> list[str]:
        prepared = self._prepare_completion_block_text(text)
        raw_lines = [line for line in prepared.splitlines() if line.strip()]
        max_lines = self._completed_result_line_limit()
        raw_lines = self._middle_truncate_lines(raw_lines, max_lines)
        line_limit = self._completed_result_line_char_limit()
        return [f"  {middle_truncate_text(line.strip(), line_limit)}" for line in raw_lines]

    def _prepare_completion_block_text(self, text: str) -> str:
        value = delegate_helpers.simplify_known_plugin_paths(str(text or ""))
        value = re.sub(
            r"(?:~|/home/[^/]+)/.hermes/plugins/hermes-progress-tail\b",
            "hermes-progress-tail",
            value,
        )
        value = re.sub(r"```[\w-]*\n?", "", value).replace("```", "")
        value = re.sub(r"^(?:✓\s*)?(?:done|failed):\s*", "", value, flags=re.I)
        value = re.sub(r"\b([0-9a-f]{10})([0-9a-f]{8,})\b", r"\1…", value, flags=re.I)
        value = re.sub(
            r"(?<!\S)(#{1,6})\s+(.+)", lambda m: m.group(2).strip().rstrip(":") + ":", value
        )
        value = re.sub(r"(?m)^\s*[-*+]\s+", "- ", value)
        value = re.sub(r"(?m)^\s*(\d+)\.\s+", r"\1. ", value)
        value = self._split_inline_markdown_sections(value)
        value = self._simplify_long_paths(value)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    @staticmethod
    def _split_inline_markdown_sections(text: str) -> str:
        value = re.sub(r"\s+(#{1,6}\s+)", r"\n\1", text)
        value = re.sub(r"\s+(-\s+)", r"\n\1", value)
        value = re.sub(r"\s+(\d+\.\s+)", r"\n\1", value)
        return value

    @staticmethod
    def _simplify_long_paths(text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            raw = match.group(0).rstrip(".,;:)")
            suffix = match.group(0)[len(raw) :]
            parts = [part for part in raw.replace("\\", "/").split("/") if part]
            if len(parts) <= 3:
                return raw + suffix
            return "/".join(parts[-3:]) + suffix

        return re.sub(r"(?:~|/[A-Za-z0-9_.-]+|/[hH]ome/[^\s`'\")]+)[^\s`'\")]*", repl, text)

    def _completed_result_line_limit(self) -> int:
        density = self.settings.renderer.density
        if density == "compact":
            return 3
        if density == "verbose":
            return 8
        if density == "debug":
            return 12
        return 6

    def _completed_result_line_char_limit(self) -> int:
        density = self.settings.renderer.density
        if density == "compact":
            return max(72, self.settings.delegates.max_line_chars)
        if density == "verbose":
            return max(140, self.settings.delegates.max_line_chars)
        if density == "debug":
            return max(220, self.settings.delegates.max_line_chars * 2)
        return max(120, self.settings.delegates.max_line_chars)

    @staticmethod
    def _middle_truncate_lines(lines: list[str], limit: int) -> list[str]:
        if limit <= 0 or len(lines) <= limit:
            return lines
        if limit <= 2:
            return lines[:limit]
        head_count = max(1, limit - 3)
        tail_count = max(1, limit - head_count - 1)
        return [*lines[:head_count], "…", *lines[-tail_count:]]

    def _simplify_completion_line(self, text: str, *, branch: DelegateBranch | None = None) -> str:
        value = delegate_helpers.simplify_known_plugin_paths(text)
        value = re.sub(
            r"(?:~|/home/[^/]+)/.hermes/plugins/hermes-progress-tail\b",
            "hermes-progress-tail",
            value,
        )
        if branch is not None and self._delegate_result_only(branch):
            value = middle_truncate_text(value, self._completed_result_limit())
        return value

    def _delegate_result_only(self, branch: DelegateBranch) -> bool:
        if str(self.settings.renderer.mode or "").strip().lower() != "focused":
            return False
        status = str(branch.status or "").strip().lower()
        return bool(branch.completion_line) and status in {"completed", "done", "success"}

    def _completed_result_limit(self) -> int:
        density = self.settings.renderer.density
        if density == "compact":
            return max(120, self.settings.delegates.max_line_chars)
        if density == "verbose":
            return max(900, self.settings.delegates.max_line_chars * 5)
        if density == "debug":
            return max(1400, self.settings.delegates.max_line_chars * 8)
        return max(600, self.settings.delegates.max_line_chars * 4)

    def _delegate_display_lines(self, branch: DelegateBranch) -> list[DelegateLine]:
        lines = list(branch.lines)
        if not branch.completion_line:
            return lines
        tool_lines = [line for line in lines if line.kind == "tool"]
        if len(tool_lines) < 4 or len(tool_lines) != len(lines):
            return lines
        names = [self._delegate_tool_name(line) for line in tool_lines]
        summary = ", ".join(names[:4])
        hidden = len(names) - 4
        if hidden:
            summary += f", +{hidden}"
        return [DelegateLine("summary", f"{len(tool_lines)} tools · {summary}")]

    def _delegate_title(
        self, branch: DelegateBranch, *, inferred_task_count: int | None = None
    ) -> str:
        settings = self.settings.delegates
        label = truncate_text(
            branch.goal or f"task {branch.task_index + 1}", settings.max_goal_chars
        )
        status = branch.status or "running"
        if self.settings.renderer.style == "emoji":
            status = f"{self._status_symbol(status)} {status}"
        display_total = max(
            branch.task_count or 1,
            branch.task_index + 1,
            inferred_task_count or 1,
        )
        parts = [f"[{branch.task_index + 1}/{display_total}] {status}"]
        if label:
            parts.append(label)
        if settings.show_tool_count and branch.tool_count:
            parts.append(f"{branch.tool_count} tools")
        if settings.show_model and branch.model:
            parts.append(branch.model)
        if settings.show_completion and branch.duration_seconds:
            parts.append(self._duration(branch.duration_seconds))
        return " · ".join(parts)

    def _delegate_connector(self, index: int, total: int) -> str:
        if total <= 1:
            return "└"
        return "└" if index == total - 1 else "├"

    def _delegate_compact_line(self, item: DelegateLine) -> str:
        if item.kind == "debug":
            return item.text
        return item.text

    def _delegate_event_label(self, item: DelegateLine) -> str:
        if item.kind == "tool":
            return self._simplify_delegate_tool_text(item.text)
        if item.kind == "debug":
            return f"debug: {item.text}"
        if item.kind == "summary":
            return item.text
        if item.kind == "thinking":
            return f"thinking: {item.text}"
        if item.kind == "reply":
            return f"reply: {item.text}"
        return f"update: {item.text}"

    @staticmethod
    def _delegate_tool_name(item: DelegateLine) -> str:
        if item.tool_name:
            return item.tool_name
        text = delegate_helpers.strip_tool_emoji(item.text)
        return text.split(":", 1)[0].strip() or "tool"

    def _simplify_delegate_tool_text(self, text: str) -> str:
        cleaned = delegate_helpers.strip_tool_emoji(text)
        cleaned = delegate_helpers.simplify_known_plugin_paths(cleaned)
        cleaned = re.sub(r"·\s+cwd\s+~/.hermes/plugins/hermes-progress-tail\b", "· cwd .", cleaned)
        cleaned = re.sub(
            r"·\s+cwd\s+/home/[^/]+/.hermes/plugins/hermes-progress-tail\b",
            "· cwd .",
            cleaned,
        )
        return cleaned
