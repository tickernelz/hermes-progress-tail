from collections import deque
from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from hermes_progress_tail.models.decision import (
    ClarifyCheckpoint,
    DecisionKind,
    DecisionRecord,
    DecisionState,
    FreezeReservation,
    decision_is_waiting,
)
from hermes_progress_tail.models.events import DecisionEvent, ProgressEvent, ToolEvent
from hermes_progress_tail.models.state import SessionContext
from hermes_progress_tail.rendering.decision_archive import DecisionArchive


def record(identity, created_at, *, text=None, priority=10, kind="tool"):
    return DecisionRecord(kind, text or identity, identity, priority, created_at)


def context(**kwargs):
    return SessionContext("s", "k", "discord", "c", None, None, None, **kwargs)


def checkpoint(message_id, *, status="frozen"):
    return ClarifyCheckpoint(1, message_id, "question", (), status, True, "content")


def test_session_contexts_own_independent_decision_state_mutables():
    first, second = context(), context()
    first.decision.records.append(record("first", 1.0))
    first.decision.pending_freeze = FreezeReservation(1, "pending", 1.0, None)
    first.decision.protected_message_ids.add("pending")
    assert first.decision is not second.decision
    assert first.decision.records is not second.decision.records
    assert first.decision.pending_freeze is not second.decision.pending_freeze
    assert first.decision.protected_message_ids is not second.decision.protected_message_ids
    assert not second.decision.records
    assert second.decision.pending_freeze is None
    assert not second.decision.protected_message_ids


def test_decision_record_is_immutable():
    item = record("immutable", 1.0)
    with pytest.raises(FrozenInstanceError):
        item.text = "changed"


def test_decision_event_carries_a_record_without_changing_tool_event_construction():
    item = record("event", 1.0)
    event = DecisionEvent("s", "k", "discord", item)
    tool = ToolEvent("s", "k", "discord", "tool line")
    assert event.kind == "decision" and event.record is item
    assert tool.kind == "tool" and tool.line == "tool line"


def test_progress_event_transport_includes_decision_events():
    assert DecisionEvent in get_args(ProgressEvent)


def test_archive_redacts_compacts_and_bounds_ingested_records():
    archive, state = DecisionArchive(), DecisionState()
    path = "src/important_module.py"
    archive.upsert(
        state,
        record(
            "secret",
            1.0,
            text="OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz " + ("detail " * 80) + path,
        ),
    )
    stored = state.records[0]
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in stored.text
    assert "[redacted_env]" in stored.text
    assert path in stored.text
    assert len(stored.text) <= 360
    assert "  " not in stored.text


def test_archive_preserves_a_path_even_when_it_is_not_in_the_final_tail():
    archive, state = DecisionArchive(), DecisionState()
    path = "src/decision/checkpoint.py"
    archive.upsert(state, record("path", 1.0, text=("prefix " * 40) + path + (" suffix" * 80)))
    assert path in state.records[0].text
    assert len(state.records[0].text) <= 360


def test_archive_preserves_identifier_outside_retained_head_and_tail():
    archive, state = DecisionArchive(), DecisionState()
    identifier = "tool_call_id=keep-this-identifier"
    archive.upsert(
        state,
        record("identifier", 1.0, text=("prefix " * 60) + identifier + (" suffix" * 80)),
    )
    assert identifier in state.records[0].text
    assert len(state.records[0].text) <= 360


def test_archive_bounding_overlong_preserved_path_never_exceeds_record_cap():
    archive, state = DecisionArchive(), DecisionState()
    path = "src/" + ("verylongcomponent" * 30) + ".py"
    archive.upsert(state, record("overlong-path", 1.0, text=("prefix " * 40) + path))
    assert len(state.records[0].text) <= 360


def test_upsert_replaces_identity_without_growth_and_reorders_by_replacement_time():
    archive, state = DecisionArchive(), DecisionState()
    archive.upsert(state, record("first", 1.0))
    archive.upsert(state, record("second", 2.0))
    archive.upsert(state, record("first", 3.0, text="replacement"))
    assert len(state.records) == 2
    assert tuple(item.identity for item in state.records) == ("second", "first")
    assert state.records[-1].text == "replacement"


def test_distinct_archive_records_stay_chronological_internally():
    archive, state = DecisionArchive(), DecisionState()
    archive.upsert(state, record("late", 3.0))
    archive.upsert(state, record("early", 1.0))
    archive.upsert(state, record("middle", 2.0))
    assert tuple(item.identity for item in state.records) == ("early", "middle", "late")


def test_archive_capacity_is_exactly_twenty_four_records():
    archive, state = DecisionArchive(), DecisionState()
    for number in range(25):
        archive.upsert(state, record(str(number), float(number)))
    assert isinstance(state.records, deque)
    assert state.records.maxlen == 24
    assert tuple(item.identity for item in state.records) == tuple(
        str(number) for number in range(1, 25)
    )


