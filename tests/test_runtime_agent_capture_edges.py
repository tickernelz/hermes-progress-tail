from types import SimpleNamespace as NS

import pytest

from hermes_progress_tail.runtime import agent_events as ae


def harness(monkeypatch, *, ctx=True, suppress=False, scheduled=True):
    context = NS(
        session_id="sid",
        session_key="key",
        platform="discord",
        reasoning_enabled=True,
        assistant_enabled=True,
        delegates_enabled=True,
        compaction_count=0,
    )
    renderer = NS(settings=NS(assistant=NS(enabled=True)), migrate_context=lambda *a, **k: None)
    calls = []
    capture = {}
    plugin = NS(
        assistant_capture=capture,
        get_renderer=lambda: renderer,
    )
    monkeypatch.setattr(ae, "_runtime_provider", plugin)
    monkeypatch.setattr(ae, "_should_suppress_agent_progress", lambda agent: suppress)
    monkeypatch.setattr(ae, "_agent_session_id", lambda agent: "sid")
    monkeypatch.setattr(ae, "_agent_session_key", lambda agent: "key")
    monkeypatch.setattr(
        ae, "_context_for_non_background_thread", lambda *a: context if ctx else None
    )
    monkeypatch.setattr(ae, "_context_for", lambda *a: context if ctx else None)
    monkeypatch.setattr(
        ae, "_update_environment_from_agent", lambda *a: calls.append(("environment", a))
    )
    monkeypatch.setattr(
        ae, "_schedule_render", lambda *a, **k: calls.append(("render", a, k)) or scheduled
    )
    return plugin, renderer, context, calls


@pytest.mark.parametrize(
    "text,suppress,ctx,enabled",
    [
        ("", False, True, True),
        ("x", True, True, True),
        ("x", False, False, True),
        ("x", False, True, False),
    ],
)
def test_reasoning_guards(monkeypatch, text, suppress, ctx, enabled):
    _, _, context, calls = harness(monkeypatch, ctx=ctx, suppress=suppress)
    context.reasoning_enabled = enabled
    ae.on_reasoning_delta_from_agent(object(), text)
    assert calls == []


def test_reasoning_success_contract(monkeypatch):
    _, _, context, calls = harness(monkeypatch)
    ae.on_reasoning_delta_from_agent(object(), "thought", source="inline")
    event = calls[1][1][1]
    assert calls[0][0] == "environment"
    assert (event.text, event.source, event.session_id) == ("thought", "inline", context.session_id)


@pytest.mark.parametrize(
    "text,suppress,ctx,local,global_,expected",
    [
        (" ", False, True, True, True, False),
        ("x", True, True, True, True, False),
        ("x", False, False, True, True, False),
        ("x", False, True, False, True, False),
        ("x", False, True, True, False, False),
        ("preflight compression now", False, True, True, True, True),
    ],
)
def test_compression_status_edges(monkeypatch, text, suppress, ctx, local, global_, expected):
    _, renderer, context, calls = harness(monkeypatch, ctx=ctx, suppress=suppress)
    context.assistant_enabled = local
    renderer.settings.assistant.enabled = global_
    assert ae.on_compression_status_from_agent(object(), text) is expected
    if expected:
        assert calls[-1][1][1].text == "Preflight compression — preparing compact context"


def test_compression_status_schedule_failure_and_default(monkeypatch):
    _, _, _, calls = harness(monkeypatch, scheduled=False)
    assert ae.on_compression_status_from_agent(object(), "other") is False
    assert calls[-1][1][1].text.startswith("Compacting context")


@pytest.mark.parametrize("phase,text", [("started", "Compacting context"), ("failed", "failed")])
def test_lifecycle_phases(monkeypatch, phase, text):
    _, _, _, calls = harness(monkeypatch)
    assert ae.on_compression_lifecycle_from_agent(object(), phase) is True
    assert text in calls[-1][1][1].text
    assert calls[-1][2] == {"force": True}


def test_lifecycle_migration_completed_and_normalization(monkeypatch):
    _, renderer, context, calls = harness(monkeypatch)
    migrated = []
    renderer.migrate_context = lambda *a, **k: migrated.append((a, k))
    assert ae.on_compression_lifecycle_from_agent(
        object(),
        "completed",
        old_session_id="old",
        new_session_id="new",
        before_count="9",
        after_count="3",
        before_tokens="900",
        after_tokens="bad",
    )
    assert migrated == [(("old", "new"), {"session_key": "key"})]
    assert context.compaction_count == 1
    assert calls[-1][1][1].text == "Context compacted · 9 → 3 messages"
    assert (
        ae._compression_lifecycle_completed_text(
            {"before_tokens": 900, "after_tokens": 500, "after_tokens_kind": "rough"}
        )
        == "Context compaction checked · rough 900 → 500 tokens"
    )


@pytest.mark.parametrize(
    "phase,suppress,ctx",
    [("unknown", False, True), ("started", True, True), ("started", False, False)],
)
def test_lifecycle_guards(monkeypatch, phase, suppress, ctx):
    harness(monkeypatch, suppress=suppress, ctx=ctx)
    assert ae.on_compression_lifecycle_from_agent(object(), phase) is False


