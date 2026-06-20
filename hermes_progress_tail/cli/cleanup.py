from __future__ import annotations

from pathlib import Path
from typing import Any

from .installer import InstallResult, _backup_config, _read_yaml, _write_yaml

LEGACY_GLOBAL_SUPPRESSION_VALUES: tuple[tuple[str, str, Any], ...] = (
    ("display", "tool_progress", "off"),
    ("display", "streaming", False),
    ("display", "show_reasoning", False),
    ("display", "interim_assistant_messages", False),
    ("streaming", "enabled", False),
    ("agent", "gateway_notify_interval", 0),
)


def _remove_empty_mapping(config: dict[str, Any], section_name: str) -> None:
    section = config.get(section_name)
    if isinstance(section, dict) and not section:
        config.pop(section_name, None)


def _matches_exact_legacy_value(value: Any, legacy_value: Any) -> bool:
    if isinstance(legacy_value, bool):
        return value is legacy_value
    return type(value) is type(legacy_value) and value == legacy_value


def legacy_global_suppression_cleanup_paths(config: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for section_name, key, legacy_value in LEGACY_GLOBAL_SUPPRESSION_VALUES:
        section = config.get(section_name)
        if isinstance(section, dict) and _matches_exact_legacy_value(
            section.get(key), legacy_value
        ):
            paths.append(f"{section_name}.{key}")
    return paths


def apply_legacy_global_suppression_cleanup(config: dict[str, Any]) -> list[str]:
    removed = []
    for section_name, key, legacy_value in LEGACY_GLOBAL_SUPPRESSION_VALUES:
        section = config.get(section_name)
        if not isinstance(section, dict) or not _matches_exact_legacy_value(
            section.get(key), legacy_value
        ):
            continue
        section.pop(key, None)
        removed.append(f"{section_name}.{key}")
        _remove_empty_mapping(config, section_name)
    return removed


def cleanup_legacy_global_suppression(hermes_home: Path, *, dry_run: bool = False) -> InstallResult:
    hermes_home = Path(hermes_home).expanduser().resolve()
    config_path = hermes_home / "config.yaml"
    config = _read_yaml(config_path)
    paths = legacy_global_suppression_cleanup_paths(config)
    result = InstallResult(changed=bool(paths))
    if not paths:
        result.messages.append("No legacy global suppression keys found")
        return result
    if dry_run:
        result.messages.append("Would remove legacy global suppression keys: " + ", ".join(paths))
        return result
    backup = _backup_config(hermes_home)
    if backup:
        result.messages.append(f"Backed up config to {backup}")
    removed = apply_legacy_global_suppression_cleanup(config)
    _write_yaml(config_path, config)
    result.messages.append("Removed legacy global suppression keys: " + ", ".join(removed))
    return result
