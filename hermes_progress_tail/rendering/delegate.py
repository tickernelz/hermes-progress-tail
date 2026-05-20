from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path, PureWindowsPath
from typing import Any

from ..models.state import DelegateBranch, DelegateEvent, DelegateLine, SessionContext
from ..settings.config import Settings
from ..utils.redaction import redact_text
from ..utils.text import truncate_text
from .formatter import format_tool_line


def middle_truncate_text(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if limit <= 0 or len(value) <= limit:
        return value
    if limit <= 12:
        return truncate_text(value, limit)
    separator = " … "
    remaining = max(1, limit - len(separator))
    head_len = max(1, int(remaining * 0.58))
    tail_len = max(1, remaining - head_len)
    head = value[:head_len].rstrip(" ,.;:-")
    tail = value[-tail_len:].lstrip(" ,.;:-")
    return f"{head}{separator}{tail}"


def event_preview_args(event: DelegateEvent) -> dict[str, Any]:
    preview = str(event.preview or "").strip()
    args = dict(event.args) if isinstance(event.args, dict) else {}
    if event.tool_name == "terminal" and preview:
        command = str(args.get("command") or "").strip()
        if not command or len(preview) > len(command):
            args["command"] = preview
    if args:
        if preview:
            if event.tool_name in {"read_file", "write_file"} and not (
                args.get("path") or args.get("file_path")
            ):
                args["path"] = preview
            elif event.tool_name == "search_files" and not (args.get("pattern") or args.get("q")):
                args["pattern"] = preview
        return args
    if not preview:
        return {}
    if event.tool_name == "terminal":
        return {"command": preview}
    if event.tool_name in {"read_file", "write_file"}:
        return {"path": preview}
    if event.tool_name == "search_files":
        return {"pattern": preview}
    if event.tool_name == "patch":
        if "*** " in preview:
            return {"mode": "patch", "patch": preview}
        return {"path": preview, "old_string": "", "new_string": ""}
    return {}


class DelegateProgressRenderer:
    def __init__(self, settings: Settings):
        self.settings = settings

    def apply_event(self, ctx: SessionContext, event: DelegateEvent) -> None:
        event_type = event.event_type
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
                branch.completion_line = ""
                branch.lines.clear()
                branch.completed_at = 0.0
                branch.duration_seconds = 0.0
                branch.tool_count = 0
                branch.lifecycle_started = False
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
            if event.summary and self.settings.delegates.show_completion:
                branch.completion_line = self._format_delegate_completion_line(event)
            return
        if event_type in {"subagent.thinking", "delegate.task_thinking", "_thinking"}:
            if self.settings.delegates.thinking != "summary":
                return
            text = event.preview or event.tool_name or event.summary
            if text:
                branch.lines.append(
                    DelegateLine(
                        "update",
                        self._delegate_line(
                            f"thinking: {text}", self.settings.delegates.max_line_chars
                        ),
                    )
                )
            return
        branch.status = event.status or (
            "running" if branch.status in {"", "pending"} else branch.status
        )
        line = self._format_delegate_progress_line(event)
        if line:
            branch.lines.append(line)

    def _format_delegate_progress_line(self, event: DelegateEvent) -> DelegateLine | None:
        if event.tool_name:
            return self._format_delegate_tool_line(event)
        text = self._delegate_line(
            event.preview or event.summary or "", self.settings.delegates.max_line_chars
        )
        if not text:
            return None
        return DelegateLine("update", text)

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
        return self._delegate_line(f"{label}: {summary}", self.settings.delegates.max_line_chars)

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
                    current = self._simplify_completion_line(branch.completion_line, branch=branch)
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
                lines.append(
                    f"{connector} result: {self._simplify_completion_line(branch.completion_line, branch=branch)}"
                )
        hidden = len(ctx.delegate_order) - len(visible_keys)
        if hidden > 0:
            lines.append(f"+{hidden} older delegate{'s' if hidden != 1 else ''}")
        if not lines:
            return ""
        header = "🔀 Delegates" if self.settings.renderer.style == "emoji" else "Delegates"
        return header + "\n" + "\n".join(lines)

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

    @staticmethod
    def _status_symbol(status: str) -> str:
        normalized = str(status or "").strip().lower()
        if normalized in {"completed", "done"}:
            return "✓"
        if normalized in {"failed", "cancelled"}:
            return "✗"
        if normalized in {"queued", "pending"}:
            return "…"
        return "→"

    @staticmethod
    def _duration(seconds: float) -> str:
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return ""
        if value < 10:
            return f"{value:.1f}s"
        return f"{value:.0f}s"
