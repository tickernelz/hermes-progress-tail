from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, replace
from typing import Any

import pytest

from hermes_progress_tail.settings import config
from hermes_progress_tail.settings.platforms import resolve_platform_settings
from hermes_progress_tail.settings.types import (
    AssistantSettings,
    BackgroundJobSettings,
    DelegateSettings,
    PlatformSettings,
    ReasoningSettings,
    RendererSettings,
    Settings,
    ToolSettings,
)


@pytest.mark.parametrize(
    ("platform", "enabled", "strategy"),
    [
        ("discord", True, "live_tail"),
        ("slack", True, "snapshot"),
        ("sms", False, "off"),
        ("unknown", True, "live_tail"),
    ],
)
def test_platform_default_policy(platform: str, enabled: bool, strategy: str) -> None:
    settings = Settings(renderer=RendererSettings(strategy="live_tail"))

    resolved = resolve_platform_settings(settings, platform)

    assert (resolved.enabled, resolved.strategy) == (enabled, strategy)


def test_global_disabled_combines_with_platform_defaults_and_explicit_override() -> None:
    settings = Settings(enabled=False, platforms={"sms": {"enabled": True}})

    assert resolve_platform_settings(settings, "discord").enabled is False
    assert resolve_platform_settings(settings, "sms").enabled is True
    assert resolve_platform_settings(settings, "sms").strategy == "off"


def test_malformed_platform_map_and_override_fall_back_to_defaults() -> None:
    malformed_map = replace(Settings(), platforms="not-a-map")  # type: ignore[arg-type]
    malformed_override = Settings(platforms={"discord": "not-a-map"})  # type: ignore[dict-item]

    with pytest.raises(AttributeError):
        resolve_platform_settings(malformed_map, "discord")
    assert resolve_platform_settings(malformed_override, "discord") == PlatformSettings(
        show_completed=True
    )


def test_every_platform_override_field_is_coerced() -> None:
    raw: dict[str, Any] = {
        "enabled": "false",
        "strategy": "SUMMARY_ONLY",
        "lines": "7",
        "preview_length": "88",
        "edit_interval": "2.5",
        "stale_ttl_seconds": "42",
        "redact_secrets": 0,
        "show_completed": "false",
        "tools": "false",
        "assistant": 0,
        "reasoning": "no",
        "delegates": "off",
        "background_jobs": False,
        "timestamp": 0,
        "timestamp_format": 24,
    }
    resolved = resolve_platform_settings(Settings(platforms={"discord": raw}), "discord")

    assert resolved == PlatformSettings(
        enabled=False,
        strategy="summary_only",
        lines=7,
        preview_length=88,
        edit_interval=2.5,
        stale_ttl_seconds=42,
        redact_secrets=False,
        show_completed=False,
        tools_enabled=False,
        assistant_enabled=False,
        reasoning_enabled=False,
        delegates_enabled=False,
        background_jobs_enabled=False,
        timestamp=False,
        timestamp_format="24",
    )
    assert {field.name for field in fields(PlatformSettings)} == set(resolved.__dict__)


@pytest.mark.parametrize(
    ("legacy", "new"),
    [
        ("tools", "tools_enabled"),
        ("assistant", "assistant_enabled"),
        ("reasoning", "reasoning_enabled"),
        ("delegates", "delegates_enabled"),
        ("background_jobs", "background_jobs_enabled"),
    ],
)
def test_legacy_alias_precedes_new_alias_and_new_alias_still_works(legacy: str, new: str) -> None:
    assert (
        getattr(
            resolve_platform_settings(Settings(platforms={"x": {legacy: False, new: True}}), "x"),
            new,
        )
        is False
    )
    assert (
        getattr(resolve_platform_settings(Settings(platforms={"x": {new: False}}), "x"), new)
        is False
    )


def test_base_values_come_from_all_nested_settings() -> None:
    settings = Settings(
        tools=ToolSettings(
            enabled=False,
            lines=4,
            preview_length=44,
            show_completed=False,
            timestamp=False,
            timestamp_format="%S",
        ),
        assistant=AssistantSettings(enabled=False),
        reasoning=ReasoningSettings(enabled=False),
        delegates=DelegateSettings(enabled=False),
        background_jobs=BackgroundJobSettings(enabled=False),
        renderer=RendererSettings(
            strategy="auto", edit_interval=3.5, stale_ttl_seconds=77, redact_secrets=False
        ),
    )

    assert resolve_platform_settings(settings, "discord") == PlatformSettings(
        lines=4,
        preview_length=44,
        edit_interval=3.5,
        stale_ttl_seconds=77,
        redact_secrets=False,
        show_completed=False,
        tools_enabled=False,
        assistant_enabled=False,
        reasoning_enabled=False,
        delegates_enabled=False,
        background_jobs_enabled=False,
        timestamp=False,
        timestamp_format="%S",
    )


def test_resolution_does_not_mutate_settings_or_platform_map() -> None:
    platform_map = {"discord": {"lines": "9", "tools": False}, "opaque": {"value": object()}}
    settings = Settings(platforms=platform_map)
    before_override = deepcopy(platform_map["discord"])
    override = platform_map["discord"]
    opaque = platform_map["opaque"]["value"]

    resolve_platform_settings(settings, "discord")

    assert platform_map["discord"] == before_override
    assert settings.platforms is platform_map
    assert settings.platforms["discord"] is override
    assert settings.platforms["opaque"]["value"] is opaque


def test_config_explicitly_reexports_canonical_function_with_identity() -> None:
    assert config.resolve_platform_settings is resolve_platform_settings
    assert "resolve_platform_settings" in config.__all__
