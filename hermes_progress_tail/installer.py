from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PLUGIN_NAME = "hermes-progress-tail"
LEGACY_PLUGIN_NAME = "tool-progress-tail"
DEFAULT_CONFIG = {
    "enabled": True,
    "tools": {
        "enabled": True,
        "lines": 3,
        "preview_length": 120,
        "show_completed": False,
    },
    "reasoning": {
        "enabled": True,
        "max_lines": 3,
        "max_chars": 600,
        "min_update_chars": 80,
        "no_edit_strategy": "off",
        "capture_inline_think_tags": False,
    },
    "renderer": {
        "strategy": "auto",
        "edit_interval": 1.5,
        "stale_ttl_seconds": 900,
        "redact_secrets": True,
        "mode": "sectioned",
    },
    "no_edit": {
        "interval_seconds": 30,
        "min_new_events": 3,
        "final_summary": True,
        "max_snapshots_per_turn": 5,
    },
}


@dataclass
class InstallResult:
    changed: bool
    messages: list[str] = field(default_factory=list)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _backup_config(hermes_home: Path) -> Path | None:
    config = hermes_home / "config.yaml"
    if not config.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = hermes_home / PLUGIN_NAME / "backups" / stamp / "config.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config, dest)
    return dest


def _copy_plugin(source_dir: Path, target_dir: Path) -> None:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    ignore = shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache", "*.pyc")
    shutil.copytree(source_dir, target_dir, ignore=ignore)


def _migrate_legacy_config(config: dict[str, Any]) -> bool:
    changed = False
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else None
    enabled = (
        plugins.get("enabled")
        if isinstance(plugins, dict) and isinstance(plugins.get("enabled"), list)
        else None
    )
    if enabled is not None and LEGACY_PLUGIN_NAME in enabled:
        enabled[:] = [PLUGIN_NAME if item == LEGACY_PLUGIN_NAME else item for item in enabled]
        changed = True
    legacy = config.pop("tool_progress_tail", None)
    if isinstance(legacy, dict) and "progress_tail" not in config:
        defaults = legacy.get("defaults") if isinstance(legacy.get("defaults"), dict) else {}
        config["progress_tail"] = {
            "enabled": legacy.get("enabled", True),
            "tools": {
                "enabled": True,
                "lines": defaults.get("lines", 3),
                "preview_length": defaults.get("preview_length", 120),
                "show_completed": defaults.get("show_completed", False),
            },
            "reasoning": DEFAULT_CONFIG["reasoning"].copy(),
            "renderer": {
                "strategy": "auto",
                "edit_interval": defaults.get("edit_interval", 1.5),
                "stale_ttl_seconds": defaults.get("stale_ttl_seconds", 900),
                "redact_secrets": defaults.get("redact_secrets", True),
                "mode": "sectioned",
            },
            "no_edit": legacy.get("no_edit", DEFAULT_CONFIG["no_edit"]).copy()
            if isinstance(legacy.get("no_edit"), dict)
            else DEFAULT_CONFIG["no_edit"].copy(),
            "platforms": legacy.get("platforms", {}).copy()
            if isinstance(legacy.get("platforms"), dict)
            else {},
        }
        changed = True
    elif legacy is not None:
        changed = True
    return changed


def _reasoning_tail_enabled(config: dict[str, Any]) -> bool:
    section = config.get("progress_tail")
    if not isinstance(section, dict):
        return True
    if section.get("enabled") is False:
        return False
    reasoning = section.get("reasoning")
    if not isinstance(reasoning, dict):
        return True
    return reasoning.get("enabled") is not False


