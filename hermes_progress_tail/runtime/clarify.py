from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..models.decision import CheckpointStatus
from ..utils.redaction import redact_text

logger = logging.getLogger(__name__)

_BARRIER_TIMEOUT_SECONDS = 3.0
_ANSWER_LIMIT = 240
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


@dataclass
class FreezeAttemptGuard:
    cancelled: threading.Event

    def invalidate(self) -> None:
        self.cancelled.set()

    def is_cancelled(self) -> bool:
        return self.cancelled.is_set()


def _usable_loop(ctx: Any) -> Any | None:
    loop = getattr(ctx, "loop", None)
    if loop is None or loop.is_closed() or not loop.is_running():
        return None
    return loop


def _on_loop(loop: Any) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def _diagnostic(label: str, exc: BaseException | str) -> None:
    text = _CONTROL.sub(" ", redact_text(str(exc)))
    logger.debug("hermes-progress-tail clarify %s: %s", label, " ".join(text.split())[:160])


def _consume(label: str):
    def callback(future: Any) -> None:
        try:
            future.result()
        except Exception as exc:
            _diagnostic(label, exc)

    return callback


def run_clarify_freeze_barrier(
    ctx: Any, operation: Callable[[FreezeAttemptGuard], Awaitable[bool]]
) -> bool:
    """Run the worker-thread freeze fence, or safely schedule the same-loop fallback."""
    loop = _usable_loop(ctx)
    if loop is None:
        return False
    guard = FreezeAttemptGuard(threading.Event())
    try:
        if _on_loop(loop):
            loop.create_task(operation(guard)).add_done_callback(
                _consume("same-loop freeze failed")
            )
            return True
        future = asyncio.run_coroutine_threadsafe(operation(guard), loop)
        return bool(future.result(timeout=_BARRIER_TIMEOUT_SECONDS))
    except Exception as exc:
        guard.invalidate()
        if "future" in locals():
            future.cancel()
        _diagnostic("freeze barrier failed", exc)
        return False


def run_clarify_resolution_bridge(ctx: Any, operation: Callable[[], Awaitable[bool]]) -> None:
    """Bound hook-thread waiting without cancelling an already queued resolution."""
    loop = _usable_loop(ctx)
    if loop is None:
        return
    try:
        if _on_loop(loop):
            loop.create_task(operation()).add_done_callback(_consume("same-loop resolution failed"))
            return
        future = asyncio.run_coroutine_threadsafe(operation(), loop)
        future.result(timeout=_BARRIER_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        future.add_done_callback(_consume("late resolution failed"))
        _diagnostic("resolution bridge timed out", exc)
    except Exception as exc:
        _diagnostic("resolution bridge failed", exc)


def _response_text(value: Any) -> str:
    if not isinstance(value, (str, int, float, bool)):
        return ""
    return _CONTROL.sub(" ", str(value)).strip()


def _answer(values: list[Any]) -> str:
    parts = [_response_text(value) for value in values]
    text = ", ".join(part for part in parts if part)
    text = " ".join(redact_text(text).split())
    return text[:_ANSWER_LIMIT].rstrip()


def _sentinel(value: str) -> CheckpointStatus | None:
    normalized = value.strip().casefold()
    if normalized.startswith("[user did not respond"):
        return "timed_out"
    if normalized.startswith("[clarify prompt could not be delivered"):
        return "prompt_failed"
    return None


def classify_clarify_result(result: Any) -> tuple[CheckpointStatus, str]:
    """Read only clarify_tool's response field; malformed host output fails safely."""
    payload = result
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            return "prompt_failed", ""
    if not isinstance(payload, Mapping) or payload.get("success") is False or payload.get("error"):
        return "prompt_failed", ""
    if "user_response" not in payload:
        return "prompt_failed", ""
    response = payload["user_response"]
    if isinstance(response, list):
        if len(response) == 1:
            sentinel = _sentinel(_response_text(response[0]))
            if sentinel:
                return sentinel, ""
        answer = _answer(response)
    else:
        text = _response_text(response)
        sentinel = _sentinel(text)
        if sentinel:
            return sentinel, ""
        answer = _answer([response])
    return ("resolved", answer) if answer else ("prompt_failed", "")
