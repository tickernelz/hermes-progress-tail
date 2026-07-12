from __future__ import annotations

from typing import Any

from .schema import (
    PLATFORM_CONFIG_CONTRACT,
    PROGRESS_TAIL_CONFIG_CONTRACT,
    RETIRED_CONFIG_KEYS,
)
from .types import Settings


def _copy_config_containers(value: Any, memo: dict[int, Any] | None = None) -> Any:
    if memo is None:
        memo = {}
    value_id = id(value)
    if value_id in memo:
        return memo[value_id]
    if isinstance(value, dict):
        copied: Any = {}
        memo[value_id] = copied
        copied.update({key: _copy_config_containers(item, memo) for key, item in value.items()})
        return copied
    if isinstance(value, list):
        copied = []
        memo[value_id] = copied
        copied.extend(_copy_config_containers(item, memo) for item in value)
        return copied
    if isinstance(value, tuple):
        items = tuple(_copy_config_containers(item, memo) for item in value)
        if value_id in memo:
            return memo[value_id]
        memo[value_id] = items
        return items
    if isinstance(value, set):
        copied = set()
        memo[value_id] = copied
        copied.update(_copy_config_containers(item, memo) for item in value)
        return copied
    if isinstance(value, frozenset):
        copied = frozenset(_copy_config_containers(item, memo) for item in value)
        if value_id in memo:
            return memo[value_id]
        memo[value_id] = copied
        return copied
    return value


def extract_progress_tail_section(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return an independently owned current-shape progress-tail section."""
    if not isinstance(config, dict):
        return {}
    section = config.get("progress_tail")
    if isinstance(section, dict):
        return _copy_config_containers(section)
    legacy = config.get("tool_progress_tail")
    if isinstance(legacy, dict):
        return _legacy_to_progress_tail(legacy)
    return _copy_config_containers(config)


def _legacy_to_progress_tail(section: dict[str, Any]) -> dict[str, Any]:
    defaults = Settings()
    legacy_defaults = section.get("defaults")
    if not isinstance(legacy_defaults, dict):
        legacy_defaults = {}
    migrated = {
        "enabled": section.get("enabled", defaults.enabled),
        "tools": {
            "enabled": defaults.tools.enabled,
            "lines": legacy_defaults.get("lines", defaults.tools.lines),
            "preview_length": legacy_defaults.get("preview_length", defaults.tools.preview_length),
            "show_completed": legacy_defaults.get("show_completed", defaults.tools.show_completed),
            "show_duration": legacy_defaults.get("show_duration", defaults.tools.show_duration),
            "timestamp": legacy_defaults.get("timestamp", defaults.tools.timestamp),
            "timestamp_format": legacy_defaults.get(
                "timestamp_format", defaults.tools.timestamp_format
            ),
        },
        "delegates": section.get("delegates", {}),
        "assistant": section.get("assistant", {}),
        "renderer": {
            "strategy": defaults.renderer.strategy,
            "edit_interval": legacy_defaults.get("edit_interval", defaults.renderer.edit_interval),
            "stale_ttl_seconds": legacy_defaults.get(
                "stale_ttl_seconds", defaults.renderer.stale_ttl_seconds
            ),
            "redact_secrets": legacy_defaults.get(
                "redact_secrets", defaults.renderer.redact_secrets
            ),
        },
        "no_edit": section.get("no_edit", {}),
        "platforms": section.get("platforms", {}),
    }
    return _copy_config_containers(migrated)


def _walk_unknown_keys(raw: dict[str, Any], contract: dict[str, Any], prefix: str) -> list[str]:
    unknown: list[str] = []
    for key, value in raw.items():
        path = f"{prefix}.{key}"
        if path in RETIRED_CONFIG_KEYS:
            continue
        expected = contract.get(key, "__missing__")
        if expected == "__missing__":
            unknown.append(path)
            continue
        if expected == "legacy_map":
            continue
        if expected == "platform_map":
            if not isinstance(value, dict):
                continue
            for platform, platform_raw in value.items():
                platform_path = f"{path}.{platform}"
                if not isinstance(platform_raw, dict):
                    continue
                for platform_key in platform_raw:
                    nested_path = f"{platform_path}.{platform_key}"
                    if nested_path in RETIRED_CONFIG_KEYS:
                        continue
                    if platform_key not in PLATFORM_CONFIG_CONTRACT:
                        unknown.append(nested_path)
            continue
        if isinstance(expected, dict) and isinstance(value, dict):
            unknown.extend(_walk_unknown_keys(value, expected, path))
    return unknown


def find_unknown_config_keys(config: dict[str, Any] | None) -> list[str]:
    section = extract_progress_tail_section(config)
    return sorted(_walk_unknown_keys(section, PROGRESS_TAIL_CONFIG_CONTRACT, "progress_tail"))


def find_retired_config_keys(config: dict[str, Any] | None) -> list[str]:
    section = extract_progress_tail_section(config)
    retired = []
    if isinstance(section.get("finalization"), dict):
        retired.append("progress_tail.finalization")
    background_jobs = section.get("background_jobs")
    if isinstance(background_jobs, dict) and "default_notify_on_complete" in background_jobs:
        retired.append("progress_tail.background_jobs.default_notify_on_complete")
    telegram = section.get("telegram")
    if isinstance(telegram, dict):
        if "collapsible_details" in telegram:
            retired.append("progress_tail.telegram.collapsible_details")
        if "details_open_on_failure" in telegram:
            retired.append("progress_tail.telegram.details_open_on_failure")
    return retired
