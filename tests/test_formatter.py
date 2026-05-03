from hermes_progress_tail.formatter import format_tool_line


def test_formats_todo_with_current_task_and_counts():
    line = format_tool_line(
        "todo",
        {
            "todos": [
                {"content": "inspect hooks", "status": "completed"},
                {"content": "build renderer", "status": "in_progress"},
                {"content": "write tests", "status": "pending"},
                {"content": "smoke discord", "status": "pending"},
            ]
        },
        preview="updating 4 task(s)",
        preview_length=80,
    )

    assert line == "📋 todo: ▶ build renderer · 2 pending · 1 done"


def test_formats_file_tools_compactly():
    assert (
        format_tool_line(
            "read_file", {"path": "/tmp/project/gateway/run.py", "offset": 10, "limit": 20}
        )
        == "📖 read_file: gateway/run.py:10+20"
    )
    assert (
        format_tool_line("write_file", {"path": "/tmp/project/src/formatter.py"})
        == "✍️ write_file: src/formatter.py"
    )
    assert (
        format_tool_line("patch", {"path": "/tmp/project/src/renderer.py"})
        == "🔧 patch: src/renderer.py"
    )


def test_formats_search_terminal_and_parallel():
    assert (
        format_tool_line("terminal", {"command": "npm run build", "workdir": "/tmp/project"})
        == "💻 terminal: npm run build · cwd project"
    )
    assert (
        format_tool_line("search_files", {"pattern": "tool_progress", "path": "gateway"})
        == '🔎 search_files: "tool_progress" in gateway'
    )
    assert (
        format_tool_line("multi_tool_use.parallel", {"tool_uses": [{}, {}, {}]})
        == "🧰 parallel: 3 tool calls"
    )


def test_formatter_truncates_and_redacts():
    line = format_tool_line(
        "terminal",
        {"command": "OPENAI_API_KEY=sk-secret python deploy.py " + "x" * 100},
        preview_length=70,
    )

    assert "sk-secret" not in line
    assert len(line) <= 73
    assert "[redacted_env]" in line
