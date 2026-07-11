from collections import deque

from hermes_progress_tail.models.events import TodoItem
from hermes_progress_tail.models.state import SessionContext
from tests.test_event_reducer import (
    test_architecture_todo_sticky_hide_matrix,
    test_architecture_tool_fingerprint_replacement_failure_and_new_completes_active,
    test_architecture_tool_new_replace_terminal_and_idempotence,
)
from tests.test_renderer_delivery import (
    test_sticky_todo_survives_latest_tool_tail_and_resets_on_finalize,
    test_todo_tool_line_can_be_kept_when_configured,
)
from tests.test_reviewer_blockers import test_finalize_resets_turn_state_before_next_turn
from tests.test_session_registry import test_characterization_registration_reuse_and_non_reuse


def context(**kwargs):
    return SessionContext("s", "k", "discord", "c", None, None, None, **kwargs)


def test_legacy_tool_values_and_mutable_identity():
    lines = deque(["one"], maxlen=5)
    active = {"id": "one"}
    fingerprints = {"fp": "one"}
    completed = {"id:old"}
    todos = (TodoItem("next", "pending"),)
    ctx = context(
        tool_lines=lines,
        active_tool_lines=active,
        active_tool_fingerprints=fingerprints,
        tool_started_count=3,
        tool_completed_count=1,
        tool_failed_count=1,
        completed_tool_ids=completed,
        todo_items=todos,
        todo_updated_at=4.0,
    )
    assert ctx.tool_lines is lines
    assert ctx.active_tool_lines is active
    assert ctx.active_tool_fingerprints is fingerprints
    assert ctx.completed_tool_ids is completed
    assert ctx.todo_items is todos
    assert (ctx.tool_started_count, ctx.tool_completed_count, ctx.tool_failed_count) == (3, 1, 1)
    assert ctx.todo_updated_at == 4.0


def test_tool_defaults_alias_resize_and_owner_conflicts():
    first, second = context(), context()
    assert first.tool_lines is not second.tool_lines
    assert first.active_tool_lines is not second.active_tool_lines
    assert first.completed_tool_ids is not second.completed_tool_ids
    replacement = deque(["a", "b", "c"], maxlen=8)
    first.line_buffer = replacement
    assert first.line_buffer is replacement is first.tool_lines
    first.resize(2)
    assert list(first.tool_lines) == ["b", "c"]
    assert first.tool_lines.maxlen == first.lines == 2


def test_lifecycle_replacement_fingerprint_idempotence_and_active_completion():
    test_architecture_tool_new_replace_terminal_and_idempotence()
    test_architecture_tool_fingerprint_replacement_failure_and_new_completes_active()


def test_sticky_hide_todo_update_and_reset_contract():
    cases = (
        (False, False, False, False),
        (False, True, False, True),
        (True, False, True, False),
        (True, True, True, True),
    )
    for args in cases:
        test_architecture_todo_sticky_hide_matrix(*args)
    test_sticky_todo_survives_latest_tool_tail_and_resets_on_finalize()
    test_todo_tool_line_can_be_kept_when_configured()


def test_registry_reuse_fencing_and_finalization_contract():
    test_characterization_registration_reuse_and_non_reuse()
    test_finalize_resets_turn_state_before_next_turn()
