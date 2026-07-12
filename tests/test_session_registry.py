import importlib
import inspect
import time
from types import SimpleNamespace

from hermes_progress_tail.models.state import SessionContext
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.settings.loading import load_settings
from tests.support.rendering import EditableAdapter, NoEditAdapter

SESSION_MODULE = "hermes_progress_tail.rendering.session"


class CancellationSpy:
    def __init__(self):
        self.deleted = []
        self.flushed = []

    def cancel_delete(self, ctx):
        self.deleted.append(ctx)

    def cancel_delayed_flush(self, ctx):
        self.flushed.append(ctx)


class PendingTask:
    @staticmethod
    def done():
        return False


def legacy_owner():
    spy = CancellationSpy()
    owner = SimpleNamespace(
        settings=load_settings({}),
        sessions={},
        session_keys={},
        _cancel_delete=spy.cancel_delete,
        _cancel_delayed_flush=spy.cancel_delayed_flush,
    )
    return owner, spy


def assert_legacy_helper_succeeds(call):
    error = None
    try:
        result = call()
    except Exception as exc:  # convert compatibility errors to protocol-valid assertion RED
        error = exc
        result = None
    assert error is None, f"legacy helper raised {type(error).__name__}: {error}"
    return result


def context(session_id="s1", session_key="k1", *, source=None, strategy="live_tail", lines=3):
    return SessionContext(
        session_id,
        session_key,
        "discord",
        "chat",
        None,
        EditableAdapter(),
        None,
        strategy,
        lines=lines,
        source_message_id=source,
    )


def registry():
    registry_type = getattr(importlib.import_module(SESSION_MODULE), "SessionRegistry", None)
    assert inspect.isclass(registry_type)
    spy = CancellationSpy()
    return registry_type(load_settings({}), spy.cancel_delete, spy.cancel_delayed_flush), spy


def test_characterization_source_message_fence():
    same = context(source="m1")
    assert ProgressRenderer._same_source_message(same, context(source="m1"))
    assert not ProgressRenderer._same_source_message(same, context(source="m2"))
    assert ProgressRenderer._same_source_message(same, context(source=None))


def test_characterization_registration_reuse_and_non_reuse():
    renderer = ProgressRenderer(load_settings({}))
    old = context(source="m1")
    old.tool_lines.append("old")
    old.todo_items = ("todo",)
    old.background_jobs["job"] = object()
    renderer.register_context(old)
    reused = context(source="m1")
    renderer.register_context(reused)
    assert reused.tool is old.tool
    assert reused.tool_lines is old.tool_lines
    assert reused.todo_items is old.todo_items and reused.todo_items == ("todo",)
    assert reused.background_jobs is old.background_jobs
    fenced = context(source="m2")
    renderer.register_context(fenced)
    assert fenced.tool is not reused.tool
    assert fenced.tool_lines is not reused.tool_lines
    assert fenced.todo_items == ()
    assert fenced.background_jobs is reused.background_jobs


def test_architecture_registry_exists_and_owns_state():
    item, _ = registry()
    assert {
        "register_context",
        "same_source_message",
        "find_context",
        "migrate_context",
        "purge",
    } <= type(item).__dict__.keys()
    assert isinstance(item.sessions, dict)
    assert isinstance(item.session_keys, dict)


def test_architecture_registration_preserves_identity_and_generation():
    item, spy = registry()
    old = context(source="m1")
    old.message_id = "progress"
    old.tool_lines.append("tool")
    old.background_jobs["job"] = object()
    old.total_events = 4
    old.snapshots_sent = 2
    old.delivery.message_started_at = 123.0
    old.delivery.progress_message_ids.extend(["old", "progress"])
    delivery_state = old.delivery
    history = old.delivery.progress_message_ids
    item.register_context(old)
    incoming = context(source="m1")
    item.register_context(incoming)
    assert incoming.generation == old.generation + 1
    assert incoming.lock is old.lock
    assert incoming.tool_lines is old.tool_lines
    assert incoming.background_jobs is old.background_jobs
    assert incoming.message_id == "progress"
    assert incoming.delivery is delivery_state
    assert incoming.delivery.progress_message_ids is history
    assert incoming.delivery.message_started_at == 123.0
    assert (incoming.total_events, incoming.snapshots_sent) == (4, 2)
    assert incoming.tool_lines.maxlen == 3
    assert spy.deleted == [old]


def test_architecture_non_reuse_resets_progress_but_reuses_background_identity():
    item, _ = registry()
    old = context(source="m1")
    old.progress_state = "finalized"
    old.tool_lines.append("old")
    old.background_jobs["job"] = object()
    old.total_events = 9
    old.last_error = "error"
    item.register_context(old)
    incoming = context(source="m1")
    item.register_context(incoming)
    assert incoming.tool_lines is not old.tool_lines
    assert incoming.background_jobs is old.background_jobs
    assert incoming.total_events == 0
    assert incoming.last_error == "error"
    assert incoming.generation == 1


def test_architecture_auto_strategy_and_find():
    item, _ = registry()
    editable = context(strategy="auto")
    item.register_context(editable)
    assert editable.strategy == "live_tail"
    assert item.find_context(session_key="k1") is editable


