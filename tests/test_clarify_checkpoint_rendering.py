from collections import deque

from hermes_progress_tail.models.decision import DecisionRecord, DecisionState
from hermes_progress_tail.models.state import SessionContext
from hermes_progress_tail.rendering.clarify_checkpoint import (
    _MAX_RECORDS,
    _SOURCE_BUDGET,
    canonicalize_clarify_args,
    compose_checkpoint,
    compose_resolution,
)


def context(*, label="Ada", records=()):
    ctx = SessionContext("s", "k", "discord", "chat", None, None, None, agent_label=label)
    ctx.decision = DecisionState(records=deque(records, maxlen=24))
    return ctx


def record(kind, text, identity, priority, created_at):
    return DecisionRecord(kind, text, identity, priority, created_at)


def test_checkpoint_includes_configured_label_question_and_every_choice_without_archive():
    text = compose_checkpoint(
        context(), {"question": "Choose scope?", "choices": ["default", "all"]}, 2
    )
    assert text == (
        "Ada needs your input\n\n"
        "Checkpoint #2 · this progress message is now read-only\n\n"
        "FROZEN · waiting for your answer\n\n"
        "Decision\nChoose scope?\n\n"
        "Choices\n• 1. default\n• 2. all"
    )


def test_checkpoint_groups_meaningful_records_without_event_metadata():
    ctx = context(
        records=(
            record("assistant", "Found a safe default", "a", 10, 1),
            record("plan", "→ select a restart scope", "p", 20, 2),
            record("tool", "✓ pytest: 10 passed", "t", 30, 3),
        )
    )
    text = compose_checkpoint(ctx, {"question": "Proceed?", "choices": []}, 1)
    assert "Context so far\n• Found a safe default" in text
    assert "Plan\n• → select a restart scope" in text
    assert "Relevant evidence\n• ✓ pytest: 10 passed" in text
    assert "created_at" not in text and "identity" not in text


def test_checkpoint_reserves_mandatory_previews_before_dropping_archive_and_caps_at_source_budget():
    huge = "question " * 4000
    choices = ["choice " * 2000 for _ in range(4)]
    records = tuple(record("tool", "evidence " * 400, str(i), 50 - i, i) for i in range(60))
    text = compose_checkpoint(context(records=records), {"question": huge, "choices": choices}, 4)
    assert len(text) <= _SOURCE_BUDGET
    assert "…" in text
    assert all(f"• {index + 1}." in text for index in range(4))


def test_record_section_overhead_stays_inside_frozen_and_resolved_source_budgets():
    records = tuple(record("tool", "word " * 72, str(i), 30 - i, i) for i in range(12))
    frozen = compose_checkpoint(context(records=records), {"question": "q", "choices": []}, 1)
    assert len(frozen) <= _SOURCE_BUDGET
    assert len(compose_resolution(frozen, "resolved", "answer " * 40)) <= _SOURCE_BUDGET


def test_checkpoint_selects_at_most_max_records_and_redacts_at_final_boundary():
    count = _MAX_RECORDS + 4
    records = tuple(
        record("tool", f"result {index}", str(index), 1, index) for index in range(count)
    )
    text = compose_checkpoint(
        context(records=records), {"question": "token sk-abcdefghijklmno", "choices": []}, 1
    )
    assert text.count("• result") <= _MAX_RECORDS
    assert "sk-abcdefghijklmno" not in text
    assert "[redacted_token]" in text


def test_checkpoint_preserves_safe_markdown_conventions():
    text = compose_checkpoint(
        context(records=(record("assistant", "Use `snake_case` and **bold**", "a", 1, 1),)),
        {"question": "Keep **this**?", "choices": []},
        1,
    )
    assert "`snake_case`" in text and "**bold**" in text and "**this**" in text


def test_resolution_reserves_terminal_budget_redacts_and_never_invents_answer():
    frozen = compose_checkpoint(context(), {"question": "Q?", "choices": []}, 1)
    resolved = compose_resolution(frozen, "resolved", "sk-abcdefghijklmno " + "answer " * 1000)
    assert len(resolved) <= _SOURCE_BUDGET
    assert "RESOLVED · [redacted_token]" in resolved
    assert "…" in resolved
    for status, expected in (
        ("timed_out", "TIMED OUT · no answer received"),
        ("prompt_failed", "PROMPT FAILED · clarify prompt was not delivered"),
        ("interrupted", "INTERRUPTED · task stopped while waiting"),
    ):
        assert compose_resolution(frozen, status, "invented").endswith(expected)


def test_pure_resolution_caps_oversized_frozen_input_after_final_redaction_and_keeps_terminal():
    frozen = "FROZEN · waiting for your answer\n\n" + ("source sk-abcdefghijklmno " * 4000)
    for status, terminal in (
        ("resolved", "RESOLVED · answer"),
        ("timed_out", "TIMED OUT · no answer received"),
    ):
        resolved = compose_resolution(frozen, status, "answer")
        assert len(resolved) <= _SOURCE_BUDGET
        assert resolved.endswith(terminal)
        assert "sk-abcdefghijklmno" not in resolved
        assert "…" in resolved


def test_canonicalization_matches_native_choice_shapes_and_invalid_input_fails_open():
    normalized = canonicalize_clarify_args(
        {
            "question": "  Which?  ",
            "choices": [
                {"label": " A ", "description": "details"},
                ["B", 3],
                {"title": "C"},
                object(),
                "D",
                "E",
            ],
        }
    )
    assert normalized.question == "Which?"
    assert normalized.choices == ("A", "B 3", "C", "D")
    assert canonicalize_clarify_args({"question": "  ", "choices": []}) is None
    assert canonicalize_clarify_args({"question": 3, "choices": []}) is None
    assert canonicalize_clarify_args({"question": "q", "choices": "no"}) is None
    assert canonicalize_clarify_args({"question": "q", "choices": []}).choices == ()
    assert canonicalize_clarify_args({"question": "q"}).choices == ()


def test_invalid_args_produce_no_composition():
    assert compose_checkpoint(context(), {"question": None, "choices": []}, 1) is None
