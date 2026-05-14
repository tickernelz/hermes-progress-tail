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
        format_tool_line("terminal", {"command": "python3 - <<'PY'\nprint('x')\nPY"})
        == "💻 terminal: python inline script · print('x') · 3 lines"
    )
    assert (
        format_tool_line("terminal", {"command": "npm run build >/tmp/build.log 2>&1; echo ok"})
        == "💻 terminal: npm run build"
    )
    assert (
        format_tool_line("terminal", {"command": "cat /home/alice/.ssh/id_rsa"})
        == "💻 terminal: cat .ssh/id_rsa"
    )
    assert (
        format_tool_line(
            "terminal", {"command": "cat /home/alice/.ssh/id_rsa >/home/alice/out.txt"}
        )
        == "💻 terminal: cat .ssh/id_rsa >alice/out.txt"
    )
    assert (
        format_tool_line("search_files", {"pattern": "tool_progress", "path": "gateway"})
        == '🔎 search_files: "tool_progress" in gateway'
    )
    assert (
        format_tool_line(
            "multi_tool_use.parallel",
            {
                "tool_uses": [
                    {
                        "recipient_name": "functions.read_file",
                        "parameters": {"path": "formatter.py"},
                    },
                    {
                        "recipient_name": "functions.search_files",
                        "parameters": {"pattern": "density|verbose", "path": "tests"},
                    },
                    {
                        "recipient_name": "functions.terminal",
                        "parameters": {"command": "git status"},
                    },
                ]
            },
        )
        == '🧰 parallel: read_file formatter.py · search_files "density|verbose" · terminal git status'
    )


def test_formats_execution_and_orchestration_tools_without_raw_json():
    cases = {
        "execute_code": (
            {"code": "from pathlib import Path\npath = Path('/tmp/x')\nprint(path)"},
            "🐍 execute_code: path = Path('…') … print(path) · 3 lines",
        ),
        "process": (
            {"action": "poll", "session_id": "proc_abc123", "timeout": 30},
            "⚙️ process: poll proc_abc123",
        ),
        "cronjob": (
            {"action": "create", "name": "daily report", "schedule": "0 9 * * *"},
            "⚙️ cronjob: create daily report · 0 9 * * *",
        ),
        "clarify": (
            {"question": "Pilih mode?", "choices": ["focused", "sectioned"]},
            "⚙️ clarify: Pilih mode? · 2 choices",
        ),
        "delegate_task": (
            {"tasks": [{"goal": "review formatter"}, {"goal": "review renderer"}]},
            "🧑‍💻 delegate_task: 2 tasks",
        ),
    }

    for tool_name, (args, expected) in cases.items():
        line = format_tool_line(tool_name, args, preview_length=120)
        assert line == expected
        assert "{" not in line
        assert "}" not in line

    assert format_tool_line("delegate_task", {"tasks": [{"goal": "review formatter"}]}) == (
        "🧑‍💻 delegate_task: 1 task"
    )


def test_execute_code_summary_uses_meaningful_start_and_end():
    code = """from pathlib import Path
import json
# gather versions
root = Path('/tmp/project')
data = {'root': root.name}
print(json.dumps(data))
"""

    line = format_tool_line("execute_code", {"code": code}, preview_length=160)

    assert line == "🐍 execute_code: root = Path('…') … print(json.dumps(data)) · 6 lines"


def test_execute_code_summary_redacts_secret_values():
    code = """OPENAI_API_KEY = 'sk-secret'
print(OPENAI_API_KEY)
"""

    line = format_tool_line("execute_code", {"code": code}, preview_length=160)

    assert "sk-secret" not in line
    assert "[redacted_env]" in line


def test_execute_code_summary_hides_string_literals():
    code = """payload = {'password': 'super-secret-literal'}
print(payload)
"""

    line = format_tool_line("execute_code", {"code": code}, preview_length=160)

    assert "super-secret-literal" not in line
    assert line == "🐍 execute_code: payload = {'…': '…'} … print(payload) · 2 lines"


def test_long_search_patterns_use_middle_ellipsis():
    pattern = r"\.hx-ai-chat-interface \.ai-message-action \{|\.hx-ai-chat-interface \.ai-message-content pre code"

    line = format_tool_line(
        "search_files", {"pattern": pattern, "path": "hmx/module/basic/ai/static/css/views"}
    )

    assert line == (
        '🔎 search_files: "\\.hx-ai-chat-interface ...message-content pre code" '
        "in hmx/module/basic/ai/static/css/views"
    )


