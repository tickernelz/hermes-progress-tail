from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any

from ..models.decision import DecisionRecord
from ..rendering.event_reducer import EventReducer
from ..rendering.formatter import extract_todo_items, format_tool_line, summarize_todo_items
from ..utils.redaction import redact_text

_MAX_TEXT = 360
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_VERIFYING = re.compile(
    r"\b(pytest|test|ruff|compile|verify|check|lint|git\s+(?:status|diff))\b", re.I
)


def _compact(value: Any) -> str:
    return " ".join(_CONTROL.sub(" ", _ANSI.sub("", redact_text(str(value)))).split())[:_MAX_TEXT]


def _line(tool_name: str, args: Mapping[str, Any] | None) -> str:
    return format_tool_line(tool_name, dict(args or {}), preview_length=120)


def _identity(tool_name: str, args: Mapping[str, Any] | None, tool_call_id: str) -> str:
    if tool_call_id:
        return f"tool:{tool_call_id}"
    fingerprint = EventReducer.tool_line_fingerprint(_line(tool_name, args))
    return f"tool:fp:{fingerprint}" if fingerprint else f"tool:{tool_name}"


def _failed(result: Any) -> bool:
    payload = result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return bool(re.search(r"\b(error|exception|traceback|failed)\b", payload, re.I))
    if isinstance(payload, Mapping):
        return bool(
            payload.get("success") is False
            or (isinstance(payload.get("exit_code"), int) and payload["exit_code"] != 0)
            or (payload.get("error") and payload.get("success") is not True)
        )
    return False


def _result_text(tool_name: str, result: Any) -> str:
    payload = result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            lines = [line.strip() for line in payload.splitlines() if line.strip()]
            return _compact(lines[-1] if lines else "")
    if not isinstance(payload, Mapping):
        return ""
    keys = ("error", "message", "summary")
    if tool_name == "terminal":
        keys = (*keys, "output")
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            lines = [line.strip() for line in value.splitlines() if line.strip()]
            return _compact(lines[-1] if lines else value)
    return ""


def _priority(tool_name: str, args: Mapping[str, Any] | None, result: Any) -> int:
    if _failed(result) or re.search(r"\bwarning\b", _result_text(tool_name, result), re.I):
        return 60
    if _VERIFYING.search(str((args or {}).get("command") or "")):
        return 40
    if tool_name in {"write_file", "patch", "todo"}:
        return 30
    return 10


def build_todo_record(args: Mapping[str, Any] | None) -> DecisionRecord:
    text = summarize_todo_items(extract_todo_items(dict(args or {})), limit=240)
    return DecisionRecord("plan", _compact("plan: " + text), "todo:plan", 20, time.monotonic())


def build_tool_start_record(
    tool_name: str, args: Mapping[str, Any] | None, tool_call_id: str = ""
) -> DecisionRecord:
    if tool_name == "todo":
        return build_todo_record(args)
    return DecisionRecord(
        "tool",
        _compact(_line(tool_name, args) + " · running"),
        _identity(tool_name, args, tool_call_id),
        _priority(tool_name, args, None),
        time.monotonic(),
    )


def build_tool_completion_record(
    tool_name: str, args: Mapping[str, Any] | None, result: Any, tool_call_id: str = ""
) -> DecisionRecord:
    if tool_name == "todo":
        return build_todo_record(args)
    status = "failed" if _failed(result) else "done"
    detail = _result_text(tool_name, result)
    warning = bool(re.search(r"\bwarning\b", detail, re.I))
    text = _line(tool_name, args) + f" · {status}"
    if detail:
        text += " · " + detail
    return DecisionRecord(
        "warning" if status == "failed" or warning else "tool",
        _compact(text),
        _identity(tool_name, args, tool_call_id),
        _priority(tool_name, args, result),
        time.monotonic(),
    )
