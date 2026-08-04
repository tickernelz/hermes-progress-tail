from __future__ import annotations

import re

from ..models.decision import DecisionRecord, DecisionState
from ..utils.redaction import redact_text

_ARCHIVE_CAPACITY = 24
_MAX_RECORD_CHARS = 360
_MAX_RENDERED_RECORDS = 12
_ELLIPSIS = " … "
_IDENTIFIERS = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*_id\s*=\s*\S+", re.IGNORECASE)
_PATHS = re.compile(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+")


def _compact_text(text: str) -> str:
    return " ".join(redact_text(text).split())


def _bounded_text(text: str) -> str:
    if len(text) <= _MAX_RECORD_CHARS:
        return text
    tail = text[-80:].lstrip()
    preserved = [match.group() for match in _IDENTIFIERS.finditer(text)]
    paths = _PATHS.findall(text)
    if paths:
        preserved.append(paths[-1])
    suffix = " | ".join(dict.fromkeys(preserved))
    suffix = suffix if suffix and suffix not in tail else tail
    suffix = suffix[: _MAX_RECORD_CHARS - len(_ELLIPSIS)]
    head_size = _MAX_RECORD_CHARS - len(_ELLIPSIS) - len(suffix)
    head = text[:head_size].rstrip()
    return (head + _ELLIPSIS if head else "") + suffix


class DecisionArchive:
    """A bounded, redacted, deterministic source of decision-relevant records."""

    def upsert(self, state: DecisionState, record: DecisionRecord) -> None:
        stored = DecisionRecord(
            record.kind,
            _bounded_text(_compact_text(record.text)),
            record.identity,
            record.priority,
            record.created_at,
        )
        records = [item for item in state.records if item.identity != stored.identity]
        records.append(stored)
        records.sort(key=lambda item: item.created_at)
        state.records.clear()
        state.records.extend(records[-_ARCHIVE_CAPACITY:])

    def observe_assistant(self, state: DecisionState, text: str, created_at: float) -> None:
        compact = _compact_text(text)
        latest = next((item for item in reversed(state.records) if item.kind == "assistant"), None)
        prior_prefix = latest.text.split(_ELLIPSIS, 1)[0].rstrip() if latest else ""
        if latest is not None and (
            compact.startswith(latest.text)
            or (_ELLIPSIS in latest.text and compact.startswith(prior_prefix))
        ):
            identity = latest.identity
        else:
            identity = self._new_assistant_identity(state, created_at)
        self.upsert(state, DecisionRecord("assistant", compact, identity, 10, created_at))

    def select(
        self, state: DecisionState, *, max_records: int, max_chars: int
    ) -> tuple[DecisionRecord, ...]:
        selected = []
        chars = 0
        limit = min(max(0, max_records), _MAX_RENDERED_RECORDS)
        candidates = sorted(
            state.records, key=lambda item: (item.priority, item.created_at), reverse=True
        )
        for record in candidates:
            if len(selected) == limit:
                break
            if chars + len(record.text) > max_chars:
                continue
            selected.append(record)
            chars += len(record.text)
        return tuple(sorted(selected, key=lambda item: item.created_at))

    @staticmethod
    def _new_assistant_identity(state: DecisionState, created_at: float) -> str:
        base = f"assistant:{created_at}"
        identities = {item.identity for item in state.records}
        suffix = 0
        identity = base
        while identity in identities:
            suffix += 1
            identity = f"{base}:{suffix}"
        return identity
