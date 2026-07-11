import pytest

from hermes_progress_tail.models.state import TodoItem
from hermes_progress_tail.rendering.formatter import (
    _fallback,
    _fmt_terminal_command,
    _patch_stats,
    _preview_text,
    _safe_script_preview_line,
    _short_path,
    _truncate,
    _truncate_middle,
    extract_todo_items,
    format_tool_line,
    summarize_todo_items,
)


@pytest.mark.parametrize(
    ("fn", "limit", "expected"),
    [
        (_truncate, 0, "abcdef"),
        (_truncate, 1, "a"),
        (_truncate, 3, "abc"),
        (_truncate, 4, "a..."),
        (_truncate_middle, 2, "ab"),
        (_truncate_middle, 5, "a...f"),
    ],
)
def test_tiny_truncation_partitions(fn, limit, expected):
    assert fn("abcdef", limit) == expected


def test_path_and_todo_malformed_partitions():
    assert _short_path(None) == "<unknown>"
    assert _short_path("[redacted_blob]") == "[redacted_blob]"
    long = "~/" + "/".join(["segment"] * 20)
    assert len(_short_path(long)) <= 80
    items = extract_todo_items({"todos": [None, {}, {"content": "go", "status": "bogus"}]})
    assert items == (TodoItem("go", "pending"),)
    assert summarize_todo_items((TodoItem("one", "in_progress"),)) == "▶ one"
    assert (
        summarize_todo_items((TodoItem("", "in_progress"), TodoItem("", "in_progress")))
        == "2 in_progress"
    )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("", "<empty>"),
        ('echo "unterminated', 'echo "unterminated'),
        ("\n# only\nimport os", "shell script · # only … import os · 2 lines"),
        ("python - <<'PY'\nprint('x')\nPY", "python inline script · print('x') · 3 lines"),
        ("node x typescript/bin/tsc -p . --noEmit", "tsc -p . --noEmit"),
        ("python -m pre_commit run --all-files", "pre-commit run --all-files"),
        ("   ", "<empty>"),
    ],
)
def test_terminal_exact_text(command, expected):
    assert _fmt_terminal_command(command) == expected


def test_empty_safe_line_read_and_preview_edges():
    assert _safe_script_preview_line("") == ""
    assert format_tool_line("read_file", {"path": "a", "offset": 7}) == "📖 read_file: a:7"
    assert _preview_text(" \n ", 20) == "<empty>"


def test_patch_delete_headers_stats_and_visibility():
    patch = """*** Delete File: a.py
--- a.py
-old
*** Add File: b.py
+new
*** Update File: c.py
+x
-y
*** Add File: d.py
"""
    assert _patch_stats("words", 3) == "patch text"
    assert _patch_stats("*** Delete File: a.py", 3) == "1 file · a.py delete"
    assert _patch_stats(patch, 3) == "4 files · a.py -1 · b.py +1 · c.py +1/-1 · +1 more"
    assert _patch_stats(patch, 2).endswith("+2 more")
    assert format_tool_line("patch", {}, patch_detail="off") == "🔧 patch: patch"
    assert (
        format_tool_line("patch", {"path": "a.py", "mode": "replace"}, patch_detail="stats")
        == "🔧 patch: a.py replace"
    )


def test_fallback_bool_preview_sanitized_and_skipped():
    assert _fallback({"enabled": True}, None, 80) == "enabled=true"
    assert _fallback({}, "visible", 80) == "visible"
    assert _fallback({}, None, 80) == "tool call"
    assert _fallback({"token": "secret", "": 1}, None, 80) == "tool call"
    assert format_tool_line("unknown", {"enabled": False}) == "⚙️ unknown: enabled=false"
