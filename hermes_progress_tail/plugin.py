from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import yaml

from .compat import platform_name, source_chat_id, source_thread_id
from .config import load_settings, resolve_platform_settings
from .formatter import extract_todo_items, format_tool_line
from .monkeypatches import install_monkeypatches
from .renderer import ProgressRenderer
from .state import ReasoningEvent, SessionContext, ToolEvent

logger = logging.getLogger(__name__)
_renderer: ProgressRenderer | None = None
VERSION = "0.1.2"


def _load_runtime_config() -> dict[str, Any]:
    config = {}
    try:
        from hermes_constants import get_hermes_home

        config_path = Path(get_hermes_home()) / "config.yaml"
    except Exception:
        config_path = Path.home() / ".hermes" / "config.yaml"
    try:
        if config_path.exists():
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config = loaded
    except Exception as exc:
        logger.debug("hermes-progress-tail config load failed: %s", exc)
    return config


def _load_runtime_settings():
    return load_settings(_load_runtime_config())


def _builtin_reasoning_conflict(config: dict[str, Any]) -> bool:
    display = config.get("display")
    if not isinstance(display, dict) or display.get("show_reasoning") is not True:
        return False
    progress_tail = config.get("progress_tail")
    if isinstance(progress_tail, dict) and progress_tail.get("enabled") is False:
        return False
    reasoning = progress_tail.get("reasoning") if isinstance(progress_tail, dict) else None
    return not (isinstance(reasoning, dict) and reasoning.get("enabled") is False)


def _reasoning_conflict_warning() -> str:
    return (
        "warning: display.show_reasoning=true while progress_tail.reasoning.enabled=true; "
        "duplicate reasoning/final output may occur. Set display.show_reasoning=false."
    )


def _get_renderer() -> ProgressRenderer:
    global _renderer
    settings = _load_runtime_settings()
    if _renderer is None:
        _renderer = ProgressRenderer(settings)
    else:
        _renderer.settings = settings
    return _renderer


def _get_session_entry(session_store: Any, source: Any):
    try:
        return session_store.get_or_create_session(source)
    except Exception as exc:
        logger.debug("hermes-progress-tail failed to resolve session entry: %s", exc)
        return None


def _session_key(entry: Any, source: Any, gateway: Any) -> str:
    direct = getattr(entry, "session_key", "")
    if direct:
        return str(direct)
    try:
        from gateway.session import build_session_key

        cfg = getattr(gateway, "config", None)
        return build_session_key(
            source,
            group_sessions_per_user=getattr(cfg, "group_sessions_per_user", True),
            thread_sessions_per_user=getattr(cfg, "thread_sessions_per_user", False),
        )
    except Exception:
        return ""


def _adapter_for(gateway: Any, source: Any):
    adapters = getattr(gateway, "adapters", {}) or {}
    platform = getattr(source, "platform", None)
    return adapters.get(platform) or adapters.get(platform_name(source))


def _on_pre_gateway_dispatch(event: Any, gateway: Any, session_store: Any, **_: Any):
    renderer = _get_renderer()
    source = getattr(event, "source", None)
    if source is None:
        return None
    platform = platform_name(source)
    if not platform:
        return None
    settings = resolve_platform_settings(renderer.settings, platform)
    if not settings.enabled or settings.strategy == "off":
        return None
    entry = _get_session_entry(session_store, source)
    session_id = str(getattr(entry, "session_id", "") or "")
    if not session_id:
        return None
    adapter = _adapter_for(gateway, source)
    if adapter is None:
        logger.debug("hermes-progress-tail adapter not found for platform %s", platform)
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    ctx = SessionContext(
        session_id=session_id,
        session_key=_session_key(entry, source, gateway),
        platform=platform,
        chat_id=source_chat_id(source),
        thread_id=source_thread_id(source),
        adapter=adapter,
        loop=loop,
        strategy=settings.strategy,
        lines=settings.lines,
        preview_length=settings.preview_length,
        edit_interval=settings.edit_interval,
        tools_enabled=settings.tools_enabled,
        reasoning_enabled=settings.reasoning_enabled,
        timestamp=settings.timestamp,
        timestamp_format=settings.timestamp_format,
    )
    renderer.register_context(ctx)
    return None


def _schedule_render(ctx: SessionContext, event: ToolEvent | ReasoningEvent) -> None:
    renderer = _get_renderer()
    if ctx.loop is None:
        return
    try:
        future = asyncio.run_coroutine_threadsafe(renderer.handle_event(event), ctx.loop)

        def _consume_done(fut):
            try:
                fut.result()
            except Exception as exc:
                logger.debug("hermes-progress-tail render failed: %s", exc)

        future.add_done_callback(_consume_done)
    except Exception as exc:
        logger.debug("hermes-progress-tail schedule failed: %s", exc)


