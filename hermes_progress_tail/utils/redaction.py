from __future__ import annotations

import re
from collections.abc import Mapping
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
