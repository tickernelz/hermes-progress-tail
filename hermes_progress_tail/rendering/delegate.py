from __future__ import annotations

import contextlib
import os
import re
import time
from contextlib import suppress
from pathlib import Path, PureWindowsPath
from typing import Any

from ..models.state import DelegateBranch, DelegateEvent, DelegateLine, SessionContext
from ..settings.config import Settings
from ..utils.redaction import redact_text
from ..utils.text import truncate_text
from .delegate_formatting import duration, event_preview_args, middle_truncate_text, status_symbol
from .formatter import format_tool_line


class DelegateProgressRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings

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
        for idx in range(len(branch.lines) - 1, -1, -1):
            if branch.lines[idx].kind == "thinking":
                branch.lines[idx] = line
                return
        branch.lines.append(line)

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
            text = self._strip_tool_emoji(text)
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
            details.append(f"cwd: {self._delegate_cwd(cwd)}")
        first = self._terminal_first_line(str(args.get("command") or event.preview or ""))
        if first:
            details.append(f"first: {first}")
        return tuple(details[:2])

    @staticmethod
    def _terminal_first_line(command: str) -> str:
        lines = [line.strip() for line in str(command or "").splitlines() if line.strip()]
        if not lines:
            return ""
        return truncate_text(redact_text(lines[0]), 80)

    @staticmethod
    def _delegate_cwd(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if raw in {".", "./"}:
            return "."
        normalized = raw.replace("\\", "/")
        if normalized.endswith("/hermes-progress-tail"):
            return "."
        home_display = DelegateProgressRenderer._home_relative_path(raw)
        if home_display:
            return truncate_text(redact_text(home_display), 80)
        return truncate_text(redact_text(raw), 80)

    @staticmethod
    def _home_relative_path(raw: str) -> str:
        candidates = []
        with suppress(Exception):
            candidates.append(str(Path.home()))
        env_home = os.environ.get("HOME")
        if env_home:
            candidates.append(env_home)
        userprofile = os.environ.get("USERPROFILE")
        if userprofile:
            candidates.append(userprofile)
        home_drive = os.environ.get("HOMEDRIVE")
        home_path = os.environ.get("HOMEPATH")
        if home_drive and home_path:
            candidates.append(home_drive + home_path)
        for home in dict.fromkeys(candidates):
            display = DelegateProgressRenderer._relative_to_home(raw, home)
            if display:
                return display
        return ""

    @staticmethod
    def _relative_to_home(raw: str, home: str) -> str:
        home = str(home or "").strip()
        if not home:
            return ""
        raw_norm = raw.replace("\\", "/").rstrip("/")
        home_norm = home.replace("\\", "/").rstrip("/")
        if not raw_norm or not home_norm:
            return ""
        if raw_norm == home_norm:
            return "~"
        if raw_norm.startswith(home_norm + "/"):
            rel = raw_norm[len(home_norm) + 1 :].strip("/")
            rel = re.sub(r"/+", "/", rel)
            return "~/" + rel if rel else "~"
        try:
            raw_win = PureWindowsPath(raw)
            home_win = PureWindowsPath(home)
            rel = raw_win.relative_to(home_win)
            return "~/" + rel.as_posix()
        except Exception:
            return ""

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
        summary = self._simplify_known_plugin_paths(summary)
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
    def _strip_tool_emoji(text: str) -> str:
        return re.sub(r"^[^\w\s]+\s+", "", str(text or "")).strip()

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
        if not ctx.delegate_branches:
            return ""
        self.prune_completed(ctx)
        if not ctx.delegate_branches:
            return ""
        settings = self.settings.delegates
        visible_keys = list(ctx.delegate_order)[-settings.max_delegates :]
        lines: list[str] = []
        visible_branches = [
            branch for key in visible_keys if (branch := ctx.delegate_branches.get(key)) is not None
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
        hidden = len(ctx.delegate_order) - len(visible_keys)
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
        value = self._simplify_known_plugin_paths(str(text or ""))
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
        value = self._simplify_known_plugin_paths(text)
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
        return f"update: {item.text}"

    @staticmethod
    def _delegate_tool_name(item: DelegateLine) -> str:
        if item.tool_name:
            return item.tool_name
        text = DelegateProgressRenderer._strip_tool_emoji(item.text)
        return text.split(":", 1)[0].strip() or "tool"

    def _simplify_delegate_tool_text(self, text: str) -> str:
        cleaned = self._strip_tool_emoji(text)
        cleaned = self._simplify_known_plugin_paths(cleaned)
        cleaned = re.sub(r"·\s+cwd\s+~/.hermes/plugins/hermes-progress-tail\b", "· cwd .", cleaned)
        cleaned = re.sub(
            r"·\s+cwd\s+/home/[^/]+/.hermes/plugins/hermes-progress-tail\b",
            "· cwd .",
            cleaned,
        )
        return cleaned

    @staticmethod
    def _simplify_known_plugin_paths(text: str) -> str:
        return re.sub(
            r"(?:~|/home/[^/]+)/.hermes/plugins/hermes-progress-tail/"
            r"hermes_progress_tail/([\w./-]+?\.py)(:\d+(?:\+\d+)?)?",
            lambda match: match.group(1) + (match.group(2) or ""),
            str(text or ""),
        )

    @staticmethod
    def _looks_like_progress_output(text: str) -> bool:
        lowered = str(text or "").lower()
        return any(token in lowered for token in ("<empty>", "stdout", "stderr", "exit_code"))


DelegateProgressRenderer._status_symbol = staticmethod(status_symbol)
DelegateProgressRenderer._duration = staticmethod(duration)