def _update_config(config: dict[str, Any], set_display_off: bool) -> tuple[dict[str, Any], bool]:
    changed = _migrate_legacy_config(config)
    plugins = config.setdefault("plugins", {})
    if not isinstance(plugins, dict):
        config["plugins"] = plugins = {}
        changed = True
    enabled = plugins.setdefault("enabled", [])
    if not isinstance(enabled, list):
        enabled = []
        plugins["enabled"] = enabled
        changed = True
    if LEGACY_PLUGIN_NAME in enabled:
        enabled[:] = [item for item in enabled if item != LEGACY_PLUGIN_NAME]
        changed = True
    if PLUGIN_NAME not in enabled:
        enabled.append(PLUGIN_NAME)
        changed = True
    if "progress_tail" not in config or not isinstance(config.get("progress_tail"), dict):
        config["progress_tail"] = DEFAULT_CONFIG.copy()
        changed = True
    if set_display_off:
        display = config.setdefault("display", {})
        if not isinstance(display, dict):
            config["display"] = display = {}
            changed = True
        if display.get("tool_progress") != "off":
            display["tool_progress"] = "off"
            changed = True
        if _reasoning_tail_enabled(config) and display.get("show_reasoning") is not False:
            display["show_reasoning"] = False
            changed = True
    return config, changed


def install(
    hermes_home: Path,
    source_dir: Path | None = None,
    *,
    set_display_off: bool = False,
    dry_run: bool = False,
) -> InstallResult:
    hermes_home = Path(hermes_home).expanduser().resolve()
    source_dir = Path(source_dir or Path(__file__).resolve().parents[1]).resolve()
    target_dir = hermes_home / "plugins" / PLUGIN_NAME
    legacy_dir = hermes_home / "plugins" / LEGACY_PLUGIN_NAME
    config_path = hermes_home / "config.yaml"
    config = _read_yaml(config_path)
    updated, config_changed = _update_config(config, set_display_off=set_display_off)
    plugin_changed = not target_dir.exists() or legacy_dir.exists()
    result = InstallResult(changed=config_changed or plugin_changed)
    if dry_run:
        result.messages.append(f"Would copy plugin to {target_dir}")
        if legacy_dir.exists():
            result.messages.append(f"Would remove legacy plugin {legacy_dir}")
        result.messages.append(f"Would update {config_path}")
        return result
    hermes_home.mkdir(parents=True, exist_ok=True)
    backup = _backup_config(hermes_home)
    if backup:
        result.messages.append(f"Backed up config to {backup}")
    if legacy_dir.exists():
        shutil.rmtree(legacy_dir)
        result.messages.append(f"Removed legacy plugin {legacy_dir}")
    _copy_plugin(source_dir, target_dir)
    _write_yaml(config_path, updated)
    result.messages.append(f"Installed plugin to {target_dir}")
    result.messages.append("Restart Hermes gateway for changes to take effect")
    return result


def uninstall(hermes_home: Path, *, dry_run: bool = False) -> InstallResult:
    hermes_home = Path(hermes_home).expanduser().resolve()
    target_dir = hermes_home / "plugins" / PLUGIN_NAME
    legacy_dir = hermes_home / "plugins" / LEGACY_PLUGIN_NAME
    config_path = hermes_home / "config.yaml"
    config = _read_yaml(config_path)
    changed = target_dir.exists() or legacy_dir.exists()
    plugins = config.get("plugins") if isinstance(config.get("plugins"), dict) else {}
    enabled = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    for name in (PLUGIN_NAME, LEGACY_PLUGIN_NAME):
        if name in enabled:
            enabled.remove(name)
            changed = True
    result = InstallResult(changed=changed)
    if dry_run:
        result.messages.append(f"Would remove {target_dir}")
        if legacy_dir.exists():
            result.messages.append(f"Would remove {legacy_dir}")
        result.messages.append(f"Would update {config_path}")
        return result
    backup = _backup_config(hermes_home)
    if backup:
        result.messages.append(f"Backed up config to {backup}")
    for directory in (target_dir, legacy_dir):
        if directory.exists():
            shutil.rmtree(directory)
    if plugins:
        plugins["enabled"] = enabled
        config["plugins"] = plugins
        _write_yaml(config_path, config)
    result.messages.append(f"Uninstalled {PLUGIN_NAME}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes_progress_tail")
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--hermes-home", default=os.getenv("HERMES_HOME", "~/.hermes"))
    parser.add_argument("--source-dir", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--set-display-off", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "install":
        result = install(
            Path(args.hermes_home),
            Path(args.source_dir),
            set_display_off=args.set_display_off,
            dry_run=args.dry_run,
        )
    else:
        result = uninstall(Path(args.hermes_home), dry_run=args.dry_run)
    for msg in result.messages:
        print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
