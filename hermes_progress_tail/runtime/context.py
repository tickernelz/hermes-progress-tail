from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from ..gateway.compat import (
    platform_name,
    source_chat_id,
    source_chat_type,
    source_message_id,
    source_thread_id,
)
from ..models.state import RoutingState, SessionContext
from ..settings.config import resolve_platform_settings

if TYPE_CHECKING:
    from ..rendering.renderer import ProgressRenderer

logger = logging.getLogger(__name__)


class RuntimePort(Protocol):
    def get_renderer(self) -> Any: ...


_runtime_provider: RuntimePort | None = None


def configure_runtime_provider(provider: RuntimePort) -> None:
    global _runtime_provider
    _runtime_provider = provider


def _renderer_provider():
    return _runtime_provider.get_renderer()


def _operational_renderer():
    renderer = _renderer_provider()
    return renderer


def _context_for(renderer: ProgressRenderer, session_id: str = "", session_key: str = ""):
    ctx = renderer.find_context(session_id, session_key)
    if ctx is not None:
        return ctx
    if session_id:
        matches = [
            candidate
            for candidate in renderer.sessions.values()
            if candidate.session_id == session_id
        ]
        if len(matches) == 1:
            return matches[0]
    if session_key:
        mapped = renderer.session_keys.get(session_key)
        if mapped:
            return renderer.sessions.get(mapped)
    return None


def _get_session_entry(session_store: Any, source: Any):
    try:
        return session_store.get_or_create_session(source)
    except Exception as exc:
        logger.debug("hermes-progress-tail failed to resolve session entry: %s", exc)
        return None


class _SourceThreadOverride:
    def __init__(self, source: Any, thread_id: str):
        self._source = source
        self.thread_id = thread_id

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)


def _is_telegram_dm_source(source: Any) -> bool:
    return platform_name(source).lower() == "telegram" and source_chat_type(source) == "dm"


def _telegram_general_topic_ids(gateway: Any) -> set[str]:
    raw = getattr(gateway, "_TELEGRAM_GENERAL_TOPIC_IDS", frozenset({"", "1"}))
    try:
        return {str(item) for item in raw}
    except TypeError:
        return {"", "1"}


def _source_with_thread_id(source: Any, thread_id: str) -> Any:
    if str(source_thread_id(source) or "") == str(thread_id or ""):
        return source
    return _SourceThreadOverride(source, str(thread_id or ""))


def _topic_recovered_source(gateway: Any, source: Any) -> Any:
    if not _is_telegram_dm_source(source):
        return source
    recover = getattr(gateway, "_recover_telegram_topic_thread_id", None)
    if not callable(recover):
        return source
    try:
        recovered = recover(source)
    except Exception:
        logger.debug("hermes-progress-tail Telegram topic recovery lookup failed", exc_info=True)
        return source
    if not recovered:
        return source
    return _source_with_thread_id(source, str(recovered))


def _timestamp_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        return value.timestamp()
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def _telegram_topic_binding(gateway: Any, source: Any) -> dict[str, Any] | None:
    if not _is_telegram_dm_source(source):
        return None
    thread_id = str(source_thread_id(source) or "")
    if not thread_id or thread_id in _telegram_general_topic_ids(gateway):
        return None
    session_db = getattr(gateway, "_session_db", None)
    getter = getattr(session_db, "get_telegram_topic_binding", None)
    if not callable(getter):
        return None
    try:
        binding = getter(chat_id=source_chat_id(source), thread_id=thread_id)
    except Exception:
        logger.debug("hermes-progress-tail Telegram topic binding lookup failed", exc_info=True)
        return None
    if binding is None:
        return None
    if isinstance(binding, dict):
        return binding
    return {
        "session_id": getattr(binding, "session_id", ""),
        "updated_at": getattr(binding, "updated_at", 0),
    }


def _binding_session_id(binding: dict[str, Any] | None) -> str:
    if not binding:
        return ""
    return str(binding.get("session_id") or "")


def _binding_is_stale_for_entry(binding: dict[str, Any] | None, entry: Any) -> bool:
    bound_session_id = _binding_session_id(binding)
    entry_session_id = str(getattr(entry, "session_id", "") or "")
    if not bound_session_id or not entry_session_id or bound_session_id == entry_session_id:
        return False
    binding_updated = _timestamp_seconds((binding or {}).get("updated_at"))
    entry_updated = _timestamp_seconds(getattr(entry, "updated_at", None))
    return bool(binding_updated and entry_updated and binding_updated < entry_updated)


