from collections import deque

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.rendering.delegate import DelegateProgressRenderer
from hermes_progress_tail.state import DelegateBranch, DelegateEvent, DelegateLine, SessionContext


class Adapter:
    pass


def make_ctx():
    return SessionContext(
        "s1",
        "k1",
        "telegram",
        "chat",
        "thread",
        Adapter(),
        None,
        "live_tail",
    )


def make_renderer(**config):
    return DelegateProgressRenderer(load_settings({"progress_tail": config}))


def test_delegate_section_simplifies_tool_labels_and_paths():
    renderer = make_renderer(
        renderer={"style": "emoji", "mode": "focused"},
        delegates={"lines_per_delegate": 3, "max_line_chars": 180},
    )
    ctx = make_ctx()
    branch = DelegateBranch(
        subagent_id="sa-1",
        task_index=0,
        task_count=1,
        goal="Visible formatted progress-tail smoke task A",
        status="completed",
        duration_seconds=32,
        lines=deque(
            [
                DelegateLine(
                    "tool",
                    "📖 read_file: ~/.hermes/plugins/hermes-progress-tail/hermes_progress_tail/rendering/formatter.py:260+260",
                    tool_name="read_file",
                ),
                DelegateLine(
                    "tool",
                    "🖥 terminal: python -m pytest -q tests/test_formatter.py · cwd ~/.hermes/plugins/hermes-progress-tail",
                    tool_name="terminal",
                ),
            ],
            maxlen=3,
        ),
        completion_line="✓ done: Sudah saya lakukan smoke check read-only pada implementasi progress tail yang terpasang di /home/zhafron/.hermes/plugins/hermes-progress-tail",
    )
    ctx.delegate_branches["sa-1"] = branch
    ctx.delegate_order.append("sa-1")

    section = renderer.section(ctx)

    assert "read_file: rendering/formatter.py:260+260" not in section
    assert "terminal: python -m pytest -q tests/test_formatter.py · cwd ." not in section
    assert "tool:" not in section
    assert "~/.hermes/plugins/hermes-progress-tail/hermes_progress_tail" not in section
    assert "/home/zhafron/.hermes/plugins/hermes-progress-tail" not in section
    assert "└ result:" in section


def test_delegate_section_collapses_completed_tool_burst_when_result_exists():
    renderer = make_renderer(
        renderer={"style": "emoji", "mode": "focused"},
        delegates={"lines_per_delegate": 5, "max_line_chars": 180},
    )
    ctx = make_ctx()
    branch = DelegateBranch(
        subagent_id="sa-1",
        task_index=0,
        task_count=1,
        goal="Visible formatted progress-tail smoke task A",
        status="completed",
        duration_seconds=32,
        lines=deque(
            [
                DelegateLine("tool", "📖 read_file: /repo/a.py:1+20", tool_name="read_file"),
                DelegateLine("tool", '🔎 search_files "format"', tool_name="search_files"),
                DelegateLine("tool", "🖥 terminal: pytest -q", tool_name="terminal"),
                DelegateLine("tool", "📖 read_file: /repo/b.py:1+20", tool_name="read_file"),
            ],
            maxlen=5,
        ),
        completion_line="✓ done: smoke check passed",
    )
    ctx.delegate_branches["sa-1"] = branch
    ctx.delegate_order.append("sa-1")

    section = renderer.section(ctx)

    assert "4 tools · read_file, search_files, terminal, read_file" not in section
    assert "read_file: /repo/a.py" not in section
    assert 'search_files "format"' not in section
    assert "terminal: pytest -q" not in section
    assert "└ result: ✓ done: smoke check passed" in section


def test_delegate_completed_result_middle_truncates_long_summary():
    renderer = make_renderer(
        renderer={"style": "emoji", "mode": "focused"},
        delegates={"lines_per_delegate": 5, "max_line_chars": 120},
    )
    ctx = make_ctx()
    branch = DelegateBranch(
        subagent_id="sa-1",
        task_index=0,
        task_count=1,
        goal="Review long delegate result",
        status="completed",
        duration_seconds=32,
        lines=deque(
            [
                DelegateLine("tool", "📖 read_file: /repo/a.py:1+20", tool_name="read_file"),
                DelegateLine("tool", "🖥 terminal: pytest -q", tool_name="terminal"),
            ],
            maxlen=5,
        ),
        completion_line="✓ done: short fallback",
        completion_summary=(
            "✓ done: PASS start verdict. "
            + "front filler detail " * 40
            + "UNIQUE_MIDDLE_SENTINEL "
            + "tail filler detail " * 40
            + "Final caveat stays visible."
        ),
    )
    ctx.delegate_branches["sa-1"] = branch
    ctx.delegate_order.append("sa-1")

    section = renderer.section(ctx)

    assert "PASS start verdict" in section
    assert "Final caveat stays visible" in section
    assert "short fallback" not in section
    assert "…" in section
    assert "UNIQUE_MIDDLE_SENTINEL" not in section
    assert "read_file: /repo/a.py" not in section
    assert "terminal: pytest -q" not in section


