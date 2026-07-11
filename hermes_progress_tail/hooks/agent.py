from __future__ import annotations

import logging
import threading
from contextlib import suppress
from functools import wraps
from typing import Any

from .contracts import HookCallbacks, current_hook_callbacks

logger = logging.getLogger(__name__)
_ORIGINALS: dict[type, dict[str, Any]] = {}
_NOOP_MARKER = "_hermes_progress_tail_noop"
_PATCH_MARKER = "_hermes_progress_tail_patched"
_TOOL_AGENT_CONTEXT_LOCAL = threading.local()


def current_tool_agent_context(
    *,
    tool_name: str = "",
    session_id: str = "",
    task_id: str = "",
    tool_call_id: str = "",
) -> dict[str, Any]:
    """Return the current AIAgent tool invocation context for plugin hooks.

    Hermes core pre/post tool hooks are observational and do not include the
    owning AIAgent object. Progress-tail needs that object to refresh runtime
    metadata (ctx window, model/provider, stable session key) on every tool
    event. The AIAgent monkeypatch below installs a thread-local bridge around
    ``_invoke_tool`` so the hook can resolve the agent without changing Hermes
    core hook signatures.
    """
    stack = getattr(_TOOL_AGENT_CONTEXT_LOCAL, "stack", None)
    if not stack:
        return {}
    for item in reversed(stack):
        if _tool_context_matches(
            item,
            tool_name=tool_name,
            session_id=session_id,
            task_id=task_id,
            tool_call_id=tool_call_id,
        ):
            return dict(item)
    return {}


def _tool_context_matches(
    item: dict[str, Any],
    *,
    tool_name: str,
    session_id: str,
    task_id: str,
    tool_call_id: str,
) -> bool:
    if tool_name and item.get("tool_name") and item.get("tool_name") != tool_name:
        return False
    if tool_call_id and item.get("tool_call_id") and item.get("tool_call_id") != tool_call_id:
        return False
    if session_id and item.get("session_id") and item.get("session_id") != session_id:
        return False
    return not (task_id and item.get("task_id") and item.get("task_id") != task_id)


def _push_tool_agent_context(item: dict[str, Any]) -> None:
    stack = getattr(_TOOL_AGENT_CONTEXT_LOCAL, "stack", None)
    if stack is None:
        stack = []
        _TOOL_AGENT_CONTEXT_LOCAL.stack = stack
    stack.append(item)


def _pop_tool_agent_context(item: dict[str, Any]) -> None:
    stack = getattr(_TOOL_AGENT_CONTEXT_LOCAL, "stack", None)
    if not stack:
        return
    if stack[-1] is item:
        stack.pop()
        return
    with suppress(ValueError):
        stack.remove(item)


def _noop_reasoning_callback(_text: str) -> None:
    return None


setattr(_noop_reasoning_callback, _NOOP_MARKER, True)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def install_agent_monkeypatches(
    agent_cls: type | None = None, *, callbacks: HookCallbacks | None = None
) -> bool:
    callbacks = callbacks if callbacks is not None else current_hook_callbacks()
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
    emit_interim = getattr(agent_cls, "_emit_interim_assistant_message", None)
    invoke_tool = getattr(agent_cls, "_invoke_tool", None)
    if init is None or fire_reasoning is None:
        logger.warning("hermes-progress-tail monkeypatch disabled: AIAgent callback API missing")
        return False
    _ORIGINALS[agent_cls] = {
        "__init__": init,
        "_fire_reasoning_delta": fire_reasoning,
        "_emit_interim_assistant_message": emit_interim,
        "_invoke_tool": invoke_tool,
    }

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
        try:
            self.stream_delta_callback = _wrap_stream_delta_callback(
                self, getattr(self, "stream_delta_callback", None), callbacks=callbacks
            )
        except Exception:
            logger.debug("hermes-progress-tail could not wrap stream delta callback", exc_info=True)

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
                callbacks.on_reasoning_delta(self, str(text))
            except Exception:
                logger.debug("hermes-progress-tail reasoning capture failed", exc_info=True)
        return fire_reasoning(self, *args, **kwargs)

    def patched_emit_interim_assistant_message(self, assistant_msg):
        if emit_interim is None:
            return None
        handled = False
        visible = _assistant_visible_text(self, assistant_msg)
        already_streamed = _assistant_already_streamed(self, visible, assistant_msg)
        if visible:
            try:
                handled = callbacks.on_assistant_progress(
                    self, visible, already_streamed=already_streamed
                )
            except Exception:
                logger.debug(
                    "hermes-progress-tail assistant progress capture failed", exc_info=True
                )
        if handled:
            return None
        return emit_interim(self, assistant_msg)

    def patched_invoke_tool(
        self,
        function_name,
        function_args,
        effective_task_id,
        tool_call_id=None,
        messages=None,
        *args,
        **kwargs,
    ):
        if invoke_tool is None:
            return None
        item = {
            "agent": self,
            "tool_name": str(function_name or ""),
            "task_id": str(effective_task_id or ""),
            "session_id": str(getattr(self, "session_id", "") or ""),
            "session_key": str(
                getattr(self, "gateway_session_key", None)
                or getattr(self, "_gateway_session_key", None)
                or ""
            ),
            "tool_call_id": str(tool_call_id or ""),
            "messages": messages,
        }
        _push_tool_agent_context(item)
        try:
            return invoke_tool(
                self,
                function_name,
                function_args,
                effective_task_id,
                tool_call_id,
                messages,
                *args,
                **kwargs,
            )
        finally:
            _pop_tool_agent_context(item)

    agent_cls.__init__ = patched_init
    agent_cls._fire_reasoning_delta = patched_fire_reasoning_delta
    if emit_interim is not None:
        agent_cls._emit_interim_assistant_message = wraps(emit_interim)(
            patched_emit_interim_assistant_message
        )
    if invoke_tool is not None:
        agent_cls._invoke_tool = wraps(invoke_tool)(patched_invoke_tool)
    setattr(agent_cls, _PATCH_MARKER, True)
    return True