def test_formats_skill_and_memory_tools_without_raw_json():
    cases = {
        "skill_view": ({"name": "hermes-agent"}, "📚 skill_view: hermes-agent"),
        "skills_list": (
            {"category": "software-development"},
            "⚙️ skills_list: software-development",
        ),
        "skill_manage": (
            {"action": "patch", "name": "hermes-agent"},
            "⚙️ skill_manage: patch hermes-agent",
        ),
        "memory": ({"action": "add", "target": "user"}, "⚙️ memory: add user"),
        "session_search": (
            {"query": "progress tail", "limit": 3},
            '⚙️ session_search: "progress tail"',
        ),
    }

    for tool_name, (args, expected) in cases.items():
        line = format_tool_line(tool_name, args, preview_length=120)
        assert line == expected
        assert "{" not in line
        assert "}" not in line


def test_formats_communication_and_media_tools_without_raw_json():
    cases = {
        "send_message": (
            {"target": "telegram", "message": "build finished"},
            "⚙️ send_message: telegram",
        ),
        "text_to_speech": (
            {"text": "Halo semuanya", "output_path": "/tmp/progress.mp3"},
            "⚙️ text_to_speech: progress.mp3",
        ),
        "vision_analyze": (
            {
                "image_url": "https://cdn.example.com/screens/fail.png?token=secret#frag",
                "question": "apa yang rusak?",
            },
            "⚙️ vision_analyze: cdn.example.com/screens/fail.png",
        ),
        "imagegen": ({"prompt": "dark HUD"}, "⚙️ imagegen: prompt · 8 chars"),
    }

    for tool_name, (args, expected) in cases.items():
        line = format_tool_line(tool_name, args, preview_length=120)
        assert line == expected
        assert "{" not in line
        assert "}" not in line
        assert "build finished" not in line
        assert "Halo semuanya" not in line
        assert "apa yang rusak" not in line
        assert "dark HUD" not in line


def test_formats_browser_and_video_tools_without_raw_json():
    cases = {
        "browser_navigate": (
            {"url": "https://example.com/path?q=secret"},
            "⚙️ browser_navigate: example.com/path",
        ),
        "browser_click": ({"ref": "@e12"}, "⚙️ browser_click: @e12"),
        "browser_type": ({"ref": "@e2", "text": "secret input"}, "⚙️ browser_type: @e2"),
        "browser_console": ({"expression": "document.cookie"}, "⚙️ browser_console: browser"),
        "browser_snapshot": ({"full": True}, "⚙️ browser_snapshot: full"),
        "mcp_claude_video_vision_video_info": (
            {"path": "/tmp/videos/demo.mp4"},
            "⚙️ mcp_claude_video_vision_video_info: videos/demo.mp4",
        ),
        "mcp_claude_video_vision_video_watch": (
            {"path": "https://youtu.be/abc123", "start_time": "00:00:10", "end_time": "00:00:20"},
            "⚙️ mcp_claude_video_vision_video_watch: youtu.be/abc123 · 00:00:10-00:00:20",
        ),
    }

    for tool_name, (args, expected) in cases.items():
        line = format_tool_line(tool_name, args, preview_length=140)
        assert line == expected
        assert "{" not in line
        assert "}" not in line
        assert "secret" not in line


def test_formats_file_search_and_memory_extended_tools_without_raw_json():
    cases = {
        "search_files": (
            {"pattern": "agent_label", "path": ".", "target": "content", "file_glob": "*.py"},
            '🔎 search_files: "agent_label" in . · content · *.py',
        ),
        "hindsight_recall": ({"query": "progress tail"}, '⚙️ hindsight_recall: "progress tail"'),
        "hindsight_retain": (
            {"context": "project convention", "content": "secret note"},
            "⚙️ hindsight_retain: project convention",
        ),
        "lcm_grep": ({"query": "progress OR tail", "limit": 5}, '⚙️ lcm_grep: "progress OR tail"'),
        "lcm_expand": ({"store_id": 123, "max_tokens": 2000}, "⚙️ lcm_expand: store_id=123"),
        "lcm_load_session": ({"session_id": "abc", "limit": 100}, "⚙️ lcm_load_session: abc"),
    }

    for tool_name, (args, expected) in cases.items():
        line = format_tool_line(tool_name, args, preview_length=140)
        assert line == expected
        assert "{" not in line
        assert "}" not in line
        assert "secret note" not in line


def test_generic_fallback_prefers_compact_key_values_not_raw_json():
    line = format_tool_line(
        "custom_tool",
        {
            "action": "inspect",
            "target": "renderer",
            "api_key": "sk-secret",
            "headers": {"Authorization": "Bearer secret-token"},
            "env": {"API_KEY": "sk-secret"},
            "items": [1, 2, 3],
            "metadata": {"nested": True},
        },
        preview_length=120,
    )

    assert "headers=" not in line
    assert "env=" not in line
    assert line.startswith("⚙️ custom_tool: action=inspect · target=renderer")
    assert "items=3 items" in line
    assert "metadata=1 key" in line
    assert "sk-secret" not in line
    assert "{" not in line
    assert "}" not in line


