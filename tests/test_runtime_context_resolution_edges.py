import asyncio
import sys
from datetime import datetime, timezone
from types import ModuleType
from types import SimpleNamespace as NS

import pytest

from hermes_progress_tail.runtime import context as rc
from hermes_progress_tail.settings.config import Settings


def source(**kw):
    values = dict(
        platform="telegram", chat_id="chat", thread_id="2", chat_type="dm", message_id="m1"
    )
    values.update(kw)
    return NS(**values)


def test_context_resolution_matrix():
    a, b = NS(session_id="s"), NS(session_id="s")
    renderer = NS(find_context=lambda *x: "exact", sessions={}, session_keys={})
    assert rc._context_for(renderer, "s", "k") == "exact"
    renderer.find_context = lambda *x: None
    renderer.sessions = {"a": a}
    assert rc._context_for(renderer, "s") is a
    renderer.sessions["b"] = b
    assert rc._context_for(renderer, "s") is None
    renderer.sessions = {"mapped": a}
    renderer.session_keys = {"key": "mapped"}
    assert rc._context_for(renderer, session_key="key") is a
    renderer.session_keys = {"key": "gone"}
    assert rc._context_for(renderer, session_key="key") is None
    assert rc._context_for(renderer) is None


def test_store_and_session_key(monkeypatch):
    store = NS(get_or_create_session=lambda s: (_ for _ in ()).throw(RuntimeError()))
    assert rc._get_session_entry(store, object()) is None
    assert rc._session_key(NS(session_key="direct"), object(), object()) == "direct"
    gateway = ModuleType("gateway")
    session = ModuleType("gateway.session")
    seen = []
    session.build_session_key = lambda src, **kw: seen.append(kw) or "built"
    gateway.session = session
    monkeypatch.setitem(sys.modules, "gateway", gateway)
    monkeypatch.setitem(sys.modules, "gateway.session", session)
    config = NS(group_sessions_per_user=False, thread_sessions_per_user=True)
    assert rc._session_key(NS(session_key=""), object(), NS(config=config)) == "built"
    assert seen == [{"group_sessions_per_user": False, "thread_sessions_per_user": True}]
    session.build_session_key = lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    assert rc._session_key(NS(session_key=""), object(), NS(config=config)) == ""


def test_telegram_general_recovery_and_override():
    assert rc._telegram_general_topic_ids(NS(_TELEGRAM_GENERAL_TOPIC_IDS=1)) == {"", "1"}
    original = source()
    assert rc._source_with_thread_id(original, "2") is original
    assert rc._source_with_thread_id(original, "3").thread_id == "3"
    assert rc._topic_recovered_source(NS(), source(platform="discord")).thread_id == "2"
    assert rc._topic_recovered_source(NS(), original) is original
    for recover in [lambda s: "", lambda s: (_ for _ in ()).throw(RuntimeError())]:
        assert (
            rc._topic_recovered_source(NS(_recover_telegram_topic_thread_id=recover), original)
            is original
        )
    recovered = rc._topic_recovered_source(
        NS(_recover_telegram_topic_thread_id=lambda s: "9"), original
    )
    assert recovered.thread_id == "9" and recovered.chat_id == "chat"


@pytest.mark.parametrize(
    "value,expected", [(None, 0), (3, 3), ("", 0), ("2.5", 2.5), ("bad", 0), (object(), 0)]
)
def test_timestamp_values(value, expected):
    assert rc._timestamp_seconds(value) == expected


def test_timestamp_datetime_and_iso():
    dt = datetime(2020, 1, 1, tzinfo=timezone.utc)
    assert rc._timestamp_seconds(dt) == dt.timestamp()
    assert rc._timestamp_seconds("2020-01-01T00:00:00Z") == dt.timestamp()


def test_topic_binding_matrix():
    assert rc._telegram_topic_binding(NS(), source(platform="discord")) is None
    assert rc._telegram_topic_binding(NS(), source(thread_id="1")) is None
    assert rc._telegram_topic_binding(NS(), source()) is None
    bad = NS(get_telegram_topic_binding=lambda **k: (_ for _ in ()).throw(RuntimeError()))
    assert rc._telegram_topic_binding(NS(_session_db=bad), source()) is None
    db = NS(get_telegram_topic_binding=lambda **k: None)
    assert rc._telegram_topic_binding(NS(_session_db=db), source()) is None
    db.get_telegram_topic_binding = lambda **k: {"session_id": "s", "updated_at": 1}
    assert rc._bound_telegram_topic_session_id(NS(_session_db=db), source()) == "s"
    db.get_telegram_topic_binding = lambda **k: NS(session_id="o", updated_at=2)
    assert rc._telegram_topic_binding(NS(_session_db=db), source()) == {
        "session_id": "o",
        "updated_at": 2,
    }


@pytest.mark.parametrize(
    "binding,entry,expected",
    [
        (None, NS(session_id="s"), False),
        ({"session_id": ""}, NS(session_id="s"), False),
        ({"session_id": "s"}, NS(session_id="s"), False),
        ({"session_id": "a", "updated_at": 1}, NS(session_id="b", updated_at=2), True),
        ({"session_id": "a", "updated_at": 3}, NS(session_id="b", updated_at=2), False),
    ],
)
def test_binding_staleness(binding, entry, expected):
    assert rc._binding_is_stale_for_entry(binding, entry) is expected


def renderer():
    return NS(settings=Settings(), registered=[], register_context=lambda ctx: None)


def test_register_context_exception_visible():
    r = renderer()
    r.register_context = lambda ctx: (_ for _ in ()).throw(RuntimeError("visible"))
    with pytest.raises(RuntimeError, match="visible"):
        rc._register_context(
            renderer=r, source=source(), adapter=object(), session_id="s", session_key="k"
        )


