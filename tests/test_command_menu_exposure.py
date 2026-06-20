import sys
import types

from hermes_progress_tail.hooks.command_menus import (
    _pin_pairs,
    install_command_menu_monkeypatch,
    uninstall_command_menu_monkeypatch,
)


def _install_fake_hermes_cli(monkeypatch, plugin_commands):
    hermes_cli = types.ModuleType("hermes_cli")
    commands_mod = types.ModuleType("hermes_cli.commands")
    plugins_mod = types.ModuleType("hermes_cli.plugins")

    def telegram_menu_commands(max_commands=100):
        commands = [(f"builtin_{index}", f"Builtin {index}") for index in range(10)]
        visible = commands[:max_commands]
        return visible, max(0, len(commands) - len(visible))

    def slack_native_slashes():
        return [(f"builtin_{index}", f"Builtin {index}", "") for index in range(10)]

    commands_mod.telegram_menu_commands = telegram_menu_commands
    commands_mod.slack_native_slashes = slack_native_slashes
    commands_mod._SLACK_MAX_SLASH_COMMANDS = 50
    commands_mod._sanitize_telegram_name = lambda name: name
    commands_mod._sanitize_slack_name = lambda name: name
    plugins_mod.get_plugin_commands = lambda: plugin_commands

    hermes_cli.commands = commands_mod
    hermes_cli.plugins = plugins_mod
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.commands", commands_mod)
    monkeypatch.setitem(sys.modules, "hermes_cli.plugins", plugins_mod)
    return commands_mod


def test_pin_pairs_promotes_progress_tail_commands_inside_platform_cap():
    base = [("help", "Help"), ("status", "Status"), ("model", "Model")]
    pinned = [("progresstail", "Progress tail"), ("progresstail_update", "Update")]

    visible, dropped = _pin_pairs(base, pinned, 3)

    assert visible == [
        ("progresstail", "Progress tail"),
        ("progresstail_update", "Update"),
        ("help", "Help"),
    ]
    assert dropped == 2


def test_command_menu_patch_pins_progress_tail_in_telegram_cap(monkeypatch):
    uninstall_command_menu_monkeypatch()
    commands_mod = _install_fake_hermes_cli(
        monkeypatch,
        {
            "progresstail": {
                "handler": lambda _args: "ok",
                "description": "Show progress-tail status",
                "args_hint": "status|doctor|jobs",
                "plugin": "hermes-progress-tail",
            },
            "progresstail_update": {
                "handler": lambda _args: "ok",
                "description": "Apply progress-tail update",
                "args_hint": "",
                "plugin": "hermes-progress-tail",
            },
            "progresstail_doctor": {
                "handler": lambda _args: "ok",
                "description": "Diagnose progress-tail config",
                "args_hint": "",
                "plugin": "hermes-progress-tail",
            },
            "progresstail_jobs": {
                "handler": lambda _args: "ok",
                "description": "Show progress-tail jobs",
                "args_hint": "",
                "plugin": "hermes-progress-tail",
            },
        },
    )

    try:
        assert install_command_menu_monkeypatch() is True
        menu, _hidden = commands_mod.telegram_menu_commands(max_commands=3)
        assert [name for name, _desc in menu] == [
            "progresstail",
            "progresstail_update",
            "progresstail_doctor",
        ]
    finally:
        uninstall_command_menu_monkeypatch()


def test_command_menu_patch_pins_progress_tail_in_slack_native_cap(monkeypatch):
    uninstall_command_menu_monkeypatch()
    commands_mod = _install_fake_hermes_cli(
        monkeypatch,
        {
            "progresstail": {
                "handler": lambda _args: "ok",
                "description": "Show progress-tail status",
                "args_hint": "status|doctor|jobs",
                "plugin": "hermes-progress-tail",
            },
            "progresstail_update": {
                "handler": lambda _args: "ok",
                "description": "Apply progress-tail update",
                "args_hint": "",
                "plugin": "hermes-progress-tail",
            },
        },
    )

    try:
        assert install_command_menu_monkeypatch() is True
        names = [name for name, _desc, _hint in commands_mod.slack_native_slashes()[:2]]
        assert names == ["progresstail", "progresstail_update"]
    finally:
        uninstall_command_menu_monkeypatch()