def test_architecture_migration_rekeys_and_cancels_delete():
    item, spy = registry()
    ctx = context()
    item.register_context(ctx)
    assert item.migrate_context("s1", "s2", "k2")
    assert ctx.session_id == "s2" and ctx.session_key == "k2"
    assert item.sessions == {"s2": ctx}
    assert item.session_keys == {"k2": "s2"}
    assert spy.deleted == [ctx]


def test_architecture_purge_cancels_flush_and_stale_purge_obeys_platform():
    item, spy = registry()
    stale = context("stale", "old")
    stale.last_event_at = time.monotonic() - 10_000
    fresh = context("fresh", "new")
    other = context("other", "other")
    other.platform = "telegram"
    other.last_event_at = stale.last_event_at
    for ctx in (stale, fresh, other):
        item.register_context(ctx)
    item.purge(platform="discord")
    assert set(item.sessions) == {"fresh", "other"}
    assert spy.flushed == [stale]
    item.purge(session_id="fresh")
    assert spy.flushed == [stale, fresh]


def test_architecture_facade_composes_registry_and_aliases_exact_dicts():
    settings = load_settings({})
    renderer = ProgressRenderer(settings)
    registry_type = getattr(importlib.import_module(SESSION_MODULE), "SessionRegistry", None)
    assert inspect.isclass(registry_type)
    assert isinstance(renderer.registry, registry_type)
    assert renderer.sessions is renderer.registry.sessions
    assert renderer.session_keys is renderer.registry.session_keys
    assert "sessions" not in renderer.__dict__
    assert "session_keys" not in renderer.__dict__
    assert renderer.registry.settings is settings
    replacement = load_settings({"progress_tail": {"renderer": {"stale_ttl_seconds": 12}}})
    renderer.replace_settings(replacement)
    assert renderer.registry.settings is replacement


def test_architecture_facade_forwards_are_explicit_and_injected_registry_is_preserved():
    expected = {
        "register_context",
        "_same_source_message",
        "find_context",
        "migrate_context",
        "purge",
        "sessions",
        "session_keys",
    }
    assert expected <= ProgressRenderer.__dict__.keys()
    injected, _ = registry()
    renderer = ProgressRenderer(load_settings({}), registry=injected)
    assert renderer.registry is injected
    ctx = context()
    renderer.register_context(ctx)
    assert injected.sessions["s1"] is ctx


def test_compatibility_register_context_accepts_pre_c3_owner_shape():
    module = importlib.import_module(SESSION_MODULE)
    owner, _ = legacy_owner()
    ctx = context()
    assert_legacy_helper_succeeds(lambda: module.register_context(owner, ctx))
    assert owner.sessions == {"s1": ctx}
    assert owner.session_keys == {"k1": "s1"}


def test_compatibility_find_context_accepts_pre_c3_owner_shape():
    module = importlib.import_module(SESSION_MODULE)
    owner, _ = legacy_owner()
    ctx = context()
    owner.sessions["s1"] = ctx
    owner.session_keys["k1"] = "s1"
    assert assert_legacy_helper_succeeds(lambda: module.find_context(owner, "s1")) is ctx
    assert (
        assert_legacy_helper_succeeds(lambda: module.find_context(owner, session_key="k1")) is ctx
    )


def test_compatibility_migrate_context_accepts_pre_c3_owner_shape():
    module = importlib.import_module(SESSION_MODULE)
    owner, spy = legacy_owner()
    ctx = context()
    owner.sessions["s1"] = ctx
    owner.session_keys["k1"] = "s1"
    assert assert_legacy_helper_succeeds(lambda: module.migrate_context(owner, "s1", "s2", "k2"))
    assert owner.sessions == {"s2": ctx}
    assert owner.session_keys == {"k2": "s2"}
    assert spy.deleted == [ctx]


def test_compatibility_purge_accepts_pre_c3_owner_shape():
    module = importlib.import_module(SESSION_MODULE)
    owner, spy = legacy_owner()
    ctx = context()
    owner.sessions["s1"] = ctx
    owner.session_keys["k1"] = "s1"
    assert_legacy_helper_succeeds(lambda: module.purge(owner, session_id="s1"))
    assert owner.sessions == {}
    assert owner.session_keys == {}
    assert spy.flushed == [ctx]


def test_registration_cancels_pending_flush_and_clears_incoming_backoff():
    item, spy = registry()
    old = context(source="m1")
    old.delayed_flush_task = PendingTask()
    item.register_context(old)
    incoming = context(source="m1")
    incoming.edit_backoff_until = 99.0
    item.register_context(incoming)
    assert spy.flushed == [old]
    assert incoming.edit_backoff_until == 0.0
    assert incoming.lock is old.lock


def test_auto_and_live_tail_downgrade_for_non_edit_adapter():
    item, _ = registry()
    auto = context("auto", "auto", strategy="auto")
    auto.adapter = NoEditAdapter()
    item.register_context(auto)
    live = context("live", "live", strategy="live_tail")
    live.adapter = NoEditAdapter()
    item.register_context(live)
    assert auto.strategy == "snapshot"
    assert live.strategy == "snapshot"


def test_changed_key_alias_and_stale_key_lookup_match_legacy_behavior():
    item, _ = registry()
    original = context("s1", "old")
    item.register_context(original)
    replacement = context("s1", "new")
    item.register_context(replacement)
    assert item.session_keys == {"old": "s1", "new": "s1"}
    assert item.find_context(session_key="old") is replacement
    item.sessions.pop("s1")
    assert item.find_context(session_key="old") is None
