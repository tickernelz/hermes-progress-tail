from __future__ import annotations

import os
import re
from contextlib import suppress
from pathlib import Path, PureWindowsPath
from typing import Any

from ..utils.redaction import redact_text
from ..utils.text import truncate_text


def terminal_first_line(command: str) -> str:
    lines = [line.strip() for line in str(command or "").splitlines() if line.strip()]
    if not lines:
        return ""
    return truncate_text(redact_text(lines[0]), 80)


def delegate_cwd(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in {".", "./"}:
        return "."
    normalized = raw.replace("\\", "/")
    if normalized.endswith("/hermes-progress-tail"):
        return "."
    home_display = _home_relative_path(raw)
    if home_display:
        return truncate_text(redact_text(home_display), 80)
    return truncate_text(redact_text(raw), 80)


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
        display = _relative_to_home(raw, home)
        if display:
            return display
    return ""


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


def strip_tool_emoji(text: str) -> str:
    return re.sub(r"^[^\w\s]+\s+", "", str(text or "")).strip()


def simplify_known_plugin_paths(text: str) -> str:
    return re.sub(
        r"(?:~|/home/[^/]+)/.hermes/plugins/hermes-progress-tail/"
        r"hermes_progress_tail/([\w./-]+?\.py)(:\d+(?:\+\d+)?)?",
        lambda match: match.group(1) + (match.group(2) or ""),
        str(text or ""),
    )
