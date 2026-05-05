from __future__ import annotations

import argparse
import copy
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
        "completed_ttl_seconds": 180,
        "max_jobs": 4,
        "head_lines": 2,
        "tail_lines": 3,
        "max_line_chars": 120,
        "update_interval_seconds": 3,
        "suppress_native_notify": True,
        "suppress_watch_notifications": True,
        "default_notify_on_complete": False,
    },
    "renderer": {
        "strategy": "auto",
        "edit_interval": 1.5,
        "stale_ttl_seconds": 900,
        "redact_secrets": True,
        "mode": "sectioned",
        "style": "emoji",
        "density": "normal",
        "code_fence": "auto",
        "code_fence_language": "",
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


def _discover_profile_names(hermes_home: Path) -> list[str]:
    profiles_dir = hermes_home / "profiles"
    if not profiles_dir.exists():
        return []
    names = []
    for path in sorted(profiles_dir.iterdir()):
        if not path.is_dir():
            continue
        if (path / "config.yaml").exists() or (path / "plugins").exists():
            names.append(path.name)
    return names


def _resolve_profile_targets(
    hermes_home: Path,
    profiles: list[str] | None = None,
    *,
    all_profiles: bool = False,
) -> list[tuple[str, Path]]:
    hermes_home = Path(hermes_home).expanduser().resolve()
    discovered = _discover_profile_names(hermes_home)
    known = {
        "default": hermes_home,
        **{name: hermes_home / "profiles" / name for name in discovered},
    }
    requested = ["default", *discovered] if all_profiles else (profiles or ["default"])
    targets: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw in requested:
        name = str(raw or "").strip()
        if not name:
            continue
        if name in {"base", "main"}:
            name = "default"
        if name not in known:
            available = ", ".join(known) or "default"
            raise ValueError(f"unknown Hermes profile '{name}'. Available profiles: {available}")
        if name in seen:
            continue
        seen.add(name)
        targets.append((name, known[name]))
    return targets or [("default", hermes_home)]


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
            "reasoning": copy.deepcopy(DEFAULT_CONFIG["reasoning"]),
            "background_jobs": copy.deepcopy(DEFAULT_CONFIG["background_jobs"]),
            "renderer": {
                "strategy": "auto",
                "edit_interval": defaults.get("edit_interval", 1.5),
                "stale_ttl_seconds": defaults.get("stale_ttl_seconds", 900),
                "redact_secrets": defaults.get("redact_secrets", True),
                "mode": "sectioned",
                "style": "emoji",
                "density": "normal",
                "code_fence": "auto",
                "code_fence_language": "",
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
        changed = changed or bool(added_defaults)
    if feature_overrides:
        changed = _apply_config_overrides(config["progress_tail"], feature_overrides) or changed
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
        result.messages.append("Added missing default config keys: " + ", ".join(added_defaults))
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


def _parse_profile_list(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    profiles: list[str] = []
    for value in values:
        profiles.extend(item.strip() for item in value.split(",") if item.strip())
    return profiles or None


def _prompt(input_stream: Any, prompt: str) -> str:
    print(prompt, end="", flush=True)
    line = input_stream.readline()
    if line == "":
        raise EOFError("interactive input ended unexpectedly")
    return line.strip()


def _confirm(prompt: str, default: bool = True, input_stream: Any = sys.stdin) -> bool:
    suffix = "Y/n" if default else "y/N"
    answer = _prompt(input_stream, f"{prompt} [{suffix}]: ").lower()
    if not answer:
        return default
    return answer in {"y", "yes", "1", "true", "on"}


def _prompt_int(
    prompt: str, default: int, input_stream: Any = sys.stdin, *, min_value: int = 1
) -> int:
    answer = _prompt(input_stream, f"{prompt} [{default}]: ")
    if not answer:
        return default
    try:
        value = int(answer)
    except ValueError as exc:
        raise ValueError(f"invalid integer for {prompt!r}: {answer}") from exc
    if value < min_value:
        raise ValueError(f"{prompt!r} must be >= {min_value}")
    return value


def _prompt_float(
    prompt: str, default: float, input_stream: Any = sys.stdin, *, min_value: float = 0.0
) -> float:
    answer = _prompt(input_stream, f"{prompt} [{default:g}]: ")
    if not answer:
        return default
    try:
        value = float(answer)
    except ValueError as exc:
        raise ValueError(f"invalid number for {prompt!r}: {answer}") from exc
    if value <= min_value:
        raise ValueError(f"{prompt!r} must be > {min_value:g}")
    return value


def _prompt_choice(
    prompt: str, choices: tuple[str, ...], default: str, input_stream: Any = sys.stdin
) -> str:
    answer = _prompt(input_stream, f"{prompt} ({'|'.join(choices)}) [{default}]: ").strip().lower()
    if not answer:
        return default
    if answer not in choices:
        raise ValueError(
            f"invalid choice for {prompt!r}: {answer}. Expected one of: {', '.join(choices)}"
        )
    return answer


def _prompt_setup_mode(input_stream: Any = sys.stdin) -> str:
    answer = (
        _prompt(
            input_stream,
            "Setup mode (default|simple|advance/advanced) [default]: ",
        )
        .strip()
        .lower()
    )
    if not answer:
        return "default"
    aliases = {
        "d": "default",
        "s": "simple",
        "a": "advance",
        "adv": "advance",
        "advanced": "advance",
    }
    answer = aliases.get(answer, answer)
    if answer not in {"default", "simple", "advance"}:
        raise ValueError(
            "invalid choice for 'Setup mode': "
            f"{answer}. Expected one of: default, simple, advance, advanced"
        )
    return answer


def _select_profiles_interactive(
    hermes_home: Path, input_stream: Any = sys.stdin, *, action: str = "install"
) -> tuple[list[str] | None, bool]:
    discovered = _discover_profile_names(hermes_home)
    if not discovered:
        print("No Hermes profiles found; installing to default only.")
        return ["default"], False
    print("Available targets:")
    print("  0) default")
    for idx, name in enumerate(discovered, start=1):
        print(f"  {idx}) {name}")
    print("  a) all")
    raw = _prompt(
        input_stream,
        f"{action.title()} target profiles (comma-separated numbers/names, default: all): ",
    )
    if not raw or raw.lower() in {"a", "all"}:
        return None, True
    selected: list[str] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        if token.isdigit():
            idx = int(token)
            if idx == 0:
                selected.append("default")
            elif 1 <= idx <= len(discovered):
                selected.append(discovered[idx - 1])
            else:
                raise ValueError(f"invalid profile selection index: {token}")
        else:
            selected.append("default" if token in {"base", "main"} else token)
    return selected or ["default"], False


def _simple_install_overrides(input_stream: Any = sys.stdin) -> dict[str, Any]:
    print("\nSimple setup")
    return {
        "tools": {"enabled": _confirm("Enable tool progress tail", True, input_stream)},
        "delegates": {
            "enabled": _confirm("Enable delegate_task/subagent progress", True, input_stream)
        },
        "todo": {"sticky": _confirm("Enable sticky todo section", True, input_stream)},
        "reasoning": {"enabled": _confirm("Enable reasoning/thinking tail", True, input_stream)},
        "renderer": {
            "style": _prompt_choice("Renderer style", ("emoji", "plain"), "emoji", input_stream),
            "density": _prompt_choice(
                "Renderer density", ("compact", "normal", "debug"), "normal", input_stream
            ),
        },
    }


def _advanced_install_overrides(input_stream: Any = sys.stdin) -> dict[str, Any]:
    print("\nTool progress")
    tools = {
        "enabled": _confirm("Enable tool progress tail", True, input_stream),
        "lines": _prompt_int("Latest tool lines to keep", 3, input_stream),
        "preview_length": _prompt_int(
            "Tool preview max characters", 120, input_stream, min_value=24
        ),
        "show_completed": _confirm(
            "Show completion status by replacing running tool lines", True, input_stream
        ),
        "show_duration": _confirm(
            "Show tool duration on completed/failed lines", True, input_stream
        ),
        "timestamp": _confirm("Show compact timestamps on tool lines", True, input_stream),
        "timestamp_format": "%H:%M",
    }

    print("\nDelegate/subagent progress")
    delegates = {
        "enabled": _confirm("Enable delegate_task/subagent progress", True, input_stream),
        "max_delegates": _prompt_int("Maximum visible delegates", 4, input_stream),
        "lines_per_delegate": _prompt_int("Timeline lines per delegate", 2, input_stream),
        "max_goal_chars": _prompt_int(
            "Delegate title max characters", 48, input_stream, min_value=12
        ),
        "max_line_chars": _prompt_int(
            "Delegate line max characters", 120, input_stream, min_value=24
        ),
        "show_model": _confirm("Show delegate model names", False, input_stream),
        "show_tool_count": _confirm("Show delegate tool count", True, input_stream),
        "show_completion": _confirm("Show delegate completion summary", True, input_stream),
        "thinking": _prompt_choice(
            "Delegate thinking display", ("off", "summary"), "off", input_stream
        ),
    }

    print("\nSticky Todo section")
    todo = {
        "sticky": _confirm("Enable sticky todo section", True, input_stream),
        "hide_tool_line": _confirm("Hide duplicate todo tool line", True, input_stream),
        "max_pending": _prompt_int("Maximum pending todo items shown", 3, input_stream),
        "max_completed": _prompt_int("Maximum completed todo items shown", 3, input_stream),
        "max_cancelled": _prompt_int("Maximum cancelled todo items shown", 2, input_stream),
        "max_item_chars": _prompt_int("Todo item max characters", 40, input_stream, min_value=10),
    }

    print("\nReasoning/thinking tail")
    reasoning = {
        "enabled": _confirm("Enable reasoning/thinking tail", True, input_stream),
        "max_lines": _prompt_int("Reasoning max lines", 3, input_stream),
        "max_chars": _prompt_int("Reasoning max characters", 600, input_stream, min_value=80),
        "min_update_chars": _prompt_int(
            "Reasoning minimum new characters before edit", 80, input_stream
        ),
        "no_edit_strategy": _prompt_choice(
            "Reasoning behavior on no-edit platforms",
            ("auto", "live_tail", "snapshot", "summary_only", "off"),
            "off",
            input_stream,
        ),
    }

    print("\nPatch formatter")
    patch = {
        "detail": _prompt_choice(
            "Patch detail mode", ("off", "path", "smart", "stats"), "smart", input_stream
        ),
        "preview_chars": _prompt_int(
            "Patch preview max characters", 48, input_stream, min_value=10
        ),
        "max_files": _prompt_int("Maximum patch files in summary", 3, input_stream),
    }

    print("\nRenderer")
    renderer = {
        "strategy": _prompt_choice(
            "Renderer update strategy",
            ("auto", "live_tail", "snapshot", "summary_only", "off"),
            "auto",
            input_stream,
        ),
        "mode": _prompt_choice(
            "Renderer layout mode", ("sectioned", "compact"), "sectioned", input_stream
        ),
        "style": _prompt_choice("Renderer style", ("emoji", "plain"), "emoji", input_stream),
        "density": _prompt_choice(
            "Renderer density", ("compact", "normal", "debug"), "normal", input_stream
        ),
        "edit_interval": _prompt_float("Minimum seconds between live edits", 1.5, input_stream),
        "stale_ttl_seconds": _prompt_int("Stale session TTL seconds", 900, input_stream),
        "redact_secrets": _confirm("Redact common secrets before rendering", True, input_stream),
        "code_fence": "auto",
        "code_fence_language": "",
    }

    print("\nNo-edit platform snapshots")
    no_edit = {
        "interval_seconds": _prompt_int("Snapshot interval seconds", 30, input_stream),
        "min_new_events": _prompt_int("Minimum new events before snapshot", 3, input_stream),
        "final_summary": _confirm("Send final snapshot summary", True, input_stream),
        "max_snapshots_per_turn": _prompt_int("Maximum snapshots per turn", 5, input_stream),
    }

    return {
        "tools": tools,
        "delegates": delegates,
        "todo": todo,
        "reasoning": reasoning,
        "patch": patch,
        "renderer": renderer,
        "no_edit": no_edit,
    }


def _interactive_install_options(
    hermes_home: Path, input_stream: Any = sys.stdin
) -> tuple[list[str] | None, bool, bool, dict[str, Any], bool]:
    print("hermes-progress-tail interactive installer")
    print("Press Enter to accept the recommended default shown in brackets.")
    profiles, all_profiles = _select_profiles_interactive(hermes_home, input_stream)
    print("\nSetup mode")
    print("  default: reset/apply recommended defaults without extra questions")
    print("  simple: ask only the core UX choices")
    print("  advance: ask every public config option")
    setup_mode = _prompt_setup_mode(input_stream)
    set_display_off = True
    force_default_config = setup_mode == "default"
    if setup_mode == "default":
        print("Applying recommended defaults.")
        overrides: dict[str, Any] = {}
    elif setup_mode == "simple":
        overrides = _simple_install_overrides(input_stream)
        set_display_off = _confirm(
            "Disable Hermes built-in progress/reasoning display to avoid duplicates",
            True,
            input_stream,
        )
    else:
        overrides = _advanced_install_overrides(input_stream)
        set_display_off = _confirm(
            "Disable Hermes built-in progress/reasoning display to avoid duplicates",
            True,
            input_stream,
        )
    return profiles, all_profiles, set_display_off, overrides, force_default_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hermes_progress_tail")
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--hermes-home", default=os.getenv("HERMES_HOME", "~/.hermes"))
    parser.add_argument("--source-dir", default=str(_default_source_dir()))
    parser.add_argument("--set-display-off", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--all-profiles", action="store_true")
    parser.add_argument("--interactive", "-i", action="store_true")
    parser.add_argument("--prompt-input", default="")
    parser.add_argument("--enable-tools", choices=["on", "off"])
    parser.add_argument("--enable-delegates", choices=["on", "off"])
    parser.add_argument("--enable-todo", choices=["on", "off"])
    parser.add_argument("--enable-reasoning", choices=["on", "off"])
    parser.add_argument("--renderer-style", choices=["emoji", "plain"])
    parser.add_argument("--renderer-density", choices=["compact", "normal", "debug"])
    args = parser.parse_args(argv)
    hermes_home = Path(args.hermes_home)
    profiles = _parse_profile_list(args.profile)
    all_profiles = args.all_profiles
    set_display_off = args.set_display_off
    feature_overrides: dict[str, Any] = {}
    force_default_config = False
    prompt_stream = sys.stdin
    prompt_file = None
    if args.prompt_input:
        try:
            prompt_file = Path(args.prompt_input).open(encoding="utf-8")  # noqa: SIM115
            prompt_stream = prompt_file
        except OSError as exc:
            print(f"error: cannot open prompt input {args.prompt_input}: {exc}", file=sys.stderr)
            return 2
    try:
        if args.interactive and args.action == "install":
            profiles, all_profiles, set_display_off, feature_overrides, force_default_config = (
                _interactive_install_options(hermes_home.expanduser().resolve(), prompt_stream)
            )
        elif args.interactive and args.action == "uninstall":
            profiles, all_profiles = _select_profiles_interactive(
                hermes_home.expanduser().resolve(), prompt_stream, action="uninstall"
            )
    except (EOFError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if prompt_file is not None:
            prompt_file.close()
        return 2
    if not (args.interactive and args.action in {"install", "uninstall"}):
        toggles = {
            "tools": args.enable_tools,
            "delegates": args.enable_delegates,
            "reasoning": args.enable_reasoning,
        }
        for section, value in toggles.items():
            if value:
                feature_overrides.setdefault(section, {})["enabled"] = value == "on"
        if args.enable_todo:
            feature_overrides.setdefault("todo", {})["sticky"] = args.enable_todo == "on"
        if args.renderer_style:
            feature_overrides.setdefault("renderer", {})["style"] = args.renderer_style
        if args.renderer_density:
            feature_overrides.setdefault("renderer", {})["density"] = args.renderer_density
    try:
        if args.action == "install":
            result = install_many(
                hermes_home,
                Path(args.source_dir),
                profiles=profiles,
                all_profiles=all_profiles,
                set_display_off=set_display_off,
                dry_run=args.dry_run,
                feature_overrides=feature_overrides,
                force_default_config=force_default_config,
            )
        else:
            result = uninstall_many(
                hermes_home,
                profiles=profiles,
                all_profiles=all_profiles,
                dry_run=args.dry_run,
            )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for msg in result.messages:
        print(msg)
    if prompt_file is not None:
        prompt_file.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
