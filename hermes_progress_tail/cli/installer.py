from __future__ import annotations

import copy
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .profiles import _resolve_profile_targets

PLUGIN_NAME = "hermes-progress-tail"
LEGACY_PLUGIN_NAME = "tool-progress-tail"
DEFAULT_CONFIG = {
    "enabled": True,
    "tools": {
        "enabled": True,
        "lines": 3,
        "preview_length": 120,
        "show_completed": True,
        "show_duration": True,
        "timestamp": True,
        "timestamp_format": "%H:%M",
    },
    "delegates": {
        "enabled": True,
        "max_delegates": 4,
        "lines_per_delegate": 2,
        "max_goal_chars": 48,
        "max_line_chars": 120,
        "show_model": False,
        "show_tool_count": True,
        "show_completion": True,
        "completed_ttl_seconds": 5,
        "thinking": "off",
    },
    "todo": {
        "sticky": True,
        "hide_tool_line": True,
        "max_pending": 3,
        "max_completed": 3,
        "max_cancelled": 2,
        "max_item_chars": 40,
    },
    "patch": {
        "detail": "smart",
        "preview_chars": 48,
        "max_files": 3,
    },
    "assistant": {
        "enabled": True,
        "max_lines": 3,
        "max_chars": 500,
        "min_update_chars": 40,
    },
    "reasoning": {
        "enabled": True,
        "max_lines": 3,
        "max_chars": 600,
        "min_update_chars": 80,
        "no_edit_strategy": "off",
    },
    "background_jobs": {
        "enabled": True,
        "list_running": True,
        "show_completed": True,
        "completed_ttl_seconds": 5,
        "max_jobs": 4,
        "head_lines": 2,
        "tail_lines": 3,
        "max_line_chars": 120,
        "update_interval_seconds": 3,
        "suppress_native_notify": True,
        "suppress_watch_notifications": True,
    },
    "cleanup": {
        "auto_delete": True,
        "delay_seconds": 5,
        "delete_on_success": True,
        "delete_on_failure": False,
        "delete_background_active": False,
    },
    "footer": {
        "enabled": True,
        "density": "normal",
        "max_path_chars": 56,
    },
    "renderer": {
        "strategy": "auto",
        "edit_interval": 1.5,
        "stale_ttl_seconds": 900,
        "redact_secrets": True,
        "mode": "focused",
        "style": "emoji",
        "density": "verbose",
        "agent_label": "",
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
    if not (target_dir / "plugin.yaml").exists() and _is_package_source_dir(source_dir):
        (target_dir / "plugin.yaml").write_text(_generated_plugin_yaml(), encoding="utf-8")


def _is_plugin_source_dir(path: Path) -> bool:
    return (path / "plugin.yaml").exists() and (path / "hermes_progress_tail").is_dir()


def _is_package_source_dir(path: Path) -> bool:
    return (
        (path / "__init__.py").exists()
        and (path / "runtime" / "plugin.py").exists()
        and (path / "rendering" / "renderer.py").exists()
    )


def _generated_plugin_yaml() -> str:
    from ..runtime.plugin import VERSION

    return (
        f"name: {PLUGIN_NAME}\n"
        f"version: {VERSION}\n"
        "description: Compact tool and reasoning progress tail for Hermes gateway platforms\n"
        "kind: standalone\n"
        "provides_hooks:\n"
        "  - pre_gateway_dispatch\n"
        "  - pre_tool_call\n"
        "  - post_tool_call\n"
        "  - post_llm_call\n"
        "  - on_session_reset\n"
        "  - on_session_finalize\n"
    )


def _default_source_dir() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if _is_plugin_source_dir(parent):
            return parent
    for parent in current.parents:
        if _is_package_source_dir(parent):
            return parent
    raise FileNotFoundError("could not locate hermes-progress-tail plugin source directory")


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
                "show_completed": defaults.get("show_completed", True),
                "show_duration": defaults.get("show_duration", True),
                "timestamp": defaults.get("timestamp", True),
                "timestamp_format": defaults.get("timestamp_format", "%H:%M"),
            },
            "delegates": copy.deepcopy(DEFAULT_CONFIG["delegates"]),
            "assistant": copy.deepcopy(DEFAULT_CONFIG["assistant"]),
            "reasoning": copy.deepcopy(DEFAULT_CONFIG["reasoning"]),
            "background_jobs": copy.deepcopy(DEFAULT_CONFIG["background_jobs"]),
            "renderer": {
                "strategy": "auto",
                "edit_interval": defaults.get("edit_interval", 1.5),
                "stale_ttl_seconds": defaults.get("stale_ttl_seconds", 900),
                "redact_secrets": defaults.get("redact_secrets", True),
                "mode": "focused",
                "style": "emoji",
                "density": "verbose",
                "agent_label": "",
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


def _feature_enabled(config: dict[str, Any], name: str, default: bool = True) -> bool:
    section = config.get("progress_tail")
    if not isinstance(section, dict):
        return default
    if section.get("enabled") is False:
        return False
    feature = section.get(name)
    if not isinstance(feature, dict):
        return default
    return feature.get("enabled") is not False


def _assistant_tail_enabled(config: dict[str, Any]) -> bool:
    return _feature_enabled(config, "assistant", True)


def _reasoning_tail_enabled(config: dict[str, Any]) -> bool:
    return _feature_enabled(config, "reasoning", True)


def _progress_tail_enabled(config: dict[str, Any]) -> bool:
    section = config.get("progress_tail")
    return not (isinstance(section, dict) and section.get("enabled") is False)


def _builtin_reasoning_conflict(config: dict[str, Any]) -> bool:
    display = config.get("display")
    if not isinstance(display, dict):
        return False
    return _reasoning_tail_enabled(config) and display.get("show_reasoning") is True


def _core_notifier_conflict(config: dict[str, Any]) -> bool:
    if not _progress_tail_enabled(config):
        return False
    agent = config.get("agent")
    if not isinstance(agent, dict):
        return True
    value = agent.get("gateway_notify_interval", 180)
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return True


def _prune_removed_defaults(target: dict[str, Any]) -> list[str]:
    removed = []
    finalization = target.get("finalization")
    if isinstance(finalization, dict):
        target.pop("finalization", None)
        removed.append("progress_tail.finalization")
    background_jobs = target.get("background_jobs")
    if isinstance(background_jobs, dict) and "default_notify_on_complete" in background_jobs:
        background_jobs.pop("default_notify_on_complete", None)
        removed.append("progress_tail.background_jobs.default_notify_on_complete")
    return removed


def _merge_missing_defaults(
    target: dict[str, Any], defaults: dict[str, Any], prefix: str = ""
) -> list[str]:
    added = []
    for key, value in defaults.items():
        path = f"{prefix}.{key}" if prefix else key
        if key not in target:
            target[key] = copy.deepcopy(value)
            added.append(path)
            continue
        if isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                continue
            added.extend(_merge_missing_defaults(target[key], value, path))
    return added


def _apply_config_overrides(
    target: dict[str, Any], overrides: dict[str, Any], prefix: str = ""
) -> bool:
    changed = False
    for key, value in overrides.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            current = target.get(key)
            if not isinstance(current, dict):
                target[key] = {}
                current = target[key]
                changed = True
            changed = _apply_config_overrides(current, value, path) or changed
            continue
        if target.get(key) != value:
            target[key] = copy.deepcopy(value)
            changed = True
    return changed


def _update_config(
    config: dict[str, Any],
    set_display_off: bool,
    feature_overrides: dict[str, Any] | None = None,
    *,
    force_default_config: bool = False,
) -> tuple[dict[str, Any], bool, list[str]]:
    changed = _migrate_legacy_config(config)
    added_defaults: list[str] = []
    removed_defaults: list[str] = []
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
    if force_default_config:
        if config.get("progress_tail") != DEFAULT_CONFIG:
            config["progress_tail"] = copy.deepcopy(DEFAULT_CONFIG)
            changed = True
            added_defaults.append("progress_tail")
    elif "progress_tail" not in config or not isinstance(config.get("progress_tail"), dict):
        config["progress_tail"] = copy.deepcopy(DEFAULT_CONFIG)
        added_defaults.append("progress_tail")
        changed = True
    else:
        added_defaults.extend(
            f"progress_tail.{path}"
            for path in _merge_missing_defaults(config["progress_tail"], DEFAULT_CONFIG)
        )
        removed_defaults.extend(_prune_removed_defaults(config["progress_tail"]))
        changed = changed or bool(added_defaults) or bool(removed_defaults)
    if feature_overrides:
        changed = _apply_config_overrides(config["progress_tail"], feature_overrides) or changed
    if removed_defaults:
        added_defaults.extend(f"removed:{path}" for path in removed_defaults)
    if set_display_off:
        display = config.setdefault("display", {})
        if not isinstance(display, dict):
            config["display"] = display = {}
            changed = True
        if display.get("tool_progress") != "off":
            display["tool_progress"] = "off"
            changed = True
        if display.get("streaming") is not False:
            display["streaming"] = False
            changed = True
        if (
            _assistant_tail_enabled(config)
            and display.get("interim_assistant_messages") is not False
        ):
            display["interim_assistant_messages"] = False
            changed = True
        if _reasoning_tail_enabled(config) and display.get("show_reasoning") is not False:
            display["show_reasoning"] = False
            changed = True
        streaming = config.setdefault("streaming", {})
        if not isinstance(streaming, dict):
            config["streaming"] = streaming = {}
            changed = True
        if streaming.get("enabled") is not False:
            streaming["enabled"] = False
            changed = True
        agent = config.setdefault("agent", {})
        if not isinstance(agent, dict):
            config["agent"] = agent = {}
            changed = True
        if agent.get("gateway_notify_interval") != 0:
            agent["gateway_notify_interval"] = 0
            changed = True
    return config, changed, added_defaults


def install(
    hermes_home: Path,
    source_dir: Path | None = None,
    *,
    set_display_off: bool = False,
    dry_run: bool = False,
    feature_overrides: dict[str, Any] | None = None,
    force_default_config: bool = False,
) -> InstallResult:
    hermes_home = Path(hermes_home).expanduser().resolve()
    source_dir = Path(source_dir or _default_source_dir()).resolve()
    target_dir = hermes_home / "plugins" / PLUGIN_NAME
    legacy_dir = hermes_home / "plugins" / LEGACY_PLUGIN_NAME
    config_path = hermes_home / "config.yaml"
    config = _read_yaml(config_path)
    had_builtin_reasoning_conflict = _builtin_reasoning_conflict(config)
    had_core_notifier_conflict = _core_notifier_conflict(config)
    updated, _config_changed, added_defaults = _update_config(
        config,
        set_display_off=set_display_off,
        feature_overrides=feature_overrides,
        force_default_config=force_default_config,
    )
    has_builtin_reasoning_conflict = _builtin_reasoning_conflict(updated)
    has_core_notifier_conflict = _core_notifier_conflict(updated)
    result = InstallResult(changed=True)
    if had_builtin_reasoning_conflict or has_builtin_reasoning_conflict:
        result.messages.append(
            "warning: display.show_reasoning=true while progress_tail.reasoning.enabled=true; "
            "duplicate reasoning/final output may occur"
        )
    if had_core_notifier_conflict or has_core_notifier_conflict:
        result.messages.append(
            "warning: agent.gateway_notify_interval is enabled while progress_tail.enabled=true; "
            "core Still working notifications use send() and can duplicate progress"
        )
    if added_defaults:
        added = [item for item in added_defaults if not item.startswith("removed:")]
        removed = [
            item.removeprefix("removed:") for item in added_defaults if item.startswith("removed:")
        ]
        if added:
            result.messages.append("Added missing default config keys: " + ", ".join(added))
        if removed:
            result.messages.append("Removed retired config keys: " + ", ".join(removed))
    action = "Updated" if target_dir.exists() else "Installed"
    if dry_run:
        result.messages.append(f"Would copy plugin to {target_dir}")
        if target_dir.exists():
            result.messages.append(f"Would update existing plugin {target_dir}")
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
    result.messages.append(f"{action} plugin at {target_dir}")
    result.messages.append("Restart Hermes gateway for changes to take effect")
    return result


def install_many(
    hermes_home: Path,
    source_dir: Path | None = None,
    *,
    profiles: list[str] | None = None,
    all_profiles: bool = False,
    set_display_off: bool = False,
    dry_run: bool = False,
    feature_overrides: dict[str, Any] | None = None,
    force_default_config: bool = False,
) -> InstallResult:
    messages: list[str] = []
    changed = False
    for name, home in _resolve_profile_targets(hermes_home, profiles, all_profiles=all_profiles):
        result = install(
            home,
            source_dir,
            set_display_off=set_display_off,
            dry_run=dry_run,
            feature_overrides=feature_overrides,
            force_default_config=force_default_config,
        )
        changed = changed or result.changed
        messages.append(f"[{name}] {home}")
        messages.extend(f"[{name}] {message}" for message in result.messages)
    return InstallResult(changed=changed, messages=messages)


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


def uninstall_many(
    hermes_home: Path,
    *,
    profiles: list[str] | None = None,
    all_profiles: bool = False,
    dry_run: bool = False,
) -> InstallResult:
    messages: list[str] = []
    changed = False
    for name, home in _resolve_profile_targets(hermes_home, profiles, all_profiles=all_profiles):
        result = uninstall(home, dry_run=dry_run)
        changed = changed or result.changed
        messages.append(f"[{name}] {home}")
        messages.extend(f"[{name}] {message}" for message in result.messages)
    return InstallResult(changed=changed, messages=messages)
