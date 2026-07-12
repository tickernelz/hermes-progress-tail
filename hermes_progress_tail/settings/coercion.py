from __future__ import annotations

from typing import Any, Literal

from .schema import VALID_STRATEGIES


def as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if value is None:
        return default
    return bool(value)


def as_int(value: Any, default: int, min_value: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= min_value else default


def as_float(value: Any, default: float, min_value: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > min_value else default


def as_strategy(value: Any, default: str = "auto") -> str:
    val = str(value or default).strip().lower()
    return val if val in VALID_STRATEGIES else default


def as_style(value: Any, default: str = "emoji") -> Literal["emoji", "plain"]:
    val = str(value or default).strip().lower()
    return "plain" if val == "plain" else "emoji"


def as_density(
    value: Any, default: str = "normal"
) -> Literal["compact", "normal", "verbose", "debug"]:
    val = str(value or default).strip().lower()
    return val if val in {"compact", "normal", "verbose", "debug"} else "normal"


def as_footer_density(value: Any, default: str = "normal") -> Literal["compact", "normal", "debug"]:
    val = str(value or default).strip().lower()
    return val if val in {"compact", "normal", "debug"} else "normal"


def renderer_mode_and_density(
    raw: dict[str, Any],
    default_mode: str = "sectioned",
    default_density: str = "normal",
) -> tuple[str, Literal["compact", "normal", "verbose", "debug"]]:
    mode = str(raw.get("mode") or default_mode).strip().lower() or default_mode
    density = as_density(raw.get("density"), default_density)
    if mode == "compact":
        return "sectioned", "compact"
    if mode not in {"focused", "sectioned"}:
        return "sectioned", density
    return mode, density


def as_patch_detail(value: Any, default: str = "smart") -> str:
    val = str(value or default).strip().lower()
    return val if val in {"off", "path", "smart", "stats"} else default


def as_delegate_thinking(value: Any, default: str = "off") -> Literal["off", "summary"]:
    val = str(value or default).strip().lower()
    return "summary" if val == "summary" else "off"