def test_python_inline_script_summary_uses_meaningful_start_and_end():
    command = """python - <<'PY'
from pathlib import Path
import json
# prepare report
repo = Path.cwd()
result = {"repo": repo.name}
print(json.dumps(result))
PY"""

    line = format_tool_line("terminal", {"command": command}, preview_length=160)

    assert line == (
        "💻 terminal: python inline script · repo = Path.cwd() … "
        "print(json.dumps(result)) · 8 lines"
    )


def test_python_inline_script_summary_redacts_secret_values():
    command = """python - <<'PY'
OPENAI_API_KEY = "sk-secret"
print(OPENAI_API_KEY)
PY"""

    line = format_tool_line("terminal", {"command": command}, preview_length=160)

    assert "sk-secret" not in line
    assert "[redacted_env]" in line


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


def test_formats_project_paths_without_home_prefix(monkeypatch, tmp_path):
    project = tmp_path / "Projects" / "tail"
    project.mkdir(parents=True)
    monkeypatch.chdir(project)

    assert (
        format_tool_line(
            "read_file",
            {"path": str(project / "src" / "fmt.py"), "offset": 4, "limit": 8},
        )
        == "📖 read_file: src/fmt.py:4+8"
    )
    assert (
        format_tool_line(
            "search_files", {"pattern": "progress_tail", "path": str(project / "tests")}
        )
        == '🔎 search_files: "progress_tail" in tests'
    )


def test_project_relative_paths_still_redact_secret_like_components(monkeypatch, tmp_path):
    project = tmp_path / "Projects" / "tail"
    secret_dir = project / "API_KEY=supersecret1234567890"
    secret_dir.mkdir(parents=True)
    monkeypatch.chdir(project)

    line = format_tool_line("read_file", {"path": str(secret_dir / "file.py")}, preview_length=120)

    assert "supersecret" not in line
    assert "[redacted_env]" in line


def test_long_normal_file_names_keep_extension_context(monkeypatch, tmp_path):
    project = tmp_path / "Projects" / "tail"
    component = "VeryLongGeneratedButNormalComponentName" * 3
    file_path = project / "src" / "pages" / f"{component}.vue"
    file_path.parent.mkdir(parents=True)
    monkeypatch.chdir(project)

    line = format_tool_line(
        "read_file",
        {"path": str(file_path), "offset": 120, "limit": 95},
        preview_length=180,
    )

    assert line.startswith("📖 read_file: src/pages/VeryLongGeneratedButNormalCo")
    assert line.endswith("LongGeneratedButNormalComponentName.vue:120+95")
    assert "..." in line
    assert "[redacted_blob]" not in line


def test_hashed_asset_file_names_keep_extension_context(monkeypatch, tmp_path):
    project = tmp_path / "Projects" / "tail"
    filename = "a1b2c3d4e5f67890" * 6 + ".css"
    file_path = project / "dist" / filename
    file_path.parent.mkdir(parents=True)
    monkeypatch.chdir(project)

    line = format_tool_line(
        "patch",
        {"path": str(file_path), "old_string": ".hx-has", "new_string": ".hx-new"},
        preview_length=180,
    )

    assert line.startswith("🔧 patch: dist/a1b2c3d4e5f67890a")
    assert line.endswith('.css replace ".hx-has" → ".hx-new"')
    assert "..." in line
    assert "[redacted_blob]" not in line


def test_long_home_paths_use_middle_ellipsis_and_keep_filename(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    path = (
        tmp_path
        / "Works"
        / "HMX"
        / "hmx-002-Fundamental-New"
        / "hmx"
        / "module"
        / "basic"
        / "ai"
        / "static"
        / "css"
        / "views"
        / "ai-chat-interface.css"
    )

    line = format_tool_line("read_file", {"path": str(path), "offset": 1384, "limit": 26})

    assert line == "📖 read_file: ~/Works/HMX/.../static/css/views/ai-chat-interface.css:1384+26"
    assert "[redacted_blob]" not in line
    assert "hmx-002-Fundamental-New/hmx/module/basic/ai" not in line


def test_vue_paths_keep_filename_instead_of_redacted_blob(monkeypatch, tmp_path):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    component = "A" * 96 + ".vue"
    path = tmp_path / "Works" / "HMX" / "frontend" / "src" / "components" / component

    line = format_tool_line(
        "read_file",
        {"path": str(path), "offset": 120, "limit": 95},
        preview_length=180,
    )

    assert "[redacted_blob]" not in line
    assert line.endswith(f"components/{component}:120+95")
    assert line.startswith("📖 read_file: ~/Works/HMX/...")
