from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|secret|password|passwd|authorization|bearer|cookie|private[_-]?key|session)",
    re.IGNORECASE,
)
_ENV_SECRET_RE = re.compile(
    r"\b[A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH)[A-Z0-9_]*\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s'\"]+)",
    re.IGNORECASE,
)
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_PRIVATE_KEY_PLACEHOLDER_RE = re.compile(r"\[REDACTED PRIVATE KEY\]", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,}\b")
_BLOB_RE = re.compile(r"(?<![/~.-])\b[A-Za-z0-9_+/=-]{80,}\b(?![.][A-Za-z0-9]{1,12}\b)")
_AUTH_HEADER_RE = re.compile(
    r"\b(Authorization\s*:\s*Bearer)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
_COOKIE_RE = re.compile(r"\b(Cookie\s*:\s*)[^\n\r]+", re.IGNORECASE)
_SECRET_HEADER_RE = re.compile(
    r"\b((?:X-)?(?:API-Key|Auth-Token|Amz-Security-Token|AuthToken|Access-Token)\s*:\s*)(?:'[^']*'|\"[^\"]*\"|[^\s]+)",
    re.IGNORECASE,
)
_SECRET_FLAG_RE = re.compile(
    r"(?i)(--(?:password|passwd|token|secret|api-key|api_key|authorization)\s+)(?:'[^']*'|\"[^\"]*\"|[^\s]+)"
)
_SK_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{10,}|(?:ghp|gho|github_pat)_[A-Za-z0-9_\-]{10,})\b")


def is_secret_key(key: Any) -> bool:
    return bool(_SECRET_KEY_RE.search(str(key)))


def redact_text(value: str) -> str:
    text = str(value)
    text = _PRIVATE_KEY_RE.sub("[redacted_private_key]", text)
    text = _PRIVATE_KEY_PLACEHOLDER_RE.sub("[redacted_private_key]", text)
    text = _ENV_SECRET_RE.sub("[redacted_env]", text)
    text = _SECRET_FLAG_RE.sub(lambda match: match.group(1) + "[redacted]", text)
    text = _AUTH_HEADER_RE.sub(lambda match: match.group(1) + " [redacted]", text)
    text = _SECRET_HEADER_RE.sub(lambda match: match.group(1) + "[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _COOKIE_RE.sub(lambda match: match.group(1) + "[redacted]", text)
    text = _JWT_RE.sub("[redacted_jwt]", text)
    text = _SK_RE.sub("[redacted_token]", text)
    text = _BLOB_RE.sub("[redacted_blob]", text)
    return text


def _project_relative_path(raw: str) -> str | None:
    if not raw.startswith("/"):
        return raw or None
    try:
        resolved = Path(raw).expanduser().resolve(strict=False)
    except Exception:
        resolved = Path(raw)
    cwd = Path.cwd().resolve(strict=False)
    candidates = [cwd]
    for marker in ("Projects", "projects"):
        parts = resolved.parts
        if marker in parts:
            idx = parts.index(marker)
            if idx + 2 <= len(parts):
                candidates.append(Path(*parts[: idx + 2]))
    for base in candidates:
        try:
            return resolved.relative_to(base).as_posix() or resolved.name
        except ValueError:
            continue
    home = Path.home().resolve(strict=False)
    try:
        return "~/" + resolved.relative_to(home).as_posix()
    except ValueError:
        return None


def _wsl_windows_home_path(raw: str) -> str | None:
    match = re.match(r"^/mnt/[a-zA-Z]/Users/([^/]+)(?:/(.*))?$", raw)
    if not match:
        return None
    remainder = match.group(2) or ""
    return "~/" + remainder if remainder else "~"


def _looks_like_preservable_path_component(value: str) -> bool:
    path = PurePosixPath(value)
    stem = path.stem if path.suffix else value
    if len(stem) < 80:
        return False
    if path.suffix:
        return bool(re.fullmatch(r"[A-Za-z0-9_.-]+", value))
    if any(ch.isdigit() for ch in stem):
        return False
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z_.-]*", stem))


def _redact_path_display(path: str) -> str:
    redacted_parts = []
    for part in path.split("/"):
        if part in {"", "~"}:
            redacted_parts.append(part)
            continue
        redacted = redact_text(part)
        if redacted.startswith("[redacted_blob]") and _looks_like_preservable_path_component(part):
            redacted = part
        redacted_parts.append(redacted)
    return "/".join(redacted_parts)


def simplify_path(path: Any) -> str:
    raw = str(path or "").strip()
    if not raw:
        return "<unknown>"
    if raw.startswith("[redacted_blob]") and "/" not in raw:
        return raw
    wsl_home = _wsl_windows_home_path(raw)
    if wsl_home is not None:
        return _redact_path_display(wsl_home)
    relative = _project_relative_path(raw)
    return _redact_path_display(relative) if relative else _redact_path_display(raw)


def sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: "[redacted]" if is_secret_key(key) else sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(sanitize(item) for item in value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value