def test_delegate_completed_compact_result_middle_truncates_summary():
    renderer = make_renderer(
        renderer={"style": "emoji", "mode": "focused", "density": "compact"},
        delegates={"lines_per_delegate": 5, "max_line_chars": 80},
    )
    ctx = make_ctx()
    branch = DelegateBranch(
        subagent_id="sa-1",
        task_index=0,
        task_count=1,
        goal="Review compact long delegate result",
        status="completed",
        duration_seconds=32,
        lines=deque(
            [DelegateLine("tool", "📖 read_file: /repo/a.py:1+20", tool_name="read_file")],
            maxlen=5,
        ),
        completion_line=(
            "✓ done: PASS compact start. "
            + "front filler detail " * 40
            + "UNIQUE_COMPACT_MIDDLE "
            + "tail filler detail " * 40
            + "Compact final caveat."
        ),
    )
    ctx.delegate_branches["sa-1"] = branch
    ctx.delegate_order.append("sa-1")

    section = renderer.section(ctx)

    assert "PASS compact start" in section
    assert "Compact final caveat" in section
    assert "…" in section
    assert "UNIQUE_COMPACT_MIDDLE" not in section
    assert "read_file: /repo/a.py" not in section


def test_delegate_completion_event_preserves_full_focused_result_until_render():
    renderer = make_renderer(
        renderer={"style": "emoji", "mode": "focused"},
        delegates={"lines_per_delegate": 2, "max_line_chars": 90},
    )
    ctx = make_ctx()
    long_summary = (
        "Done — I traced the O2M inline-edit command path and did a read-only analysis. "
        + "Detailed frontend command construction notes that should not be dropped early. " * 8
        + "Final recommendation stays visible."
    )

    renderer.apply_event(
        ctx,
        DelegateEvent(
            "s1",
            "k1",
            "telegram",
            "sa-1",
            task_index=0,
            task_count=1,
            goal="Analyze frontend command construction path",
            event_type="subagent.complete",
            status="completed",
            duration_seconds=396,
            summary=long_summary,
        ),
    )

    branch = ctx.delegate_branches["sa-1"]
    section = renderer.section(ctx)

    assert "Detailed frontend command construction notes" in branch.completion_summary
    assert "Final recommendation stays visible" in branch.completion_summary
    assert "Done — I traced the O2M inline-edit command path" in section
    assert "Final recommendation stays visible" in section
    assert "Detailed frontend command construction notes" in section
    assert "396s" in section


def test_delegate_failed_result_keeps_recent_progress_context():
    renderer = make_renderer(
        renderer={"style": "emoji", "mode": "focused"},
        delegates={"lines_per_delegate": 5, "max_line_chars": 160},
    )
    ctx = make_ctx()
    branch = DelegateBranch(
        subagent_id="sa-1",
        task_index=0,
        task_count=1,
        goal="Review failing delegate result",
        status="failed",
        duration_seconds=12,
        lines=deque(
            [
                DelegateLine("tool", "📖 read_file: /repo/a.py:1+20", tool_name="read_file"),
                DelegateLine("tool", "🖥 terminal: pytest -q", tool_name="terminal"),
            ],
            maxlen=5,
        ),
        completion_line="✗ failed: tests failed with assertion error",
    )
    ctx.delegate_branches["sa-1"] = branch
    ctx.delegate_order.append("sa-1")

    section = renderer.section(ctx)

    assert "read_file: /repo/a.py" in section
    assert "terminal: pytest -q" in section
    assert "└ result: ✗ failed: tests failed with assertion error" in section


def test_delegate_title_uses_plain_status_symbol_in_emoji_style():
    renderer = make_renderer(
        renderer={"style": "emoji", "mode": "focused"},
        delegates={"lines_per_delegate": 1, "max_line_chars": 180},
    )
    ctx = make_ctx()
    branch = DelegateBranch(
        subagent_id="sa-1",
        task_index=0,
        task_count=1,
        goal="Review compact renderer mode cleanup",
        status="completed",
        duration_seconds=47,
    )
    ctx.delegate_branches["sa-1"] = branch
    ctx.delegate_order.append("sa-1")

    section = renderer.section(ctx)

    assert "[1/1] ✓ completed · Review compact renderer mode cleanup · 47s" in section
    assert "[1/1] ✅ completed" not in section


def test_delegate_title_infers_total_when_events_default_task_count_to_one():
    renderer = make_renderer(renderer={"style": "plain"})
    ctx = make_ctx()
    for index, goal in enumerate(("Audit config schema", "Audit runtime usage", "Audit docs")):
        key = f"task-{index}"
        ctx.delegate_branches[key] = DelegateBranch(
            subagent_id=key,
            task_index=index,
            task_count=1,
            goal=goal,
            status="running",
        )
        ctx.delegate_order.append(key)

    section = renderer.section(ctx)

    assert "[1/3] running · Audit config schema" in section
    assert "[2/3] running · Audit runtime usage" in section
    assert "[3/3] running · Audit docs" in section
    assert "[2/1]" not in section
    assert "[3/1]" not in section