@pytest.mark.parametrize(
    "text,ctx,local,global_,scheduled,streamed,status,result",
    [
        (" ", True, True, True, True, False, "empty", False),
        ("secret sk-abcdefghijklmnop", False, True, True, True, False, "no_context", False),
        ("x", True, False, True, True, False, "disabled", False),
        ("x", True, True, False, True, False, "disabled", False),
        ("x", True, True, True, False, False, "schedule_failed", False),
        ("x", True, True, True, True, False, "scheduled", True),
        ("x", True, True, True, True, True, "scheduled", False),
    ],
)
def test_assistant_capture_edges(
    monkeypatch, text, ctx, local, global_, scheduled, streamed, status, result
):
    plugin, renderer, context, _ = harness(monkeypatch, ctx=ctx, scheduled=scheduled)
    context.assistant_enabled = local
    renderer.settings.assistant.enabled = global_
    assert ae.on_assistant_progress_from_agent(object(), text, already_streamed=streamed) is result
    capture = plugin.assistant_capture
    assert capture["status"] == status
    assert capture["session_id"] == ("" if status == "empty" else "sid")
    assert capture["session_key_present"] is (status not in {"empty", "background_review"})
    assert capture["already_streamed"] is streamed
    expected_preview = "secret [redacted_token]" if status == "no_context" else text.strip()
    assert capture["text_preview"] == expected_preview
    assert "sk-abcdefghijklmnop" not in capture["text_preview"]


def test_assistant_background_and_delegate_normalization(monkeypatch):
    plugin, _, _, calls = harness(monkeypatch, suppress=True)
    assert ae.on_assistant_progress_from_agent(object(), "x") is False
    assert plugin.assistant_capture["status"] == "background_review"
    assert calls == []
    _, _, _, calls = harness(monkeypatch)
    ae.on_delegate_progress_from_agent(
        object(), "done", args=[1], task_index="bad", task_count="2", duration_seconds="bad"
    )
    event = calls[-1][1][1]
    assert (
        event.subagent_id,
        event.task_index,
        event.task_count,
        event.args,
        event.duration_seconds,
    ) == ("task-bad", 0, 2, {}, 0.0)


@pytest.mark.parametrize(
    "ctx,enabled,suppress", [(False, True, False), (True, False, False), (True, True, True)]
)
def test_delegate_no_schedule_guards(monkeypatch, ctx, enabled, suppress):
    _, _, context, calls = harness(monkeypatch, ctx=ctx, suppress=suppress)
    context.delegates_enabled = enabled
    ae.on_delegate_progress_from_agent(object(), "done")
    assert calls == []


@pytest.mark.parametrize("explicit", [False, True])
def test_delegate_full_event_contract(monkeypatch, explicit):
    _, _, _, calls = harness(monkeypatch)
    values = (
        dict(
            subagent_id="worker",
            task_index="4",
            task_count="5",
            goal="goal",
            status="ok",
            model="m",
            tool_count="6",
            duration_seconds="1.5",
            summary="sum",
        )
        if explicit
        else dict(task_index="bad", task_count="bad", duration_seconds="bad")
    )
    ae.on_delegate_progress_from_agent(
        object(),
        "done",
        tool_name="tool" if explicit else None,
        preview="preview" if explicit else None,
        args={"x": 1} if explicit else [1],
        **values,
    )
    e = calls[-1][1][1]
    actual = (
        e.session_id,
        e.session_key,
        e.platform,
        e.subagent_id,
        e.task_index,
        e.task_count,
        e.goal,
        e.event_type,
        e.tool_name,
        e.preview,
        e.args,
        e.status,
        e.model,
        e.tool_count,
        e.duration_seconds,
        e.summary,
    )
    expected = (
        (
            "sid",
            "key",
            "discord",
            "worker",
            4,
            5,
            "goal",
            "done",
            "tool",
            "preview",
            {"x": 1},
            "ok",
            "m",
            6,
            1.5,
            "sum",
        )
        if explicit
        else ("sid", "key", "discord", "task-bad", 0, 1, "", "done", "", "", {}, "", "", 0, 0.0, "")
    )
    assert actual == expected


@pytest.mark.parametrize("local,global_", [(False, True), (True, False)])
def test_lifecycle_disabled_guards(monkeypatch, local, global_):
    _, renderer, context, calls = harness(monkeypatch)
    context.assistant_enabled, renderer.settings.assistant.enabled = local, global_
    assert ae.on_compression_lifecycle_from_agent(object(), "started") is False
    assert calls == []


@pytest.mark.parametrize(
    "data,text",
    [
        (
            {"before_tokens": 900, "after_tokens": 500, "after_tokens_kind": "rough"},
            "Context compaction checked · rough 900 → 500 tokens",
        ),
        (
            {"before_count": 2, "after_count": 2, "before_tokens": 1500, "after_tokens": 700},
            "Context compaction checked · 2k → 700 tokens",
        ),
    ],
)
def test_lifecycle_completed_integrated_variants(monkeypatch, data, text):
    plugin, renderer, context, calls = harness(monkeypatch)
    monkeypatch.setattr(ae, "_context_for", lambda *a: None)
    migrated = []
    renderer.migrate_context = lambda *a, **k: migrated.append(1)
    assert ae.on_compression_lifecycle_from_agent(
        object(), "completed", old_session_id="old", new_session_id="new", **data
    )
    assert migrated == [] and context.compaction_count == 1
    assert calls[-1][1][1].text == text and calls[-1][2] == {"force": True}
