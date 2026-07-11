from __future__ import annotations

import contextlib
import re
import time
from typing import Any

from ..models.state import DelegateBranch, DelegateEvent, DelegateLine, SessionContext
from ..settings.config import Settings
from ..utils.text import truncate_text
from . import delegate_helpers
from .delegate_formatting import duration, event_preview_args, status_symbol
from .delegate_sections import DelegateSections
from .formatter import format_tool_line


class DelegateProgressRenderer:
    @staticmethod
    def _status_symbol(status):
        return status_symbol(status)

    @staticmethod
    def _duration(seconds):
        return duration(seconds)

    @staticmethod
    def _delegate_cwd(value):
        return delegate_helpers.delegate_cwd(value)

    @staticmethod
    def _terminal_first_line(command):
        return delegate_helpers.terminal_first_line(command)

    @staticmethod
    def _strip_tool_emoji(text):
        return delegate_helpers.strip_tool_emoji(text)

    @staticmethod
    def _simplify_known_plugin_paths(text):
        return delegate_helpers.simplify_known_plugin_paths(text)

    def __init__(self, settings: Settings):
        self.settings = settings
        self._sections = DelegateSections(self)

    def apply_event(self, ctx: SessionContext, event: DelegateEvent) -> None:
        event_type = event.event_type
        if event_type == "cleanup":
            self.prune_completed(ctx, now=event.created_at)
            return
        key = event.subagent_id or f"task-{event.task_index}"
        branch = ctx.delegate_branches.get(key)
        if branch is None:
            branch = DelegateBranch(
                subagent_id=key,
                task_index=event.task_index,
                task_count=event.task_count,
                goal=event.goal,
                model=event.model,
                started_at=event.created_at,
            )
            branch.resize(self.settings.delegates.lines_per_delegate)
            ctx.delegate_branches[key] = branch
            ctx.delegate_order.append(key)
        else:
            if event_type in {"subagent.spawn_requested", "subagent.start"} and branch.completed_at:
                self.cancel_cleanup(branch)
                branch.completion_line = ""
                branch.completion_summary = ""
                branch.lines.clear()
                branch.completed_at = 0.0
                branch.duration_seconds = 0.0
                branch.tool_count = 0
                branch.lifecycle_started = False
                branch.thinking_text = ""
                branch.response_text = ""
            branch.task_index = event.task_index
            branch.task_count = event.task_count or branch.task_count
            branch.goal = event.goal or branch.goal
            branch.model = event.model or branch.model
            branch.resize(self.settings.delegates.lines_per_delegate)
        branch.updated_at = event.created_at
        if event.tool_count:
            branch.tool_count = event.tool_count
        if event_type in {"subagent.spawn_requested", "subagent.start"}:
            branch.status = "running" if event_type == "subagent.start" else "queued"
            if not branch.lifecycle_started:
                branch.started_at = event.created_at
                branch.lifecycle_started = True
            return
        if event_type in {"subagent.complete", "subagent.failed"}:
            branch.status = event.status or (
                "failed" if event_type == "subagent.failed" else "completed"
            )
            branch.completed_at = event.created_at
            branch.duration_seconds = event.duration_seconds
            branch.completion_summary = self._format_delegate_completion_summary(event)
            if event.summary and self.settings.delegates.show_completion:
                branch.completion_line = self._format_delegate_completion_line(event)
            if branch.completion_line:
                branch.response_text = ""
                self._remove_delegate_line(branch, "reply")
            elif branch.response_text:
                self._apply_delegate_response_text(branch, "", final=True)
            return
        if event_type == "subagent.text":
            self._apply_delegate_response_text(branch, event.preview or event.summary)
            return
        if event_type in {"subagent.thinking", "delegate.task_thinking", "_thinking"}:
            if self.settings.delegates.thinking != "summary":
                return
            text = event.preview or event.tool_name or event.summary
            if text:
                self._apply_delegate_thinking(branch, text)
            return
        branch.status = event.status or (
            "running" if branch.status in {"", "pending"} else branch.status
        )
        line = self._format_delegate_progress_line(event)
        if line:
            branch.lines.append(line)

    def prune_completed(self, ctx: SessionContext, *, now: float | None = None) -> None:
        ttl = self.settings.delegates.completed_ttl_seconds
        current = time.time() if now is None else now
        for key in list(ctx.delegate_order):
            branch = ctx.delegate_branches.get(key)
            if branch is None:
                with contextlib.suppress(ValueError):
                    ctx.delegate_order.remove(key)
                continue
            if not self._delegate_is_terminal(branch):
                continue
            if branch.completed_at and current - branch.completed_at > ttl:
                self.cancel_cleanup(branch)
                ctx.delegate_branches.pop(key, None)
                with contextlib.suppress(ValueError):
                    ctx.delegate_order.remove(key)

    @staticmethod
    def cancel_cleanup(branch: DelegateBranch) -> None:
        task = branch.cleanup_task
        if task is not None and not task.done():
            task.cancel()
        branch.cleanup_task = None

    @staticmethod
    def _delegate_is_terminal(branch: DelegateBranch) -> bool:
        return bool(
            branch.completed_at
            or str(branch.status or "").strip().lower()
            in {"completed", "done", "success", "failed", "error", "cancelled", "killed"}
        )

    def _format_delegate_progress_line(self, event: DelegateEvent) -> DelegateLine | None:
        if event.tool_name:
            return self._format_delegate_tool_line(event)
        text = self._delegate_line(
            event.preview or event.summary or "", self.settings.delegates.max_line_chars
        )
        if not text:
            return None
        return DelegateLine("update", text)

    def _apply_delegate_thinking(self, branch: DelegateBranch, text: str) -> None:
        merged = self._merge_thinking_text(branch.thinking_text, text)
        branch.thinking_text = merged
        line = DelegateLine(
            "thinking",
            self._delegate_line(
                merged, max(1, self.settings.delegates.max_line_chars - len("thinking: "))
            ),
        )
        self._replace_or_append_line(branch, line, "thinking")

    def _apply_delegate_response_text(
        self, branch: DelegateBranch, text: str, *, final: bool = False
    ) -> None:
        branch.response_text = self._merge_response_text(branch.response_text, text)
        if not final and not self._should_render_response_text(branch.response_text):
            return
        line = DelegateLine(
            "reply",
            self._delegate_line(
                branch.response_text,
                max(1, self.settings.delegates.max_line_chars - len("reply: ")),
            ),
        )
        self._replace_or_append_line(branch, line, "reply")

    def _should_render_response_text(self, text: str) -> bool:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(normalized) >= max(24, self.settings.delegates.max_line_chars // 3):
            return True
        return len(normalized.split()) >= 4

    @staticmethod
    def _merge_response_text(previous: str, current: str) -> str:
        return str(previous or "") + str(current or "")

    @staticmethod
    def _replace_or_append_line(branch: DelegateBranch, line: DelegateLine, kind: str) -> None:
        for idx in range(len(branch.lines) - 1, -1, -1):
            if branch.lines[idx].kind == kind:
                branch.lines[idx] = line
                return
        branch.lines.append(line)

    @staticmethod
    def _remove_delegate_line(branch: DelegateBranch, kind: str) -> None:
        branch.lines = type(branch.lines)(
            (line for line in branch.lines if line.kind != kind),
            maxlen=branch.lines.maxlen,
        )

    @staticmethod
    def _merge_thinking_text(previous: str, current: str) -> str:
        prior = re.sub(r"\s+", " ", str(previous or "")).strip()
        text = re.sub(r"\s+", " ", str(current or "")).strip()
        if not prior:
            return text
        if not text or text == prior:
            return prior
        if text.startswith(prior):
            return text
        if prior.endswith(text):
            return prior
        joiner = "" if prior[-1:].isspace() or text[:1].isspace() else " "
        return prior + joiner + text

    def _format_delegate_tool_line(self, event: DelegateEvent) -> DelegateLine | None:
        args = event_preview_args(event)
        if self._delegate_tool_detail_is_missing(event.tool_name, args, event.preview):
            if self.settings.renderer.density != "debug":
                return None
            return DelegateLine("debug", f"{event.tool_name}: <unknown>", tool_name=event.tool_name)
        if (
            event.tool_name == "patch"
            and event.preview
            and not event.args
            and "*** " not in event.preview
        ):
            text = self._delegate_line(
                f"patch: {event.preview}", self.settings.delegates.max_line_chars
            )
            return DelegateLine("tool", text, tool_name=event.tool_name)
        text = format_tool_line(
            event.tool_name,
            args,
            preview=event.preview,
            preview_length=self.settings.delegates.max_line_chars,
            patch_detail=self.settings.patch.detail,
            patch_preview_chars=self.settings.patch.preview_chars,
            patch_max_files=self.settings.patch.max_files,
        )
        if self.settings.renderer.style != "emoji":
            text = delegate_helpers.strip_tool_emoji(text)
        text = self._delegate_line(text, self.settings.delegates.max_line_chars)
        if not text:
            return None
        return DelegateLine(
            "tool",
            text,
            details=self._delegate_tool_details(event, args),
            tool_name=event.tool_name,
        )

    @staticmethod
    def _delegate_tool_detail_is_missing(
        tool_name: str, args: dict[str, Any], preview: str
    ) -> bool:
        if tool_name == "terminal":
            return not str(args.get("command") or preview or "").strip()
        if tool_name == "read_file":
            return not str(args.get("path") or preview or "").strip()
        if tool_name == "write_file":
            return not str(args.get("path") or args.get("file_path") or preview or "").strip()
        if tool_name == "search_files":
            return not str(args.get("pattern") or args.get("q") or preview or "").strip()
        return False

    def _delegate_tool_details(self, event: DelegateEvent, args: dict[str, Any]) -> tuple[str, ...]:
        if self.settings.renderer.density != "normal" or event.tool_name != "terminal":
            return ()
        details: list[str] = []
        cwd = args.get("workdir") or args.get("cwd")
        if cwd:
            details.append(f"cwd: {delegate_helpers.delegate_cwd(cwd)}")
        first = delegate_helpers.terminal_first_line(
            str(args.get("command") or event.preview or "")
        )
        if first:
            details.append(f"first: {first}")
        return tuple(details[:2])

    def _format_delegate_completion_line(self, event: DelegateEvent) -> str:
        summary = self._brief_completion_summary(event.summary)
        if not summary:
            return ""
        return self._format_completion_text(
            event, summary, limit=self.settings.delegates.max_line_chars
        )

    def _format_delegate_completion_summary(self, event: DelegateEvent) -> str:
        summary = re.sub(r"\s+", " ", str(event.summary or "")).strip()
        if not summary:
            return ""
        return self._format_completion_text(event, summary, limit=0)

    def _format_completion_text(self, event: DelegateEvent, summary: str, *, limit: int = 0) -> str:
        label = (
            "failed"
            if event.event_type == "subagent.failed" or event.status == "failed"
            else "done"
        )
        if self.settings.renderer.style == "emoji":
            label = f"{self._status_symbol(label)} {label}"
        summary = delegate_helpers.simplify_known_plugin_paths(summary)
        summary = re.sub(
            r"(?:~|/home/[^/]+)/.hermes/plugins/hermes-progress-tail\b",
            "hermes-progress-tail",
            summary,
        )
        text = f"{label}: {summary}"
        if limit > 0:
            return self._delegate_line(text, limit)
        return text

    @staticmethod
    def _brief_completion_summary(text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return ""
        if raw.startswith("{") or raw.startswith("["):
            return re.sub(r"\s+", " ", raw)
        lines = [line.strip(" -•\t") for line in raw.splitlines() if line.strip()]
        lines = [line for line in lines if line]
        if len(lines) >= 2 and lines[0].endswith(":"):
            value = f"{lines[0]} {lines[1]}"
        else:
            value = re.sub(r"\s+", " ", raw).strip()
        parts = re.split(r"(?:\s+-\s+|[.!?]\s+)", value, maxsplit=1)
        brief = parts[0].strip(" -•\t")
        return brief or value

    @staticmethod
    def _delegate_line(text: str, limit: int) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        return truncate_text(text, limit)

    def section(self, ctx: SessionContext) -> str:
        return self._sections.section(ctx)

    def _completion_result_text(self, branch: DelegateBranch) -> str:
        return self._sections._completion_result_text(branch)

    def _completion_result_lines(self, text: str) -> list[str]:
        return self._sections._completion_result_lines(text)

    def _prepare_completion_block_text(self, text: str) -> str:
        return self._sections._prepare_completion_block_text(text)

    @staticmethod
    def _split_inline_markdown_sections(text: str) -> str:
        return DelegateSections._split_inline_markdown_sections(text)

    @staticmethod
    def _simplify_long_paths(text: str) -> str:
        return DelegateSections._simplify_long_paths(text)

    def _completed_result_line_limit(self) -> int:
        return self._sections._completed_result_line_limit()

    def _completed_result_line_char_limit(self) -> int:
        return self._sections._completed_result_line_char_limit()

    @staticmethod
    def _middle_truncate_lines(lines: list[str], limit: int) -> list[str]:
        return DelegateSections._middle_truncate_lines(lines, limit)

    def _simplify_completion_line(self, text: str, *, branch: DelegateBranch | None = None) -> str:
        return self._sections._simplify_completion_line(text, branch=branch)

    def _delegate_result_only(self, branch: DelegateBranch) -> bool:
        return self._sections._delegate_result_only(branch)

    def _completed_result_limit(self) -> int:
        return self._sections._completed_result_limit()

    def _delegate_display_lines(self, branch: DelegateBranch) -> list[DelegateLine]:
        return self._sections._delegate_display_lines(branch)

    def _delegate_title(
        self, branch: DelegateBranch, *, inferred_task_count: int | None = None
    ) -> str:
        return self._sections._delegate_title(branch, inferred_task_count=inferred_task_count)

    def _delegate_connector(self, index: int, total: int) -> str:
        return self._sections._delegate_connector(index, total)

    def _delegate_compact_line(self, item: DelegateLine) -> str:
        return self._sections._delegate_compact_line(item)

    def _delegate_event_label(self, item: DelegateLine) -> str:
        return self._sections._delegate_event_label(item)

    @staticmethod
    def _delegate_tool_name(item: DelegateLine) -> str:
        return DelegateSections._delegate_tool_name(item)

    def _simplify_delegate_tool_text(self, text: str) -> str:
        return self._sections._simplify_delegate_tool_text(text)
