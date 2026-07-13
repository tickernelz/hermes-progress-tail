import logging

import pytest
import yaml

import hermes_progress_tail.plugin as plugin
from hermes_progress_tail.runtime import commands
from hermes_progress_tail.settings.loading import load_settings


@pytest.fixture(autouse=True)
def _configured_command_runtime(monkeypatch):
    monkeypatch.setattr(plugin._runtime, "renderer", None)
    monkeypatch.setattr(plugin._runtime, "settings_loader", lambda: plugin._load_runtime_settings())
    monkeypatch.setattr(commands, "_load_runtime_config", lambda: plugin._load_runtime_config())


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
    monkeypatch.setattr(commands, "_latest_release_info", lambda: None)

    status = plugin._command("status")

    assert "hermes-progress-tail 0.2.10" in status
    assert "## Hermes Progress Tail" in status
    assert "| Version | `0.2.10` |" in status
    assert "## Runtime" in status
    assert "tools=enabled" in status
    assert "completed=True" in status
    assert "duration=True" in status
    assert "todo=sticky:True hide_tool_line:True" in status
    assert "patch=detail:smart preview_chars:48 max_files:3" in status
    assert "delegates=enabled max=4 lines=2 ttl=5s thinking=off" in status
    assert "background_jobs=enabled list_running=True show_completed=True max=4" in status
    assert "renderer=mode:sectioned strategy:auto style:emoji density:normal" in status
    assert "code_fence" not in status
    assert "agent_label:-" in status
    assert "native_gateway=suppress:True" in status
    assert "display.show_reasoning=True" in status
    assert "reasoning_effort=auto" in status
    assert "warning: display.show_reasoning=true" not in status
    assert "Update available" not in status


def test_status_reports_update_only_when_newer_release_exists(monkeypatch):
    plugin._renderer = None
    config = {
        "plugins": {"enabled": ["hermes-progress-tail"]},
        "display": {"tool_progress": "off", "show_reasoning": False},
        "agent": {"gateway_notify_interval": 0},
        "progress_tail": {"enabled": True},
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings(config))
    monkeypatch.setattr(
        commands,
        "_latest_release_info",
        lambda: {"tag_name": "v0.2.11", "html_url": "https://example.test/v0.2.11"},
    )

    status = plugin._command("status")

    assert "## Update available" in status
    assert "v0.2.10 → v0.2.11" in status
    assert "https://example.test/v0.2.11" in status


def test_status_hides_update_when_latest_release_is_not_newer(monkeypatch):
    plugin._renderer = None
    config = {
        "plugins": {"enabled": ["hermes-progress-tail"]},
        "display": {"tool_progress": "off", "show_reasoning": False},
        "agent": {"gateway_notify_interval": 0},
        "progress_tail": {"enabled": True},
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings(config))
    monkeypatch.setattr(
        commands,
        "_latest_release_info",
        lambda: {"tag_name": "v0.2.10", "html_url": "https://example.test/v0.2.10"},
    )

    status = plugin._command("status")

    assert "Update available" not in status


def test_register_does_not_warn_for_reasoning_when_native_gateway_suppression_handles_it(
    monkeypatch, caplog
):
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

    assert not any("display.show_reasoning=true" in record.message for record in caplog.records)
    assert len(ctx.hooks) == 6
    command_names = [name for name, _fn, _kwargs in ctx.commands]
    assert command_names == [
        "progresstail",
        "progresstail-update",
        "progresstail-doctor",
        "progresstail-jobs",
        "progresstail-cleanup",
        "progresstail-demo",
    ]
    assert "update --dry-run" in ctx.commands[0][2]["args_hint"]
    assert "config cleanup --dry-run" in ctx.commands[0][2]["args_hint"]


def test_register_does_not_warn_for_core_notifier_when_native_gateway_suppression_handles_it(
    monkeypatch, caplog
):
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

    assert not any(
        "agent.gateway_notify_interval is enabled" in record.message for record in caplog.records
    )
    assert len(ctx.hooks) == 6
    command_names = [name for name, _fn, _kwargs in ctx.commands]
    assert command_names == [
        "progresstail",
        "progresstail-update",
        "progresstail-doctor",
        "progresstail-jobs",
        "progresstail-cleanup",
        "progresstail-demo",
    ]
    assert "update --dry-run" in ctx.commands[0][2]["args_hint"]
    assert "config cleanup --dry-run" in ctx.commands[0][2]["args_hint"]


def test_doctor_does_not_warn_when_native_gateway_config_is_normal(monkeypatch):
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

    assert "warning: display.tool_progress" not in doctor
    assert "warning: agent.gateway_notify_interval" not in doctor
    assert "agent.gateway_notify_interval=180" in doctor
    assert "session key: strategy=snapshot" in doctor
    assert "downgraded=edit not supported" in doctor


