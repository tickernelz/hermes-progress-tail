from __future__ import annotations

import logging
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)
_ORIGINALS: dict[type, dict[str, Any]] = {}
_NOOP_MARKER = "_hermes_progress_tail_noop"
_PATCH_MARKER = "_hermes_progress_tail_patched"


def _noop_reasoning_callback(_text: str) -> None:
    return None


setattr(_noop_reasoning_callback, _NOOP_MARKER, True)


def install_monkeypatches(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import AIAgent: %s", exc)
            return False
    if getattr(agent_cls, _PATCH_MARKER, False):
        return True
    init = getattr(agent_cls, "__init__", None)
    fire_reasoning = getattr(agent_cls, "_fire_reasoning_delta", None)
    if init is None or fire_reasoning is None:
        logger.warning("hermes-progress-tail monkeypatch disabled: AIAgent callback API missing")
        return False
    _ORIGINALS[agent_cls] = {"__init__": init, "_fire_reasoning_delta": fire_reasoning}

    @wraps(init)
    def patched_init(self, *args, **kwargs):
        init(self, *args, **kwargs)
        if getattr(self, "reasoning_callback", None) is None:
            try:
                self.reasoning_callback = _noop_reasoning_callback
            except Exception:
                logger.debug(
                    "hermes-progress-tail could not set noop reasoning callback", exc_info=True
                )

    @wraps(fire_reasoning)
    def patched_fire_reasoning_delta(self, *args, **kwargs):
        text = args[0] if args else None
        if text is None:
            for key in ("text", "delta", "reasoning", "reasoning_text"):
                if kwargs.get(key):
                    text = kwargs[key]
                    break
        if text:
            try:
                from .plugin import on_reasoning_delta_from_agent

                on_reasoning_delta_from_agent(self, str(text))
            except Exception:
                logger.debug("hermes-progress-tail reasoning capture failed", exc_info=True)
        return fire_reasoning(self, *args, **kwargs)

    agent_cls.__init__ = patched_init
    agent_cls._fire_reasoning_delta = patched_fire_reasoning_delta
    setattr(agent_cls, _PATCH_MARKER, True)
    return True


def uninstall_monkeypatches(agent_cls: type | None = None) -> bool:
    if agent_cls is None:
        try:
            from run_agent import AIAgent as agent_cls
        except Exception:
            return False
    originals = _ORIGINALS.pop(agent_cls, None)
    if not originals:
        return False
    agent_cls.__init__ = originals["__init__"]
    agent_cls._fire_reasoning_delta = originals["_fire_reasoning_delta"]
    try:
        delattr(agent_cls, _PATCH_MARKER)
    except Exception:
        setattr(agent_cls, _PATCH_MARKER, False)
    return True