def _bound_telegram_topic_session_id(gateway: Any, source: Any) -> str:
    return _binding_session_id(_telegram_topic_binding(gateway, source))


def _pre_gateway_session_context(gateway: Any, session_store: Any, source: Any):
    effective_source = _topic_recovered_source(gateway, source)
    entry = _get_session_entry(session_store, effective_source)
    session_id = str(getattr(entry, "session_id", "") or "")
    binding = _telegram_topic_binding(gateway, effective_source)
    bound_session_id = _binding_session_id(binding)
    if bound_session_id and (not session_id or not _binding_is_stale_for_entry(binding, entry)):
        session_id = bound_session_id
    elif bound_session_id and session_id != bound_session_id:
        logger.debug(
            "hermes-progress-tail ignored stale Telegram topic binding: entry_session_id=%s bound_session_id=%s",
            session_id,
            bound_session_id,
        )
    return effective_source, entry, session_id


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


def _register_context(
    *,
    renderer: ProgressRenderer,
    source: Any,
    adapter: Any,
    session_id: str,
    session_key: str,
    origin: str = "gateway",
) -> None:
    platform = platform_name(source)
    settings = resolve_platform_settings(renderer.settings, platform)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    ctx = SessionContext(
        session_id=session_id,
        session_key=session_key,
        platform=platform,
        chat_id=source_chat_id(source),
        thread_id=source_thread_id(source),
        adapter=adapter,
        loop=loop,
        routing=RoutingState(
            strategy=settings.strategy,
            lines=settings.lines,
            preview_length=settings.preview_length,
            edit_interval=settings.edit_interval,
            tools_enabled=settings.tools_enabled,
            assistant_enabled=settings.assistant_enabled,
            reasoning_enabled=settings.reasoning_enabled,
            delegates_enabled=settings.delegates_enabled,
            background_jobs_enabled=settings.background_jobs_enabled,
            timestamp=settings.timestamp,
            timestamp_format=settings.timestamp_format,
            agent_label=renderer.settings.renderer.agent_label,
            chat_type=source_chat_type(source),
            source_message_id=source_message_id(source),
        ),
        owner_thread_id=0,
        owner_thread_name="",
    )
    renderer.register_context(ctx)
    logger.info(
        "hermes-progress-tail context registered: origin=%s platform=%s session_id=%s "
        "session_key_present=%s strategy=%s tools=%s assistant=%s reasoning=%s delegates=%s",
        origin,
        platform,
        session_id,
        bool(session_key),
        settings.strategy,
        settings.tools_enabled,
        settings.assistant_enabled,
        settings.reasoning_enabled,
        settings.delegates_enabled,
    )


def _on_pre_gateway_dispatch(event: Any, gateway: Any, session_store: Any, **_: Any):
    renderer = _operational_renderer()
    source = getattr(event, "source", None)
    if source is None:
        return None
    platform = platform_name(source)
    if not platform:
        return None
    settings = resolve_platform_settings(renderer.settings, platform)
    if not settings.enabled or settings.strategy == "off":
        return None
    source, entry, session_id = _pre_gateway_session_context(gateway, session_store, source)
    if not session_id:
        return None
    adapter = _adapter_for(gateway, source)
    if adapter is None:
        logger.debug("hermes-progress-tail adapter not found for platform %s", platform)
        return None
    if platform.lower() == "telegram":
        from ..hooks.telegram import install_telegram_format_monkeypatch

        install_telegram_format_monkeypatch(type(adapter))
    _register_context(
        renderer=renderer,
        source=source,
        adapter=adapter,
        session_id=session_id,
        session_key=_session_key(entry, source, gateway),
    )
    return None


def register_context_from_adapter_event(adapter: Any, event: Any) -> None:
    renderer = _operational_renderer()
    source = getattr(event, "source", None)
    if source is None:
        return
    platform = platform_name(source)
    if not platform:
        return
    settings = resolve_platform_settings(renderer.settings, platform)
    if not settings.enabled or settings.strategy == "off":
        return
    session_store = getattr(adapter, "_session_store", None)
    if session_store is None:
        return
    gateway = (
        getattr(adapter, "_hermes_progress_tail_gateway", None)
        or getattr(adapter, "gateway", None)
        or adapter
    )
    source, entry, session_id = _pre_gateway_session_context(gateway, session_store, source)
    if not session_id:
        return
    _register_context(
        renderer=renderer,
        source=source,
        adapter=adapter,
        session_id=session_id,
        session_key=_session_key(entry, source, gateway),
        origin="adapter_internal",
    )
    return
