from __future__ import annotations

import logging
from functools import wraps
from typing import Any

logger = logging.getLogger(__name__)
_DELEGATE_ORIGINALS: dict[int, tuple[Any, Any]] = {}
_DELEGATE_PATCH_MARKER = "_hermes_progress_tail_delegate_patched"


def install_delegate_monkeypatches(delegate_module: Any | None = None) -> bool:
    if delegate_module is None:
        try:
            from tools import delegate_tool as delegate_module
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import delegate_tool: %s", exc)
            return False
    if getattr(delegate_module, _DELEGATE_PATCH_MARKER, False):
        return True
    original = getattr(delegate_module, "_build_child_progress_callback", None)
    if original is None:
        logger.warning(
            "hermes-progress-tail delegate monkeypatch disabled: delegate callback builder missing"
        )
        return False
    _DELEGATE_ORIGINALS[id(delegate_module)] = (delegate_module, original)

    @wraps(original)
    def patched_build_child_progress_callback(*args, **kwargs):
        original_cb = original(*args, **kwargs)
        task_index, goal, parent_agent = _extract_delegate_builder_args(args, kwargs)
        builder_identity = _delegate_builder_identity(args, kwargs, task_index, goal)

        def progress_tail_delegate_callback(
            event_type,
            tool_name: str | None = None,
            preview: str | None = None,
            cb_args=None,
            **event_kwargs,
        ):
            captured_args = dict(cb_args) if isinstance(cb_args, dict) else cb_args
            if original_cb is not None:
                try:
                    original_cb(event_type, tool_name, preview, cb_args, **event_kwargs)
                except Exception:
                    logger.debug(
                        "hermes-progress-tail original delegate callback failed", exc_info=True
                    )
            try:
                from ..runtime.plugin import on_delegate_progress_from_agent

                for key, value in builder_identity.items():
                    event_kwargs.setdefault(key, value)
                if "task_index" not in event_kwargs:
                    event_kwargs["task_index"] = task_index
                if "goal" not in event_kwargs and goal:
                    event_kwargs["goal"] = goal
                on_delegate_progress_from_agent(
                    parent_agent,
                    str(event_type or ""),
                    tool_name,
                    preview,
                    captured_args,
                    **event_kwargs,
                )
            except Exception:
                logger.debug("hermes-progress-tail delegate capture failed", exc_info=True)

        if original_cb is not None and hasattr(original_cb, "_flush"):
            progress_tail_delegate_callback._flush = original_cb._flush
        return progress_tail_delegate_callback

    delegate_module._build_child_progress_callback = patched_build_child_progress_callback
    setattr(delegate_module, _DELEGATE_PATCH_MARKER, True)
    return True


def _extract_delegate_builder_args(args, kwargs) -> tuple[int, str, Any]:
    task_index = kwargs.get("task_index")
    goal = kwargs.get("goal")
    parent_agent = kwargs.get("parent_agent")
    if task_index is None and len(args) > 0:
        task_index = args[0]
    if goal is None and len(args) > 1:
        goal = args[1]
    if parent_agent is None and len(args) > 2:
        parent_agent = args[2]
    try:
        task_index = int(task_index)
    except (TypeError, ValueError):
        task_index = 0
    return task_index, str(goal or ""), parent_agent


def _delegate_builder_identity(args, kwargs, task_index: int, goal: str) -> dict[str, Any]:
    identity: dict[str, Any] = {"task_index": task_index, "goal": goal}
    task_count = kwargs.get("task_count")
    if task_count is None and len(args) > 3:
        task_count = args[3]
    if task_count is not None:
        identity["task_count"] = task_count
    for key in ("subagent_id", "parent_id", "depth", "model", "toolsets"):
        if key in kwargs and kwargs[key] is not None:
            identity[key] = kwargs[key]
    return identity


def uninstall_delegate_monkeypatches(delegate_module: Any | None = None) -> bool:
    if delegate_module is None:
        try:
            from tools import delegate_tool as delegate_module
        except Exception:
            return False
    entry = _DELEGATE_ORIGINALS.pop(id(delegate_module), None)
    if entry is None:
        return False
    _, original = entry
    delegate_module._build_child_progress_callback = original
    try:
        delattr(delegate_module, _DELEGATE_PATCH_MARKER)
    except Exception:
        setattr(delegate_module, _DELEGATE_PATCH_MARKER, False)
    return True