def test_doctor_warns_about_legacy_global_suppression_keys(monkeypatch):
    plugin._renderer = None
    config = {
        "plugins": {"enabled": ["hermes-progress-tail"]},
        "agent": {"gateway_notify_interval": 0},
        "display": {
            "tool_progress": "off",
            "streaming": False,
            "show_reasoning": False,
            "interim_assistant_messages": False,
        },
        "streaming": {"enabled": False},
        "progress_tail": {"enabled": True},
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings(config))

    doctor = plugin._command("doctor")

    assert (
        "warning: display.tool_progress is globally off; progress-tail suppresses native gateway progress plugin-side now"
        in doctor
    )
    assert (
        "warning: display.streaming is globally false; restore it if you want native streaming outside progress-tail-owned gateway updates"
        in doctor
    )
    assert (
        "warning: streaming.enabled is globally false; restore it if you want native streaming outside progress-tail-owned gateway updates"
        in doctor
    )
    assert (
        "warning: display.show_reasoning is globally false; progress-tail suppresses native gateway reasoning plugin-side now"
        in doctor
    )
    assert (
        "warning: display.interim_assistant_messages is globally false; progress-tail suppresses native gateway interim assistant messages plugin-side now"
        in doctor
    )
    assert (
        "warning: agent.gateway_notify_interval is globally disabled; progress-tail suppresses native gateway long-running notices plugin-side now"
        in doctor
    )


def test_config_cleanup_command_dry_run_reports_without_writing(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "display": {"tool_progress": "off", "show_reasoning": False},
                "agent": {"gateway_notify_interval": 0},
            }
        ),
        encoding="utf-8",
    )
    before = config_path.read_text(encoding="utf-8")
    monkeypatch.setattr(commands, "_hermes_home", lambda: hermes_home)

    output = plugin._command("config cleanup --dry-run")

    assert "Would remove legacy global suppression keys" in output
    assert "display.tool_progress" in output
    assert config_path.read_text(encoding="utf-8") == before


def test_config_cleanup_alias_defaults_to_apply(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "display": {"tool_progress": "off", "show_reasoning": False, "keep_me": "yes"},
                "agent": {"gateway_notify_interval": 0, "other": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "_hermes_home", lambda: hermes_home)

    output = plugin._progresstail_cleanup_alias("")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "Removed legacy global suppression keys" in output
    assert config["display"] == {"keep_me": "yes"}
    assert config["agent"] == {"other": True}


def test_config_cleanup_command_apply_removes_legacy_values(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "display": {"tool_progress": "off", "show_reasoning": False, "keep_me": "yes"},
                "agent": {"gateway_notify_interval": 0, "other": True},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(commands, "_hermes_home", lambda: hermes_home)

    output = plugin._command("config cleanup --apply")

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "Removed legacy global suppression keys" in output
    assert config["display"] == {"keep_me": "yes"}
    assert config["agent"] == {"other": True}
    assert (hermes_home / "hermes-progress-tail" / "backups").exists()


def test_doctor_reports_unknown_and_retired_config_keys(monkeypatch):
    plugin._renderer = None
    config = {
        "display": {"tool_progress": "off", "show_reasoning": False},
        "agent": {"gateway_notify_interval": 0},
        "progress_tail": {
            "enabled": True,
            "tools": {"enabled": True, "typo_lines": 4},
            "renderer": {"code_fence": "on"},
            "telegram": {"collapsible_details": True, "details_open_on_failure": True},
            "background_jobs": {"default_notify_on_complete": False},
            "finalization": {"delete_on_success": True},
            "platforms": {"telegram": {"bogus": "value", "code_fence": "on"}},
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
    assert "warning: retired config key progress_tail.telegram.collapsible_details" in doctor
    assert "warning: retired config key progress_tail.telegram.details_open_on_failure" in doctor
    assert "warning: unknown config key progress_tail.tools.typo_lines" in doctor
    assert "warning: unknown config key progress_tail.renderer.code_fence" in doctor
    assert "warning: unknown config key progress_tail.platforms.telegram.bogus" in doctor
    assert "warning: unknown config key progress_tail.platforms.telegram.code_fence" in doctor


def test_doctor_warns_when_background_job_native_notifications_are_not_suppressed(monkeypatch):
    plugin._renderer = None
    config = {
        "display": {"tool_progress": "off", "show_reasoning": False},
        "agent": {"gateway_notify_interval": 0},
        "progress_tail": {
            "enabled": True,
            "background_jobs": {
                "enabled": True,
                "suppress_native_notify": False,
                "suppress_watch_notifications": False,
                "list_running": False,
            },
        },
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings(config))

    doctor = plugin._command("doctor")

    assert (
        "warning: background_jobs.enabled=true but suppress_native_notify=false; "
        "native process notifications may duplicate progress-tail output"
    ) in doctor
    assert (
        "warning: background_jobs.enabled=true but suppress_watch_notifications=false; "
        "watch pattern notifications may duplicate progress-tail output"
    ) in doctor
    assert (
        "warning: background_jobs.enabled=true but list_running=false; "
        "running jobs will be hidden from /progresstail jobs"
    ) in doctor


def test_doctor_does_not_warn_background_suppression_when_background_jobs_disabled(monkeypatch):
    plugin._renderer = None
    config = {
        "display": {"tool_progress": "off", "show_reasoning": False},
        "agent": {"gateway_notify_interval": 0},
        "progress_tail": {
            "enabled": True,
            "background_jobs": {
                "enabled": False,
                "suppress_native_notify": False,
                "suppress_watch_notifications": False,
                "list_running": False,
            },
        },
    }
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: config)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings(config))

    doctor = plugin._command("doctor")

    assert "background_jobs.enabled=true but" not in doctor


def test_demo_commands_return_sample_progress(monkeypatch):
    plugin._renderer = None
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: {})
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings({}))

    demo = plugin._command("demo failed")

    assert "**Hermes is working**" in demo
    assert "**__Tools__**" in demo
    assert "× terminal: pytest tests/test_renderer.py -q · 2.1s" in demo
    assert "git diff --check" in demo