def _on_pre_tool_call(
    tool_name: str,
    args: dict | None = None,
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    preview: str | None = None,
    **_: Any,
):
    renderer = _get_renderer()
    ctx = renderer.find_context(session_id or task_id, task_id)
    if ctx is None or not ctx.tools_enabled:
        return None
    line = format_tool_line(
        tool_name,
        args or {},
        preview=preview,
        preview_length=ctx.preview_length,
        patch_detail=renderer.settings.patch.detail,
        patch_preview_chars=renderer.settings.patch.preview_chars,
        patch_max_files=renderer.settings.patch.max_files,
    )
    event = ToolEvent(
        session_id=ctx.session_id,
        session_key=ctx.session_key,
        platform=ctx.platform,
        line=line,
        tool_call_id=tool_call_id or "",
        tool_name=tool_name,
        todo_items=extract_todo_items(args or {}) if tool_name == "todo" else (),
    )
    _schedule_render(ctx, event)
    return None


def _on_post_tool_call(
    tool_name: str,
    args: dict | None = None,
    result: str = "",
    task_id: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: int | None = None,
    **_: Any,
):
    renderer = _get_renderer()
    ctx = renderer.find_context(session_id or task_id, task_id)
    if ctx is None or not ctx.tools_enabled or not renderer.settings.tools.show_completed:
        return None
    duration = f" {duration_ms / 1000:.1f}s" if isinstance(duration_ms, int) else ""
    line = f"✅ {tool_name}{duration}"
    _schedule_render(
        ctx,
        ToolEvent(
            ctx.session_id,
            ctx.session_key,
            ctx.platform,
            line,
            tool_call_id=tool_call_id or "",
            tool_name=tool_name,
        ),
    )
    return None


def on_reasoning_delta_from_agent(agent: Any, text: str) -> None:
    if not text:
        return
    renderer = _get_renderer()
    session_id = str(getattr(agent, "session_id", "") or "")
    session_key = str(getattr(agent, "gateway_session_key", "") or "")
    ctx = renderer.find_context(session_id, session_key)
    if ctx is None or not ctx.reasoning_enabled:
        return
    _schedule_render(ctx, ReasoningEvent(ctx.session_id, ctx.session_key, ctx.platform, text))


def _schedule_finalize(session_id: str = "", platform: str = "", *, purge: bool = False) -> None:
    renderer = _get_renderer()
    ctx = renderer.find_context(session_id)
    if ctx is None or ctx.loop is None:
        if purge:
            renderer.purge(session_id=session_id, platform=platform)
        return
    try:
        future = asyncio.run_coroutine_threadsafe(
            renderer.finalize(session_id=session_id, purge=purge), ctx.loop
        )

        def _consume_done(fut):
            try:
                fut.result()
            except Exception as exc:
                logger.debug("hermes-progress-tail finalize failed: %s", exc)

        future.add_done_callback(_consume_done)
    except Exception as exc:
        logger.debug("hermes-progress-tail finalize schedule failed: %s", exc)


def _on_post_llm_call(session_id: str = "", **_: Any):
    _schedule_finalize(session_id=session_id)
    return None


def _on_session_reset(session_id: str = "", platform: str = "", **_: Any):
    _get_renderer().purge(session_id=session_id, platform=platform)
    return None


def _on_session_finalize(session_id: str = "", platform: str = "", **_: Any):
    _schedule_finalize(session_id=session_id, platform=platform, purge=True)
    return None


def _command(raw_args: str = "") -> str:
    args = (raw_args or "").strip().lower()
    renderer = _get_renderer()
    if args in {"", "status"}:
        active = len(renderer.sessions)
        monkeypatch_active = False
        try:
            from run_agent import AIAgent

            monkeypatch_active = bool(getattr(AIAgent, "_hermes_progress_tail_patched", False))
        except Exception:
            monkeypatch_active = False
        settings = renderer.settings
        lines = [
            f"hermes-progress-tail {VERSION}",
            f"sessions={active}",
            f"tools={'enabled' if settings.tools.enabled else 'disabled'} lines={settings.tools.lines} timestamp={settings.tools.timestamp_format if settings.tools.timestamp else 'off'}",
            f"todo=sticky:{settings.todo.sticky} hide_tool_line:{settings.todo.hide_tool_line}",
            f"patch=detail:{settings.patch.detail} preview_chars:{settings.patch.preview_chars} max_files:{settings.patch.max_files}",
            f"reasoning={'enabled' if settings.reasoning.enabled else 'disabled'} max_lines={settings.reasoning.max_lines} max_chars={settings.reasoning.max_chars}",
            f"renderer=strategy:{settings.renderer.strategy} style:{settings.renderer.style} edit_interval:{settings.renderer.edit_interval}",
            f"monkeypatch={monkeypatch_active}",
        ]
        if _builtin_reasoning_conflict(_load_runtime_config()):
            lines.append(_reasoning_conflict_warning())
        return "\n".join(lines)
    if args == "test":
        return "hermes-progress-tail is loaded. Send a normal request with tool calls/reasoning to test live rendering."
    return "Usage: /progresstail status | test"


def register(ctx):
    if _builtin_reasoning_conflict(_load_runtime_config()):
        logger.warning(_reasoning_conflict_warning())
    install_monkeypatches()
    ctx.register_hook("pre_gateway_dispatch", _on_pre_gateway_dispatch)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("post_llm_call", _on_post_llm_call)
    ctx.register_hook("on_session_reset", _on_session_reset)
    ctx.register_hook("on_session_finalize", _on_session_finalize)
    ctx.register_command(
        "progresstail",
        _command,
        description="Show hermes-progress-tail plugin status",
        args_hint="status|test",
    )
