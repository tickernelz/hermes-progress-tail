from hermes_progress_tail.hooks.command_menus import (
    _pin_pairs,
    install_command_menu_monkeypatch,
    uninstall_command_menu_monkeypatch,
)


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
    import hermes_cli.commands as commands_mod
    from hermes_cli import plugins as plugins_mod

    uninstall_command_menu_monkeypatch()
    monkeypatch.setattr(
        plugins_mod,
        "get_plugin_commands",
        lambda: {
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
    import hermes_cli.commands as commands_mod
    from hermes_cli import plugins as plugins_mod

    uninstall_command_menu_monkeypatch()
    monkeypatch.setattr(
        plugins_mod,
        "get_plugin_commands",
        lambda: {
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
