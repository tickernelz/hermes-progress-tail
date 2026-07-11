"""GREEN characterizations that must pass before and after C6."""

from collections import deque

from hermes_progress_tail.models import state


def positional(**kwargs):
    return state.SessionContext(
        "s", "k", "discord", "c", None, "adapter", "loop", "manual", **kwargs
    )


def test_legacy_positional_and_keyword_construction():
    pos = positional(lines=9, assistant_latest_text="answer", reasoning_text="thought")
    kw = state.SessionContext(
        session_id="s",
        session_key="k",
        platform="discord",
        chat_id="c",
        thread_id=None,
        adapter="adapter",
        loop="loop",
        strategy="manual",
    )
    assert (pos.session_id, pos.strategy, pos.lines) == ("s", "manual", 9)
    assert (pos.assistant_latest_text, pos.reasoning_text) == ("answer", "thought")
    assert (kw.session_id, kw.strategy, kw.lines) == ("s", "manual", 3)


def test_all_keyword_required_construction_remains_legal():
    ctx = state.SessionContext(
        session_id="keyword",
        session_key="k",
        platform="telegram",
        chat_id="c",
        thread_id="1",
        adapter=None,
        loop=None,
    )
    assert (ctx.session_id, ctx.strategy, ctx.thread_id) == ("keyword", "auto", "1")


def test_mutable_defaults_are_independent():
    first, second = positional(), positional()
    mutable_names = (
        "tool_lines",
        "active_tool_lines",
        "active_tool_fingerprints",
        "completed_tool_ids",
        "delegate_branches",
        "delegate_order",
        "background_jobs",
        "background_order",
        "lock",
        "environment",
    )
    assert all(getattr(first, name) is not getattr(second, name) for name in mutable_names)
    first.tool_lines.append("x")
    assert list(second.tool_lines) == []


def test_equality_and_repr_are_dataclass_semantics():
    first = positional(started_at=1.0, last_event_at=2.0)
    second = positional(started_at=1.0, last_event_at=2.0)
    first.lock = second.lock = None
    assert first == second
    second.assistant_latest_text = "different"
    assert first != second
    text = repr(first)
    assert text.startswith("SessionContext(session_id='s'")
    assert "session_key='k'" in text


def test_assistant_stream_transient_and_pending_compatibility():
    lines = deque([state.AssistantLine("old")], maxlen=7)
    ctx = positional(
        assistant_lines=lines,
        assistant_latest_text="stream",
        assistant_pending_chars=4,
        last_assistant_chars=3,
        last_assistant_at=2.5,
        assistant_transient=True,
    )
    assert ctx.assistant_lines is lines and ctx.assistant_lines.maxlen == 7
    assert (
        ctx.assistant_latest_text,
        ctx.assistant_pending_chars,
        ctx.last_assistant_chars,
        ctx.last_assistant_at,
        ctx.assistant_transient,
    ) == ("stream", 4, 3, 2.5, True)
    ctx.assistant_latest_text = "replacement"
    ctx.assistant_pending_chars = 0
    ctx.assistant_transient = False
    assert (ctx.assistant_latest_text, ctx.assistant_pending_chars, ctx.assistant_transient) == (
        "replacement",
        0,
        False,
    )


def test_reasoning_source_pending_and_trim_compatibility():
    ctx = positional(
        reasoning_text="abcdef",
        reasoning_pending_chars=6,
        last_reasoning_source="structured",
        last_reasoning_chars=5,
        last_reasoning_at=3.5,
    )
    assert (
        ctx.reasoning_text,
        ctx.reasoning_pending_chars,
        ctx.last_reasoning_source,
        ctx.last_reasoning_chars,
        ctx.last_reasoning_at,
    ) == ("abcdef", 6, "structured", 5, 3.5)
    ctx.reasoning_text = ctx.reasoning_text[-3:]
    ctx.reasoning_pending_chars = 0
    assert (ctx.reasoning_text, ctx.reasoning_pending_chars) == ("def", 0)
