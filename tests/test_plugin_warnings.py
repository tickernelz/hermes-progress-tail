import logging

import hermes_progress_tail.plugin as plugin
from hermes_progress_tail.config import load_settings


class Ctx:
    def __init__(self):
        self.hooks = []
        self.commands = []

    def register_hook(self, name, fn):
        self.hooks.append((name, fn))

    def register_command(self, name, fn, **kwargs):
        self.commands.append((name, fn, kwargs))


def test_status_warns_when_builtin_reasoning_is_enabled(monkeypatch):
    plugin._renderer = None
    config = {
        "display": {"show_reasoning": True},
        "progress_tail": {"enabled": True, "reasoning": {"enabled": True}},
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings(config))

    status = plugin._command("status")

    assert "hermes-progress-tail 0.1.38" in status
    assert "tools=enabled" in status
    assert "completed=True" in status
    assert "duration=True" in status
    assert "todo=sticky:True hide_tool_line:True" in status
    assert "patch=detail:smart preview_chars:48 max_files:3" in status
    assert "renderer=mode:sectioned strategy:auto style:emoji density:normal" in status
    assert "code_fence:auto" in status
    assert "agent_label:-" in status
    assert "display.show_reasoning=True" in status


def test_register_logs_warning_once_for_reasoning_conflict(monkeypatch, caplog):
    plugin._renderer = None
    config = {
        "agent": {"gateway_notify_interval": 0},
        "display": {"show_reasoning": True},
        "progress_tail": {"enabled": True, "reasoning": {"enabled": True}},
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "install_monkeypatches", lambda: None)
    ctx = Ctx()

    with caplog.at_level(logging.WARNING):
        plugin.register(ctx)

    assert any("display.show_reasoning=true" in record.message for record in caplog.records)
    assert len(ctx.hooks) == 6
    assert ctx.commands[0][0] == "progresstail"


def test_register_logs_warning_for_core_notifier_conflict(monkeypatch, caplog):
    plugin._renderer = None
    config = {
        "agent": {"gateway_notify_interval": 180},
        "display": {"show_reasoning": False},
        "progress_tail": {"enabled": True},
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "install_monkeypatches", lambda: None)
    ctx = Ctx()

    with caplog.at_level(logging.WARNING):
        plugin.register(ctx)

    assert any(
        "agent.gateway_notify_interval is enabled" in record.message for record in caplog.records
    )
    assert len(ctx.hooks) == 6
    assert ctx.commands[0][0] == "progresstail"


def test_doctor_reports_display_warning_and_session_errors(monkeypatch):
    plugin._renderer = None
    config = {
        "plugins": {"enabled": ["hermes-progress-tail"]},
        "agent": {"gateway_notify_interval": 180},
        "display": {"tool_progress": "all", "show_reasoning": False},
        "progress_tail": {"enabled": True},
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings(config))
    renderer = plugin._get_renderer()

    class Adapter:
        async def send(self, *args, **kwargs):
            return None

    from hermes_progress_tail.state import SessionContext

    ctx = SessionContext("s1", "key", "discord", "chat", None, Adapter(), None, "snapshot")
    ctx.downgrade_reason = "edit not supported"
    ctx.last_error = "edit not supported"
    renderer.register_context(ctx)

    doctor = plugin._command("doctor")

    assert "warning: display.tool_progress is not off" in doctor
    assert "warning: agent.gateway_notify_interval is enabled" in doctor
    assert "agent.gateway_notify_interval=180" in doctor
    assert "session key: strategy=snapshot" in doctor
    assert "downgraded=edit not supported" in doctor


def test_doctor_warns_when_telegram_code_fence_is_forced_on(monkeypatch):
    plugin._renderer = None
    config = {
        "display": {"tool_progress": "off", "show_reasoning": False},
        "agent": {"gateway_notify_interval": 0},
        "progress_tail": {
            "enabled": True,
            "renderer": {"code_fence": "on"},
        },
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings(config))

    doctor = plugin._command("doctor")

    assert "warning: Telegram progress code_fence=on is unsupported" in doctor


def test_doctor_reports_unknown_and_retired_config_keys(monkeypatch):
    plugin._renderer = None
    config = {
        "display": {"tool_progress": "off", "show_reasoning": False},
        "agent": {"gateway_notify_interval": 0},
        "progress_tail": {
            "enabled": True,
            "tools": {"enabled": True, "typo_lines": 4},
            "background_jobs": {"default_notify_on_complete": False},
            "finalization": {"delete_on_success": True},
            "platforms": {"telegram": {"bogus": "value"}},
        },
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings(config))

    doctor = plugin._command("doctor")

    assert "warning: retired config key progress_tail.finalization" in doctor
    assert (
        "warning: retired config key progress_tail.background_jobs.default_notify_on_complete"
        in doctor
    )
    assert "warning: unknown config key progress_tail.tools.typo_lines" in doctor
    assert "warning: unknown config key progress_tail.platforms.telegram.bogus" in doctor


def test_demo_commands_return_sample_progress(monkeypatch):
    plugin._renderer = None
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: {})
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings({}))

    demo = plugin._command("demo failed")

    assert "**Hermes is working**" in demo
    assert "**Tools**" in demo
    assert "× terminal: pytest tests/test_renderer.py -q · 2.1s" in demo
    assert "git diff --check" in demo
