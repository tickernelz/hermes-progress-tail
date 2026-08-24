from __future__ import annotations

import threading
from typing import Any

BACKGROUND_REVIEW_ORIGIN = "background_review"
_BACKGROUND_REVIEW_ATTRS = (
    "_memory_write_origin",
    "_memory_write_context",
    "memory_write_origin",
    "memory_write_context",
    "write_origin",
    "execution_context",
)


def _write_origin_is_background_review() -> bool:
    """Consult the Hermes skill-provenance ContextVar when available.

    Latest Hermes executes bg-review tools on ``DaemonThreadPoolExecutor``
    worker threads whose names are NOT ``bg-review``, so the thread-name
    heuristic alone misses them. Core binds the ``skill_write_origin``
    ContextVar per turn (``agent.turn_context``) and propagates it into
    tool worker threads via ``propagate_context_to_thread``, making it a
    reliable plugin-readable origin signal. Import-guarded so tests and
    non-Hermes hosts degrade to the other detectors.
    """
    try:
        from tools.skill_provenance import get_current_write_origin
    except Exception:
        return False
    try:
        origin = str(get_current_write_origin() or "").strip().lower()
    except Exception:
        return False
    return origin == BACKGROUND_REVIEW_ORIGIN


def _is_background_review_thread() -> bool:
    thread_name = threading.current_thread().name
    if thread_name == "bg-review" or thread_name.startswith("bg-review:"):
        return True
    return _write_origin_is_background_review()


def _is_background_review_agent(agent: Any) -> bool:
    if agent is None:
        return False
    for attr in _BACKGROUND_REVIEW_ATTRS:
        value = getattr(agent, attr, "")
        if str(value or "").strip().lower() == BACKGROUND_REVIEW_ORIGIN:
            return True
    return False


def _should_suppress_agent_progress(agent: Any = None) -> bool:
    return _is_background_review_agent(agent) or _is_background_review_thread()
