import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.hooks.install_report import PatchInstallReport
from hermes_progress_tail.runtime import commands


def _renderer(*sessions):
    return SimpleNamespace(
        settings=load_settings({"progress_tail": {"tools": {"timestamp": False}}}),
        sessions=dict(sessions),
    )


def _patch_runtime(monkeypatch, renderer, config=None):
    runtime = SimpleNamespace(
        get_renderer=lambda: renderer,
        assistant_capture=commands._COMMAND_RUNTIME.assistant_capture,
        patch_report=PatchInstallReport(),
        load_runtime_config=lambda: config or {},
    )
    monkeypatch.setattr(commands, "_COMMAND_RUNTIME", runtime)
    monkeypatch.setattr(commands, "_latest_release_info", lambda: None)


def _session(**overrides):
    values = {
        "session_key": "",
        "background_order": [],
        "background_jobs": {},
        "strategy": "edit",
        "disabled": False,
        "total_events": 3,
        "downgrade_reason": "",
        "last_error": "",
        "last_assistant_at": 0,
        "last_assistant_chars": 0,
        "last_reasoning_source": "",
        "last_reasoning_at": 0,
        "last_reasoning_chars": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_config_cleanup_without_mode_returns_usage_without_import(monkeypatch):
    monkeypatch.setitem(sys.modules, "hermes_progress_tail.installer", None)
    assert commands._config_cleanup_command("") == (
        "Usage: /progresstail config cleanup --dry-run\n       /progresstail config cleanup --apply"
    )


def test_hermes_home_uses_constants_and_has_isolated_fallback(monkeypatch, tmp_path):
    constants = ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: tmp_path / "configured"
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    assert commands._hermes_home() == tmp_path / "configured"

    constants.get_hermes_home = lambda: (_ for _ in ()).throw(RuntimeError("no config"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    assert commands._hermes_home() == tmp_path / "fake-home" / ".hermes"


def test_jobs_filter_missing_and_completed_and_redact_commands(monkeypatch):
    jobs = {
        "running": SimpleNamespace(
            status="running", exit_code=None, command="curl ?token=secret-value"
        ),
        "done": SimpleNamespace(status="completed", exit_code=0, command="finished"),
    }
    ctx = _session(background_order=["missing", "running", "done"], background_jobs=jobs)
    _patch_runtime(monkeypatch, _renderer(("sid-fallback", ctx)))

    output = commands._command("jobs")
    assert output.splitlines()[0] == "background_jobs=enabled"
    assert "running running exit=None session=sid-fallback" in output
    assert "secret-value" not in output
    assert "[redacted_env]" in output
    assert "missing" not in output
    assert "done completed" not in output


def test_jobs_all_preserves_order_and_empty_has_only_header(monkeypatch):
    jobs = {
        "first": SimpleNamespace(status="completed", exit_code=0, command="one"),
        "second": SimpleNamespace(status="running", exit_code=None, command="two"),
    }
    ctx = _session(
        session_key="friendly", background_order=["first", "second"], background_jobs=jobs
    )
    _patch_runtime(monkeypatch, _renderer(("sid", ctx)))
    lines = commands._command("jobs all").splitlines()
    assert [line.split()[0] for line in lines[1:]] == ["first", "second"]
    assert all("session=friendly" in line for line in lines[1:])

    _patch_runtime(monkeypatch, _renderer())
    assert commands._command("jobs") == "background_jobs=enabled"


def test_status_reflects_optional_patch_markers(monkeypatch):
    agent_module = ModuleType("run_agent")
    agent_module.AIAgent = type("AIAgent", (), {"_hermes_progress_tail_patched": True})
    tools_module = ModuleType("tools")
    tools_module.delegate_tool = SimpleNamespace(_hermes_progress_tail_delegate_patched=True)
    monkeypatch.setitem(sys.modules, "run_agent", agent_module)
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    _patch_runtime(monkeypatch, _renderer())

    output = commands._command("status")
    assert "monkeypatch=True" in output
    assert "delegate_monkeypatch=True" in output
    assert "command_menu_monkeypatch=" in output


def test_status_command_menu_probe_exception_is_inactive(monkeypatch):
    from hermes_progress_tail.hooks import monkeypatches

    monkeypatch.setattr(
        monkeypatches,
        "command_menu_monkeypatch_active",
        lambda: (_ for _ in ()).throw(RuntimeError("probe failed")),
    )
    _patch_runtime(monkeypatch, _renderer())
    assert "command_menu_monkeypatch=False" in commands._command("status")


def test_doctor_reports_redacted_details_and_deterministic_timestamps(monkeypatch):
    secret = "token-super-secret"
    ctx = _session(
        session_key="friendly",
        downgrade_reason=f"token={secret}",
        last_error=f"api_key={secret}",
        last_assistant_at=10,
        last_assistant_chars=42,
        last_reasoning_source="structured",
        last_reasoning_at=20,
        last_reasoning_chars=17,
    )
    _patch_runtime(monkeypatch, _renderer(("sid", ctx)))
    monkeypatch.setattr(commands.time, "localtime", lambda value: value)
    monkeypatch.setattr(commands.time, "strftime", lambda fmt, value: f"T{value}")

    output = commands._command("doctor")
    assert "session friendly: strategy=edit disabled=False events=3" in output
    assert "session friendly: downgraded=" in output
    assert "session friendly: last_error=" in output
    assert secret not in output
    assert "assistant chars=42 at=T10" in output
    assert "last_reasoning source=structured chars=17 at=T20" in output


def test_doctor_omits_empty_optional_session_details(monkeypatch):
    _patch_runtime(monkeypatch, _renderer(("sid", _session())))
    output = commands._command("doctor")
    assert "session sid: strategy=edit disabled=False events=3" in output
    assert "downgraded=" not in output
    assert "last_error=" not in output
    assert "assistant chars=" not in output
    assert "last_reasoning source=" not in output


def test_unknown_command_returns_exact_usage(monkeypatch):
    _patch_runtime(monkeypatch, _renderer())
    assert commands._command("not-a-command") == (
        "Usage: /progresstail status | doctor | jobs [all] | update --dry-run|--apply | "
        "config cleanup --dry-run|--apply | demo [plain|failed]"
    )
