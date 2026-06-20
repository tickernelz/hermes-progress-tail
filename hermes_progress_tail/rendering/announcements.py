from __future__ import annotations

import re
import time
import urllib.request

from ..utils.text import truncate_text

OFFICIAL_ANNOUNCEMENTS_URL = "https://hackmd.io/@egoi_TW8Qk-ZUxVvQJS6Bg/Syy_KzNMMx/download"
DEFAULT_TIMEOUT_SECONDS = 2.0
DEFAULT_TTL_SECONDS = 180.0
DEFAULT_MAX_CHARS = 900
_ANNOUNCEMENTS_CACHE: dict[str, object] = {"checked_at": 0.0, "text": ""}
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_SCRIPT_STYLE_RE = re.compile(r"<\s*(script|style)\b[\s\S]*?<\s*/\s*\1\s*>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def clear_announcements_cache() -> None:
    _ANNOUNCEMENTS_CACHE["checked_at"] = 0.0
    _ANNOUNCEMENTS_CACHE["text"] = ""


def official_announcements_markdown(
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    refresh: bool = False,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    now = time.time()
    cached_at = float(_ANNOUNCEMENTS_CACHE.get("checked_at") or 0.0)
    if not refresh and now - cached_at < ttl_seconds:
        return str(_ANNOUNCEMENTS_CACHE.get("text") or "")
    text = ""
    try:
        request = urllib.request.Request(
            OFFICIAL_ANNOUNCEMENTS_URL,
            headers={"User-Agent": "hermes-progress-tail-announcements"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            if 200 <= status < 300:
                payload = response.read()
                text = sanitize_announcement_markdown(
                    payload.decode("utf-8", "replace"),
                    max_chars=max_chars,
                )
    except Exception:
        text = ""
    _ANNOUNCEMENTS_CACHE["checked_at"] = now
    _ANNOUNCEMENTS_CACHE["text"] = text
    return text


def sanitize_announcement_markdown(markdown: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    text = str(markdown or "")
    if not text.strip():
        return ""
    text = _SCRIPT_STYLE_RE.sub("", text)
    text = _COMMENT_RE.sub("", text)
    text = _IMAGE_RE.sub("", text)
    text = _HTML_TAG_RE.sub("", text)
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return ""
    return truncate_text(cleaned, max_chars).strip()
