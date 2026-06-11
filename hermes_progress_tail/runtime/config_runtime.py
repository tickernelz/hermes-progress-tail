from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from ..settings.config import load_settings

logger = logging.getLogger(__name__)


def _load_runtime_config() -> dict[str, Any]:
    config = {}
    try:
        from hermes_constants import get_hermes_home

        config_path = Path(get_hermes_home()) / "config.yaml"
    except Exception:
        config_path = Path.home() / ".hermes" / "config.yaml"
    try:
        if config_path.exists():
            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                config = loaded
    except Exception as exc:
        logger.debug("hermes-progress-tail config load failed: %s", exc)
    return config


def _load_runtime_settings():
    return load_settings(_load_runtime_config())


def _progress_tail_enabled(config: dict[str, Any]) -> bool:
    progress_tail = config.get("progress_tail")
    return not (isinstance(progress_tail, dict) and progress_tail.get("enabled") is False)


def _feature_enabled(config: dict[str, Any], name: str, default: bool = True) -> bool:
    if not _progress_tail_enabled(config):
        return False
    progress_tail = config.get("progress_tail")
    feature = progress_tail.get(name) if isinstance(progress_tail, dict) else None
    if not isinstance(feature, dict):
        return default
    return feature.get("enabled") is not False


def _assistant_tail_enabled(config: dict[str, Any]) -> bool:
    return _feature_enabled(config, "assistant", True)


def _builtin_interim_conflict(config: dict[str, Any]) -> bool:
    display = config.get("display")
    if not isinstance(display, dict) or display.get("interim_assistant_messages") is False:
        return False
    return _assistant_tail_enabled(config)


def _builtin_reasoning_conflict(config: dict[str, Any]) -> bool:
    display = config.get("display")
    if not isinstance(display, dict) or display.get("show_reasoning") is not True:
        return False
    return _feature_enabled(config, "reasoning", True)


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


def _interim_conflict_warning() -> str:
    return (
        "warning: display.interim_assistant_messages=true while "
        "progress_tail.assistant.enabled=true; duplicate mid-turn assistant progress may occur. "
        "Set display.interim_assistant_messages=false."
    )


def _reasoning_conflict_warning() -> str:
    return (
        "warning: display.show_reasoning=true while progress_tail.reasoning.enabled=true; "
        "duplicate reasoning/final output may occur. Set display.show_reasoning=false."
    )


def _core_notifier_conflict_warning() -> str:
    return (
        "warning: agent.gateway_notify_interval is enabled while progress_tail.enabled=true; "
        "Hermes core Still working notifications use send() and can duplicate progress. "
        "Set agent.gateway_notify_interval=0."
    )


def _background_job_config_warnings(settings: Any) -> list[str]:
    background = settings.background_jobs
    if not background.enabled:
        return []
    warnings = []
    if not background.suppress_native_notify:
        warnings.append(
            "warning: background_jobs.enabled=true but suppress_native_notify=false; "
            "native process notifications may duplicate progress-tail output"
        )
    if not background.suppress_watch_notifications:
        warnings.append(
            "warning: background_jobs.enabled=true but suppress_watch_notifications=false; "
            "watch pattern notifications may duplicate progress-tail output"
        )
    if not background.list_running:
        warnings.append(
            "warning: background_jobs.enabled=true but list_running=false; "
            "running jobs will be hidden from /progresstail jobs"
        )
    return warnings
