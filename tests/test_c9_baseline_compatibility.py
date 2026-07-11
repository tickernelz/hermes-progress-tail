from hermes_progress_tail.models.state import SessionContext
from tests.test_delivery_adapter_edges import (
    test_auto_delete_stale_generation_and_message_fences as _auto_delete_fences,
)
from tests.test_delivery_adapter_edges import (
    test_delayed_flush_guards_success_and_cancellation as _delayed_flush_fences,
)
from tests.test_renderer_delivery import (
    test_snapshot_strategy_does_not_spam_until_threshold as _snapshot_thresholds,
)
from tests.test_renderer_delivery_backoff import (
    test_initial_send_flood_control_uses_backoff_without_disabling_context as _initial_backoff,
)
from tests.test_renderer_part5 import (
    test_renderer_compact_density_and_debug_downgrade_visibility as _debug_downgrade,
)
from tests.test_reviewer_blockers import (
    test_finalize_resets_turn_state_before_next_turn as _finalize_reset,
)
from tests.test_session_registry import (
    test_architecture_purge_cancels_flush_and_stale_purge_obeys_platform as _purge_fences,
)
from tests.test_session_registry import (
    test_architecture_registration_preserves_identity_and_generation as _registry_identity,
)
from tests.test_session_registry import (
    test_registration_cancels_pending_flush_and_clears_incoming_backoff as _pending_flush,
)
from tests.test_sticky_footer import (
    test_footer_renders_per_progress_compaction_count_and_resets_on_new_context as _compaction_footer,
)


def make_context(**kwargs):
    return SessionContext("s", "k", "discord", "c", None, None, None, **kwargs)


def test_legacy_delivery_diagnostics_values_and_task_identity():
    delayed = object()
    delete = object()
    ctx = make_context(
        message_id="m",
        can_edit=False,
        disabled=True,
        progress_state="finalized",
        finalized_at=1.0,
        last_render_at=2.0,
        edit_state="recovering",
        edit_backoff_until=3.0,
        edit_failure_count=4,
        edit_recovery_sends=5,
        delayed_flush_task=delayed,
        delete_task=delete,
        fallback_send_count=6,
        snapshots_sent=7,
        last_event_at=8.0,
        new_events_since_snapshot=9,
        total_events=10,
        last_error="error",
        downgrade_reason="reason",
        downgrade_at=11.0,
        compaction_count=12,
    )
    assert ctx.message_id == "m" and ctx.can_edit is False and ctx.disabled is True
    assert (ctx.progress_state, ctx.finalized_at, ctx.last_render_at) == ("finalized", 1.0, 2.0)
    assert (ctx.edit_state, ctx.edit_backoff_until, ctx.edit_failure_count) == (
        "recovering",
        3.0,
        4,
    )
    assert ctx.edit_recovery_sends == 5
    assert ctx.delayed_flush_task is delayed and ctx.delete_task is delete
    assert (ctx.fallback_send_count, ctx.snapshots_sent) == (6, 7)
    assert (ctx.last_event_at, ctx.new_events_since_snapshot, ctx.total_events) == (8.0, 9, 10)
    assert (ctx.last_error, ctx.downgrade_reason, ctx.downgrade_at) == (
        "error",
        "reason",
        11.0,
    )
    assert ctx.compaction_count == 12


def test_legacy_defaults_remain_compatible_and_independent():
    first, second = make_context(), make_context()
    assert first.message_id is None and first.can_edit is True and first.disabled is False
    assert first.progress_state == "active" and first.finalized_at == 0.0
    assert first.last_render_at == 0.0 and first.edit_state == "editable"
    assert first.edit_backoff_until == 0.0 and first.edit_failure_count == 0
    assert first.edit_recovery_sends == first.fallback_send_count == first.snapshots_sent == 0
    assert first.delayed_flush_task is first.delete_task is None
    assert first.new_events_since_snapshot == first.total_events == first.compaction_count == 0
    assert first.last_error == first.downgrade_reason == "" and first.downgrade_at == 0.0
    assert first.lock is not second.lock


def test_registry_lock_identity_pending_flush_and_progress_reuse():
    _registry_identity()
    _pending_flush()
    _purge_fences()


def test_delayed_flush_generation_and_cancellation_fences(monkeypatch):
    _delayed_flush_fences(monkeypatch)


def test_auto_delete_generation_message_and_identity_fences(monkeypatch):
    _auto_delete_fences(monkeypatch)


def test_compaction_footer_count_and_reset(monkeypatch):
    _compaction_footer(monkeypatch)


def test_backoff_snapshot_and_downgrade_diagnostics():
    _initial_backoff()
    _snapshot_thresholds()
    _debug_downgrade()


def test_finalization_resets_turn_delivery_and_diagnostics_state():
    _finalize_reset()