def _assistant_visible_text(agent: Any, assistant_msg: Any) -> str:
    if not isinstance(assistant_msg, dict):
        return ""
    content = str(assistant_msg.get("content") or "")
    if not content:
        return ""
    stripper = getattr(agent, "_strip_think_blocks", None)
    if callable(stripper):
        with suppress(Exception):
            content = stripper(content)
    return content.strip()


def _assistant_already_streamed(agent: Any, visible: str, assistant_msg: Any) -> bool:
    checker = getattr(agent, "_interim_content_was_streamed", None)
    if callable(checker) and visible:
        with suppress(Exception):
            return bool(checker(visible))
    if isinstance(assistant_msg, dict):
        return bool(assistant_msg.get("already_streamed"))
    return False


def _wrap_stream_delta_callback(
    agent: Any, callback: Any, *, callbacks: HookCallbacks | None = None
) -> Any:
    callbacks = callbacks if callbacks is not None else current_hook_callbacks()
    if callback is None or getattr(callback, "_hermes_progress_tail_inline_think_wrapped", False):
        return callback

    @wraps(callback)
    def wrapped_stream_delta(text, *args, **kwargs):
        raw_text = str(text or "")
        fail_open_text = _inline_reasoning_pending(agent) + raw_text
        if not _agent_reasoning_enabled(agent, callbacks):
            _reset_inline_reasoning_state(agent)
            return callback(fail_open_text, *args, **kwargs)
        captured, visible = _capture_inline_reasoning(agent, raw_text)
        if captured:
            try:
                callbacks.on_reasoning_delta(agent, captured, source="inline_think")
            except Exception:
                logger.debug("hermes-progress-tail inline think capture failed", exc_info=True)
                _reset_inline_reasoning_state(agent)
                visible = fail_open_text
        return callback(visible, *args, **kwargs)

    wrapped_stream_delta._hermes_progress_tail_inline_think_wrapped = True
    return wrapped_stream_delta


def _agent_reasoning_enabled(agent: Any, callbacks: HookCallbacks | None = None) -> bool:
    resolved = callbacks if callbacks is not None else current_hook_callbacks()
    try:
        return bool(resolved.reasoning_enabled(agent))
    except Exception:
        logger.debug("hermes-progress-tail reasoning availability check failed", exc_info=True)
        return False


def _inline_reasoning_pending(agent: Any) -> str:
    state = getattr(agent, "_hermes_progress_tail_inline_think_state", None)
    if not isinstance(state, dict):
        return ""
    return str(state.get("pending") or "")


def _reset_inline_reasoning_state(agent: Any) -> None:
    with suppress(Exception):
        agent._hermes_progress_tail_inline_think_state = {
            "inside": False,
            "pending": "",
            "captured_chars": 0,
        }


def _capture_inline_reasoning(agent: Any, text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    state = getattr(agent, "_hermes_progress_tail_inline_think_state", None)
    if not isinstance(state, dict):
        state = {"inside": False, "pending": "", "captured_chars": 0}
        agent._hermes_progress_tail_inline_think_state = state
    combined = str(state.get("pending") or "") + text
    captured, visible, inside, pending = _split_inline_reasoning(
        combined, bool(state.get("inside"))
    )
    captured_chars = int(state.get("captured_chars") or 0) + len(captured)
    if inside and captured_chars > 8000:
        _reset_inline_reasoning_state(agent)
        return "", text
    state["inside"] = inside
    state["pending"] = pending
    state["captured_chars"] = captured_chars if inside else 0
    return captured, visible


def _split_inline_reasoning(text: str, inside: bool = False) -> tuple[str, str, bool, str]:
    import re

    tag_names = "thinking|reasoning|thought|analysis|REASONING_SCRATCHPAD|think"
    tag_re = re.compile(rf"</?(?:{tag_names})\b[^>]*>", re.IGNORECASE)
    captured: list[str] = []
    visible: list[str] = []
    pos = 0
    for match in tag_re.finditer(text):
        segment = text[pos : match.start()]
        if inside:
            captured.append(segment)
        else:
            visible.append(segment)
        tag = match.group(0)
        inside = not tag.startswith("</")
        pos = match.end()
    tail = text[pos:]
    incomplete = ""
    last_lt = tail.rfind("<")
    if last_lt != -1 and _looks_like_partial_reasoning_tag(tail[last_lt:]):
        incomplete = tail[last_lt:]
        tail = tail[:last_lt]
    if inside:
        captured.append(tail)
    else:
        visible.append(tail)
    return "".join(captured), "".join(visible), inside, incomplete


def _looks_like_partial_reasoning_tag(value: str) -> bool:
    lowered = value.lower().lstrip("<").lstrip("/")
    return any(
        tag.startswith(lowered)
        for tag in ("think", "thinking", "reasoning", "thought", "analysis", "reasoning_scratchpad")
    )


def uninstall_agent_monkeypatches(agent_cls: type | None = None) -> bool:
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
    emit_interim = originals.get("_emit_interim_assistant_message")
    if emit_interim is not None:
        agent_cls._emit_interim_assistant_message = emit_interim
    invoke_tool = originals.get("_invoke_tool")
    if invoke_tool is not None:
        agent_cls._invoke_tool = invoke_tool
    try:
        delattr(agent_cls, _PATCH_MARKER)
    except Exception:
        setattr(agent_cls, _PATCH_MARKER, False)
    return True
