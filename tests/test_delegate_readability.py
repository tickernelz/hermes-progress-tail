from collections import deque

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.rendering.delegate import DelegateProgressRenderer
from hermes_progress_tail.state import DelegateBranch, DelegateLine, SessionContext


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
        renderer={"style": "emoji"},
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

    assert "├ read_file: rendering/formatter.py:260+260" in section
    assert "├ terminal: python -m pytest -q tests/test_formatter.py · cwd ." in section
    assert "tool:" not in section
    assert "~/.hermes/plugins/hermes-progress-tail/hermes_progress_tail" not in section
    assert "/home/zhafron/.hermes/plugins/hermes-progress-tail" not in section
    assert "└ result:" in section


def test_delegate_section_collapses_completed_tool_burst_when_result_exists():
    renderer = make_renderer(
        renderer={"style": "emoji"},
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

    assert "├ 4 tools · read_file, search_files, terminal, read_file" in section
    assert "read_file: /repo/a.py" not in section
    assert 'search_files "format"' not in section
    assert "terminal: pytest -q" not in section
    assert "└ result: ✓ done: smoke check passed" in section


def test_delegate_title_uses_plain_status_symbol_in_emoji_style():
    renderer = make_renderer(
        renderer={"style": "emoji"},
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
