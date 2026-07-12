from __future__ import annotations

from dataclasses import replace

from .coercion import as_bool, as_float, as_int, as_strategy
from .schema import BATCH_DEFAULT_OFF, SNAPSHOT_DEFAULTS
from .types import PlatformSettings, Settings

__all__ = ("resolve_platform_settings",)


def resolve_platform_settings(settings: Settings, platform: str) -> PlatformSettings:
    default_strategy = (
        "off"
        if platform in BATCH_DEFAULT_OFF
        else ("snapshot" if platform in SNAPSHOT_DEFAULTS else settings.renderer.strategy)
    )
    base = PlatformSettings(
        enabled=settings.enabled and platform not in BATCH_DEFAULT_OFF,
        strategy=default_strategy,
        lines=settings.tools.lines,
        preview_length=settings.tools.preview_length,
        edit_interval=settings.renderer.edit_interval,
        stale_ttl_seconds=settings.renderer.stale_ttl_seconds,
        redact_secrets=settings.renderer.redact_secrets,
        show_completed=settings.tools.show_completed,
        tools_enabled=settings.tools.enabled,
        assistant_enabled=settings.assistant.enabled,
        reasoning_enabled=settings.reasoning.enabled,
        delegates_enabled=settings.delegates.enabled,
        background_jobs_enabled=settings.background_jobs.enabled,
        timestamp=settings.tools.timestamp,
        timestamp_format=settings.tools.timestamp_format,
    )
    raw = (settings.platforms or {}).get(platform, {})
    if not isinstance(raw, dict):
        return base
    return replace(
        base,
        enabled=as_bool(raw.get("enabled"), base.enabled),
        strategy=as_strategy(raw.get("strategy"), base.strategy),
        lines=as_int(raw.get("lines"), base.lines),
        preview_length=as_int(raw.get("preview_length"), base.preview_length),
        edit_interval=as_float(raw.get("edit_interval"), base.edit_interval),
        stale_ttl_seconds=as_int(raw.get("stale_ttl_seconds"), base.stale_ttl_seconds),
        redact_secrets=as_bool(raw.get("redact_secrets"), base.redact_secrets),
        show_completed=as_bool(raw.get("show_completed"), base.show_completed),
        tools_enabled=as_bool(raw.get("tools", raw.get("tools_enabled")), base.tools_enabled),
        assistant_enabled=as_bool(
            raw.get("assistant", raw.get("assistant_enabled")), base.assistant_enabled
        ),
        reasoning_enabled=as_bool(
            raw.get("reasoning", raw.get("reasoning_enabled")), base.reasoning_enabled
        ),
        delegates_enabled=as_bool(
            raw.get("delegates", raw.get("delegates_enabled")), base.delegates_enabled
        ),
        background_jobs_enabled=as_bool(
            raw.get("background_jobs", raw.get("background_jobs_enabled")),
            base.background_jobs_enabled,
        ),
        timestamp=as_bool(raw.get("timestamp"), base.timestamp),
        timestamp_format=str(raw.get("timestamp_format") or base.timestamp_format),
    )