@pytest.mark.parametrize(
    "event_source,platform,enabled,session,adapter,called",
    [
        (None, "telegram", True, "s", True, False),
        (source(platform=""), "", True, "s", True, False),
        (source(), "telegram", False, "s", True, False),
        (source(), "telegram", True, "", True, False),
        (source(), "telegram", True, "s", False, False),
        (source(), "telegram", True, "s", True, True),
    ],
)
def test_pre_dispatch_guards(
    monkeypatch, event_source, platform, enabled, session, adapter, called
):
    from hermes_progress_tail.runtime import plugin

    r = renderer()
    monkeypatch.setattr(plugin, "_get_renderer", lambda: r)
    monkeypatch.setattr(
        rc, "resolve_platform_settings", lambda *a: NS(enabled=enabled, strategy="auto")
    )
    monkeypatch.setattr(
        rc, "_pre_gateway_session_context", lambda g, st, s: (s, NS(session_key="k"), session)
    )
    monkeypatch.setattr(rc, "_adapter_for", lambda *a: object() if adapter else None)
    calls = []
    monkeypatch.setattr(rc, "_register_context", lambda **kw: calls.append(kw))
    rc._on_pre_gateway_dispatch(NS(source=event_source), object(), object())
    assert bool(calls) is called


def _context_contract(ctx, src, adapter, sid, key, loop):
    settings = rc.resolve_platform_settings(Settings(), "telegram")
    assert (
        ctx.session_id,
        ctx.session_key,
        ctx.platform,
        ctx.chat_id,
        ctx.thread_id,
        ctx.chat_type,
        ctx.source_message_id,
        ctx.adapter,
        ctx.loop,
    ) == (sid, key, "telegram", "chat", "2", "dm", "m1", adapter, loop)
    assert (
        ctx.strategy,
        ctx.lines,
        ctx.preview_length,
        ctx.edit_interval,
        ctx.tools_enabled,
        ctx.assistant_enabled,
        ctx.reasoning_enabled,
        ctx.delegates_enabled,
        ctx.background_jobs_enabled,
        ctx.timestamp,
        ctx.timestamp_format,
        ctx.agent_label,
    ) == (
        settings.strategy,
        settings.lines,
        settings.preview_length,
        settings.edit_interval,
        settings.tools_enabled,
        settings.assistant_enabled,
        settings.reasoning_enabled,
        settings.delegates_enabled,
        settings.background_jobs_enabled,
        settings.timestamp,
        settings.timestamp_format,
        Settings().renderer.agent_label,
    )


def test_register_context_complete_contract_both_loop_modes():
    r, src, adapter = renderer(), source(), object()
    r.register_context = r.registered.append
    rc._register_context(renderer=r, source=src, adapter=adapter, session_id="s", session_key="k")
    _context_contract(r.registered[-1], src, adapter, "s", "k", None)

    async def run():
        rc._register_context(
            renderer=r, source=src, adapter=adapter, session_id="s2", session_key="k2"
        )
        _context_contract(r.registered[-1], src, adapter, "s2", "k2", asyncio.get_running_loop())

    asyncio.run(run())


@pytest.mark.parametrize(
    "platform,enabled,strategy,store,session",
    [
        ("", True, "auto", True, "s"),
        ("telegram", False, "auto", True, "s"),
        ("telegram", True, "off", True, "s"),
        ("telegram", True, "auto", False, "s"),
        ("telegram", True, "auto", True, ""),
    ],
)
def test_adapter_registration_complete_guards(
    monkeypatch, platform, enabled, strategy, store, session
):
    from hermes_progress_tail.runtime import plugin

    r = renderer()
    monkeypatch.setattr(plugin, "_get_renderer", lambda: r)
    monkeypatch.setattr(
        rc, "resolve_platform_settings", lambda *a: NS(enabled=enabled, strategy=strategy)
    )
    gateway = object()
    adapter = NS(
        _session_store=object() if store else None,
        _hermes_progress_tail_gateway=gateway,
        gateway=object(),
    )
    monkeypatch.setattr(
        rc, "_pre_gateway_session_context", lambda g, st, s: (s, NS(session_key="k"), session)
    )
    calls = []
    monkeypatch.setattr(rc, "_register_context", lambda **kw: calls.append(kw))
    rc.register_context_from_adapter_event(adapter, NS(source=source(platform=platform)))
    assert calls == []


def test_adapter_gateway_precedence_and_full_registration(monkeypatch):
    from hermes_progress_tail.runtime import plugin

    r, preferred, fallback, adapter = renderer(), object(), object(), NS(_session_store=object())
    adapter._hermes_progress_tail_gateway, adapter.gateway = preferred, fallback
    monkeypatch.setattr(plugin, "_get_renderer", lambda: r)
    monkeypatch.setattr(
        rc, "resolve_platform_settings", lambda *a: NS(enabled=True, strategy="auto")
    )
    seen, calls = [], []
    monkeypatch.setattr(
        rc,
        "_pre_gateway_session_context",
        lambda g, st, s: seen.append(g) or (s, NS(session_key="k"), "s"),
    )
    monkeypatch.setattr(rc, "_register_context", lambda **kw: calls.append(kw))
    src = source()
    rc.register_context_from_adapter_event(adapter, NS(source=src))
    assert seen == [preferred]
    assert calls == [
        {
            "renderer": r,
            "source": src,
            "adapter": adapter,
            "session_id": "s",
            "session_key": "k",
            "origin": "adapter_internal",
        }
    ]
