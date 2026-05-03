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
        format_tool_line("patch", {"path": "/tmp/project/src/renderer.py"}, patch_detail="path")
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


def test_formats_patch_replace_with_intent_preview():
    line = format_tool_line(
        "patch",
        {
            "path": "/tmp/project/hermes_progress_tail/formatter.py",
            "old_string": "todo: updating 5 task(s)",
            "new_string": "todo: ▶ implement patch detail · 2 pending",
        },
        preview_length=140,
    )

    assert (
        line
        == '🔧 patch: hermes_progress_tail/formatter.py replace "todo: updating 5 task(s)" → "todo: ▶ implement patch detail · 2 pending"'
    )


def test_formats_patch_remove_and_replace_all():
    assert (
        format_tool_line(
            "patch",
            {
                "path": "/tmp/project/src/renderer.py",
                "old_string": "Still working...",
                "new_string": "",
            },
            preview_length=100,
        )
        == '🔧 patch: src/renderer.py remove "Still working..."'
    )
    assert (
        format_tool_line(
            "patch",
            {
                "path": "/tmp/project/src/installer.py",
                "old_string": "tool-progress-tail",
                "new_string": "hermes-progress-tail",
                "replace_all": True,
            },
            preview_length=110,
        )
        == '🔧 patch: src/installer.py replace all "tool-progress-tail" → "hermes-progress-tail"'
    )


def test_formats_multi_file_patch_stats_with_limit():
    patch_text = """*** Begin Patch
*** Update File: hermes_progress_tail/renderer.py
@@
-old
+new
+next
*** Update File: hermes_progress_tail/formatter.py
@@
-a
-b
+c
*** Add File: tests/test_patch.py
+def test_patch():
+    pass
*** Update File: README.md
@@
-old docs
+new docs
*** End Patch
"""

    line = format_tool_line(
        "patch",
        {"mode": "patch", "patch": patch_text},
        preview_length=140,
        patch_max_files=3,
    )

    assert (
        line
        == "🔧 patch: 4 files · hermes_progress_tail/renderer.py +2/-1 · hermes_progress_tail/formatter.py +1/-2 · tests/test_patch.py +2 · +1 more"
    )


def test_patch_preview_redacts_secrets():
    line = format_tool_line(
        "patch",
        {
            "path": "/tmp/project/.env",
            "old_string": "OPENAI_API_KEY=sk-oldsecret",
            "new_string": "OPENAI_API_KEY=sk-newsecret",
        },
        preview_length=120,
    )

    assert "sk-oldsecret" not in line
    assert "sk-newsecret" not in line
    assert "[redacted_env]" in line
