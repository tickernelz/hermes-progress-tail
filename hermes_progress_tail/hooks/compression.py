from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from .agent import _positive_int
from .contracts import HookCallbacks, current_hook_callbacks

logger = logging.getLogger(__name__)
_COMPRESSION_STATUS_ORIGINALS: dict[type, Any] = {}
_COMPRESSION_LIFECYCLE_ORIGINALS: dict[type, Any] = {}
_COMPRESSION_STATUS_PATCH_MARKER = "_hermes_progress_tail_compression_status_patched"
_COMPRESSION_LIFECYCLE_PATCH_MARKER = "_hermes_progress_tail_compression_lifecycle_patched"


def install_compression_status_monkeypatch(
    agent_cls: type | None = None, *, callbacks: HookCallbacks | None = None
) -> bool:
    callbacks = callbacks if callbacks is not None else current_hook_callbacks()
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import AIAgent for compression: %s", exc)
            return False
    if getattr(agent_cls, _COMPRESSION_STATUS_PATCH_MARKER, False):
        return True
    emit_status = getattr(agent_cls, "_emit_status", None)
    if emit_status is None:
        logger.debug("hermes-progress-tail compression status monkeypatch disabled: API missing")
        return False
    _COMPRESSION_STATUS_ORIGINALS[agent_cls] = emit_status

    @wraps(emit_status)
    def patched_emit_status(self, text, *args, **kwargs):
        if _looks_like_compression_status(text):
            handled = False
            try:
                handled = callbacks.on_compression_status(self, str(text or ""))
            except Exception:
                logger.debug(
                    "hermes-progress-tail compression status capture failed", exc_info=True
                )
            if handled:
                return None
        return emit_status(self, text, *args, **kwargs)

    agent_cls._emit_status = patched_emit_status
    setattr(agent_cls, _COMPRESSION_STATUS_PATCH_MARKER, True)
    return True


def _looks_like_compression_status(text: Any) -> bool:
    value = str(text or "").lower()
    return (
        "compacting context" in value
        or "preflight compression" in value
        or "compacting" in value
        and "context" in value
    )


def install_compression_lifecycle_monkeypatch(
    agent_cls: type | None = None, *, callbacks: HookCallbacks | None = None
) -> bool:
    callbacks = callbacks if callbacks is not None else current_hook_callbacks()
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception as exc:
            logger.debug(
                "hermes-progress-tail could not import AIAgent for compression lifecycle: %s",
                exc,
            )
            return False
    if getattr(agent_cls, _COMPRESSION_LIFECYCLE_PATCH_MARKER, False):
        return True
    compress_context = getattr(agent_cls, "_compress_context", None)
    if compress_context is None:
        logger.debug("hermes-progress-tail compression lifecycle disabled: API missing")
        return False
    _COMPRESSION_LIFECYCLE_ORIGINALS[agent_cls] = compress_context

    @wraps(compress_context)
    def patched_compress_context(self, messages, system_message, *args, **kwargs):
        old_session_id = str(getattr(self, "session_id", "") or "")
        before_count = len(messages) if hasattr(messages, "__len__") else 0
        before_tokens = kwargs.get("approx_tokens")
        try:
            callbacks.on_compression_lifecycle(
                self,
                phase="started",
                old_session_id=old_session_id,
                before_count=before_count,
                before_tokens=before_tokens,
            )
        except Exception:
            logger.debug("hermes-progress-tail compression lifecycle start failed", exc_info=True)
        try:
            result = compress_context(self, messages, system_message, *args, **kwargs)
        except Exception as exc:
            try:
                callbacks.on_compression_lifecycle(
                    self,
                    phase="failed",
                    old_session_id=old_session_id,
                    before_count=before_count,
                    before_tokens=before_tokens,
                    error=str(exc),
                )
            except Exception:
                logger.debug(
                    "hermes-progress-tail compression lifecycle failure capture failed",
                    exc_info=True,
                )
            raise
        try:
            compressed = result[0] if isinstance(result, tuple) and result else result
            after_count = len(compressed) if hasattr(compressed, "__len__") else 0
            compressor = getattr(self, "context_compressor", None)
            status = compressor.get_status() if hasattr(compressor, "get_status") else {}
            after_tokens = getattr(compressor, "last_prompt_tokens", None)
            if isinstance(status, dict):
                status_tokens = status.get("last_prompt_tokens")
                if _positive_int(status_tokens) is not None:
                    after_tokens = status_tokens
            after_tokens_kind = ""
            if _positive_int(after_tokens) is None and getattr(
                compressor, "awaiting_real_usage_after_compression", False
            ):
                rough_tokens = getattr(compressor, "last_compression_rough_tokens", None)
                if _positive_int(rough_tokens) is not None:
                    after_tokens = rough_tokens
                    after_tokens_kind = "rough"
            callbacks.on_compression_lifecycle(
                self,
                phase="completed",
                old_session_id=old_session_id,
                new_session_id=str(getattr(self, "session_id", "") or ""),
                before_count=before_count,
                after_count=after_count,
                before_tokens=before_tokens,
                after_tokens=after_tokens,
                after_tokens_kind=after_tokens_kind,
                compression_count=getattr(compressor, "compression_count", 0),
            )
        except Exception:
            logger.debug(
                "hermes-progress-tail compression lifecycle completion failed", exc_info=True
            )
        return result

    agent_cls._compress_context = patched_compress_context
    setattr(agent_cls, _COMPRESSION_LIFECYCLE_PATCH_MARKER, True)
    return True


def uninstall_compression_status_monkeypatch(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception:
            return False
    original = _COMPRESSION_STATUS_ORIGINALS.pop(agent_cls, None)
    if original is None:
        return False
    agent_cls._emit_status = original
    try:
        delattr(agent_cls, _COMPRESSION_STATUS_PATCH_MARKER)
    except Exception:
        setattr(agent_cls, _COMPRESSION_STATUS_PATCH_MARKER, False)
    return True


def uninstall_compression_lifecycle_monkeypatch(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception:
            return False
    original = _COMPRESSION_LIFECYCLE_ORIGINALS.pop(agent_cls, None)
    if original is None:
        return False
    agent_cls._compress_context = original
    try:
        delattr(agent_cls, _COMPRESSION_LIFECYCLE_PATCH_MARKER)
    except Exception:
        setattr(agent_cls, _COMPRESSION_LIFECYCLE_PATCH_MARKER, False)
    return True
