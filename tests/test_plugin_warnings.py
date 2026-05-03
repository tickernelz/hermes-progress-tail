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

    assert "display.show_reasoning=true" in status


def test_register_logs_warning_once_for_reasoning_conflict(monkeypatch, caplog):
    plugin._renderer = None
    config = {
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