def test_selection_prefers_priority_then_recency_and_returns_chronological_whole_records():
    archive, state = DecisionArchive(), DecisionState()
    for item in (
        record("old-high", 1.0, priority=20),
        record("old-low", 2.0, priority=1),
        record("new-high", 3.0, priority=20),
        record("new-low", 4.0, priority=1),
    ):
        archive.upsert(state, item)
    selected = archive.select(state, max_records=3, max_chars=100)
    assert tuple(item.identity for item in selected) == ("old-high", "new-high", "new-low")
    assert len(selected) <= 12


def test_selection_never_crosses_budget_or_truncates_a_record_to_fit():
    archive, state = DecisionArchive(), DecisionState()
    archive.upsert(state, record("high-too-large", 3.0, text="x" * 20, priority=30))
    archive.upsert(state, record("middle-fits", 2.0, text="y" * 9, priority=20))
    archive.upsert(state, record("old-fits", 1.0, text="z" * 9, priority=10))
    selected = archive.select(state, max_records=12, max_chars=18)
    assert tuple(item.identity for item in selected) == ("old-fits", "middle-fits")
    assert sum(len(item.text) for item in selected) <= 18
    assert all(item.text in {"y" * 9, "z" * 9} for item in selected)


def test_assistant_cumulative_updates_replace_but_distinct_commentary_appends():
    archive, state = DecisionArchive(), DecisionState()
    archive.observe_assistant(state, "Checking tests", 1.0)
    archive.observe_assistant(state, "Checking tests and formatting", 2.0)
    archive.observe_assistant(state, "Tests passed", 3.0)
    assert tuple(item.text for item in state.records) == (
        "Checking tests and formatting",
        "Tests passed",
    )
    assert len({item.identity for item in state.records}) == 2


def test_assistant_cumulative_update_replaces_a_bounded_prior_record():
    archive, state = DecisionArchive(), DecisionState()
    prior = "Checking " + ("long-detail " * 40)
    archive.observe_assistant(state, prior, 1.0)
    archive.observe_assistant(state, prior + "complete", 2.0)
    assert len(state.records) == 1
    assert state.records[0].text.endswith("complete")


def test_decision_contract_excludes_reasoning_and_archive_has_no_reasoning_observer():
    assert "reasoning" not in get_args(DecisionKind)
    assert not hasattr(DecisionArchive(), "observe_reasoning")


def test_clear_records_starts_a_new_segment_without_touching_checkpoint_ownership():
    state = DecisionState(
        records=deque((record("kept-only-until-freeze", 1.0),), maxlen=24),
        active_checkpoint=checkpoint("active"),
        pending_freeze=FreezeReservation(1, "pending", 1.0, None),
        protected_message_ids={"active", "pending"},
        cleanup_epoch=4,
        sequence=9,
    )
    state.clear_records_for_new_segment()
    assert not state.records
    assert state.active_checkpoint is not None
    assert state.pending_freeze is not None
    assert state.protected_message_ids == {"active", "pending"}
    assert (state.cleanup_epoch, state.sequence) == (4, 9)


def test_reset_turn_clears_decision_data_without_rewinding_cleanup_epoch():
    state = DecisionState(
        records=deque((record("old", 1.0),), maxlen=24),
        active_checkpoint=checkpoint("active"),
        pending_freeze=FreezeReservation(1, "pending", 1.0, None),
        protected_message_ids={"active", "pending"},
        cleanup_epoch=8,
        sequence=9,
    )
    state.reset_turn()
    assert not state.records
    assert state.pending_freeze is None and state.active_checkpoint is None
    assert not state.protected_message_ids
    assert (state.cleanup_epoch, state.sequence) == (8, 0)


def test_protected_ids_are_reconciled_from_only_reachable_nonempty_checkpoint_ids():
    state = DecisionState(
        pending_freeze=FreezeReservation(1, "pending", 1.0, None),
        active_checkpoint=checkpoint("active"),
        protected_message_ids={"stale", ""},
    )
    state.reconcile_protected_message_ids()
    assert state.protected_message_ids == {"pending", "active"}
    state.pending_freeze = None
    state.active_checkpoint = checkpoint("")
    state.reconcile_protected_message_ids()
    assert state.protected_message_ids == set()


def test_repeated_checkpoint_replacement_and_reset_do_not_grow_protected_ids():
    state = DecisionState()
    for index in range(10):
        state.pending_freeze = FreezeReservation(index, f"pending-{index}", 1.0, None)
        state.active_checkpoint = checkpoint(f"active-{index}")
        state.reconcile_protected_message_ids()
        assert state.protected_message_ids == {f"pending-{index}", f"active-{index}"}
        state.pending_freeze = None
        state.reconcile_protected_message_ids()
        assert state.protected_message_ids == {f"active-{index}"}
        state.reset_turn()
        assert not state.protected_message_ids


def test_decision_is_waiting_is_the_pure_pending_or_frozen_predicate():
    state = DecisionState()
    assert not decision_is_waiting(state)
    state.pending_freeze = FreezeReservation(1, "pending", 1.0, None)
    assert decision_is_waiting(state)
    state.pending_freeze = None
    state.active_checkpoint = checkpoint("active")
    assert decision_is_waiting(state)
    state.active_checkpoint.status = "resolved"
    assert not decision_is_waiting(state)
