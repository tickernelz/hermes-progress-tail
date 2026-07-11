from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from .contracts import HookCallbacks, current_hook_callbacks
from .install_report import PatchStatus
from .status_helpers import structured_patch_status

logger = logging.getLogger(__name__)
_DELEGATE_ORIGINALS: dict[int, tuple[Any, Any]] = {}
_DELEGATE_PATCH_MARKER = "_hermes_progress_tail_delegate_patched"


def _mutate_delegate_monkeypatches(
    delegate_module: Any | None = None, *, callbacks: HookCallbacks | None = None
) -> bool:
    callbacks = callbacks if callbacks is not None else current_hook_callbacks()
    if delegate_module is None:
        try:
            from tools import delegate_tool as delegate_module
        except Exception as exc:
            logger.debug("hermes-progress-tail could not import delegate_tool: %s", exc)
            return False
    if getattr(delegate_module, _DELEGATE_PATCH_MARKER, False):
        entry = _DELEGATE_ORIGINALS.get(id(delegate_module))
        return bool(
            entry
            and entry[0] is delegate_module
            and callable(entry[1])
            and callable(getattr(delegate_module, "_build_child_progress_callback", None))
        )
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
                for key, value in builder_identity.items():
                    event_kwargs.setdefault(key, value)
                if "task_index" not in event_kwargs:
                    event_kwargs["task_index"] = task_index
                if "goal" not in event_kwargs and goal:
                    event_kwargs["goal"] = goal
                callbacks.on_delegate_progress(
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


def _delegate_patch_status(
    delegate_module: Any | None = None,
    *,
    callbacks: HookCallbacks | None = None,
) -> PatchStatus:
    def resolver():
        from tools import delegate_tool

        return delegate_tool

    return structured_patch_status(
        name="delegate_progress",
        target_label="tools.delegate_tool._build_child_progress_callback",
        target=delegate_module,
        resolver=resolver,
        members=("_build_child_progress_callback",),
        mutate=lambda target: _mutate_delegate_monkeypatches(target, callbacks=callbacks),
    )


def install_delegate_monkeypatches(
    delegate_module: Any | None = None,
    *,
    callbacks: HookCallbacks | None = None,
) -> bool:
    return bool(_delegate_patch_status(delegate_module, callbacks=callbacks).installed)
