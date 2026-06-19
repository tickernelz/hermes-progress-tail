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


def _is_background_review_thread() -> bool:
    thread_name = threading.current_thread().name
    return thread_name == "bg-review" or thread_name.startswith("bg-review:")


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
