from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_COMMAND_MENU_ORIGINALS: dict[str, Any] = {}
_COMMAND_MENU_PATCH_MARKER = "_hermes_progress_tail_command_menu_patched"

_PINNED_TELEGRAM_COMMANDS = (
    "progresstail",
    "progresstail_update",
    "progresstail_doctor",
    "progresstail_jobs",
)

_PINNED_SLACK_COMMANDS = (
    "progresstail",
    "progresstail_update",
    "progresstail_doctor",
    "progresstail_jobs",
)

_COMMAND_FALLBACK_DESCRIPTIONS = {
    "progresstail": "Show progress-tail status",
    "progresstail_update": "Apply progress-tail update",
    "progresstail_doctor": "Diagnose progress-tail config",
    "progresstail_jobs": "Show progress-tail jobs",
    "progresstail_cleanup": "Apply progress-tail cleanup",
    "progresstail_demo": "Show progress-tail demo",
}


def _plugin_command_description(command_name: str) -> str:
    try:
        from hermes_cli.plugins import get_plugin_commands

        meta = (get_plugin_commands() or {}).get(command_name) or {}
        description = str(meta.get("description") or "").strip()
        if description:
            return description
    except Exception:
        pass
    return _COMMAND_FALLBACK_DESCRIPTIONS.get(command_name, "Progress-tail command")


def _menu_pairs(
    command_names: tuple[str, ...],
    *,
    desc_limit: int,
    sanitize_name: Callable[[str], str] | None = None,
) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw_name in command_names:
        name = sanitize_name(raw_name) if sanitize_name else raw_name
        if not name:
            continue
        desc = _plugin_command_description(raw_name)
        if len(desc) > desc_limit:
            desc = desc[: desc_limit - 3] + "..."
        pairs.append((name, desc))
    return pairs


def _pin_pairs(
    base: list[tuple[str, str]],
    pinned: list[tuple[str, str]],
    max_items: int,
) -> tuple[list[tuple[str, str]], int]:
    if max_items <= 0:
        return [], len(base)
    output: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, desc in pinned:
        if not name or name in seen:
            continue
        output.append((name, desc))
        seen.add(name)
    for name, desc in base:
        if not name or name in seen:
            continue
        output.append((name, desc))
        seen.add(name)
    visible = output[:max_items]
    return visible, max(0, len(output) - len(visible))


def _pin_slack_entries(
    base: list[tuple[str, str, str]],
    pinned: list[tuple[str, str, str]],
    max_items: int,
) -> list[tuple[str, str, str]]:
    if max_items <= 0:
        return []
    output: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for name, desc, hint in pinned:
        if not name or name in seen:
            continue
        output.append((name, desc, hint))
        seen.add(name)
    for name, desc, hint in base:
        if not name or name in seen:
            continue
        output.append((name, desc, hint))
        seen.add(name)
    return output[:max_items]


def install_command_menu_monkeypatch() -> bool:
    try:
        import hermes_cli.commands as commands_mod
    except Exception as exc:
        logger.debug("hermes-progress-tail command menu patch unavailable: %s", exc)
        return False
    if getattr(commands_mod, _COMMAND_MENU_PATCH_MARKER, False):
        return True

    original_telegram_menu = getattr(commands_mod, "telegram_menu_commands", None)
    original_slack_native = getattr(commands_mod, "slack_native_slashes", None)
    if not callable(original_telegram_menu):
        return False

    _COMMAND_MENU_ORIGINALS["telegram_menu_commands"] = original_telegram_menu
    if callable(original_slack_native):
        _COMMAND_MENU_ORIGINALS["slack_native_slashes"] = original_slack_native

    def patched_telegram_menu_commands(max_commands: int = 100):
        base_commands, hidden_count = original_telegram_menu(max_commands=max_commands)
        sanitize = getattr(commands_mod, "_sanitize_telegram_name", None)
        pinned = _menu_pairs(
            _PINNED_TELEGRAM_COMMANDS,
            desc_limit=40,
            sanitize_name=sanitize if callable(sanitize) else None,
        )
        visible, dropped_by_pin = _pin_pairs(list(base_commands), pinned, int(max_commands))
        return visible, int(hidden_count or 0) + dropped_by_pin

    commands_mod.telegram_menu_commands = patched_telegram_menu_commands

    if callable(original_slack_native):
        slack_sanitize = getattr(commands_mod, "_sanitize_slack_name", None)
        slack_cap = int(getattr(commands_mod, "_SLACK_MAX_SLASH_COMMANDS", 50) or 50)

        def patched_slack_native_slashes():
            base = list(original_slack_native())
            pairs = _menu_pairs(
                _PINNED_SLACK_COMMANDS,
                desc_limit=140,
                sanitize_name=slack_sanitize if callable(slack_sanitize) else None,
            )
            pinned = [(name, desc, "") for name, desc in pairs]
            return _pin_slack_entries(base, pinned, slack_cap)

        commands_mod.slack_native_slashes = patched_slack_native_slashes

    setattr(commands_mod, _COMMAND_MENU_PATCH_MARKER, True)
    logger.debug("hermes-progress-tail command menu monkeypatch installed")
    return True


def uninstall_command_menu_monkeypatch() -> bool:
    try:
        import hermes_cli.commands as commands_mod
    except Exception:
        return False
    changed = False
    original = _COMMAND_MENU_ORIGINALS.pop("telegram_menu_commands", None)
    if original is not None:
        commands_mod.telegram_menu_commands = original
        changed = True
    original = _COMMAND_MENU_ORIGINALS.pop("slack_native_slashes", None)
    if original is not None:
        commands_mod.slack_native_slashes = original
        changed = True
    if changed:
        setattr(commands_mod, _COMMAND_MENU_PATCH_MARKER, False)
    return changed


def command_menu_monkeypatch_active() -> bool:
    try:
        import hermes_cli.commands as commands_mod

        return bool(getattr(commands_mod, _COMMAND_MENU_PATCH_MARKER, False))
    except Exception:
        return False
