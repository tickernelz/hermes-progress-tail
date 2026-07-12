from dataclasses import asdict, replace

import hermes_progress_tail.settings.loading as loading
from hermes_progress_tail.settings.types import Settings


def test_loading_constructs_every_section_from_current_config():
    raw = {
        "enabled": False,
        "tools": {"lines": 7},
        "delegates": {"max_delegates": 2},
        "todo": {"max_pending": 8},
        "patch": {"max_files": 9},
        "assistant": {"max_lines": 10},
        "reasoning": {"max_chars": 700},
        "background_jobs": {"max_jobs": 11},
        "native_gateway": {"suppress": False},
        "cleanup": {"delay_seconds": 12},
        "footer": {"max_path_chars": 64},
        "telegram": {"max_table_rows": 13},
        "renderer": {"edit_interval": 1.5},
        "no_edit": {"min_new_events": 14},
        "platforms": {"opaque": {"value": object()}},
    }
    settings = loading.load_settings({"progress_tail": raw})
    assert (
        settings.enabled,
        settings.tools.lines,
        settings.delegates.max_delegates,
        settings.todo.max_pending,
        settings.patch.max_files,
        settings.assistant.max_lines,
        settings.reasoning.max_chars,
        settings.background_jobs.max_jobs,
        settings.native_gateway.suppress,
        settings.cleanup.delay_seconds,
        settings.footer.max_path_chars,
        settings.telegram.max_table_rows,
        settings.renderer.edit_interval,
        settings.no_edit.min_new_events,
    ) == (False, 7, 2, 8, 9, 10, 700, 11, False, 12, 64, 13, 1.5, 14)
    assert settings.platforms is not raw["platforms"]
    assert settings.platforms["opaque"]["value"] is raw["platforms"]["opaque"]["value"]


def test_loading_uses_one_settings_instance_as_every_fallback(monkeypatch):
    canonical = Settings()
    custom = replace(
        canonical,
        enabled=False,
        tools=replace(canonical.tools, lines=17),
        renderer=replace(canonical.renderer, edit_interval=2.5),
        telegram=replace(canonical.telegram, max_table_rows=19),
    )
    calls = 0

    def settings_factory(**kwargs):
        nonlocal calls
        if kwargs:
            return Settings(**kwargs)
        calls += 1
        return custom

    monkeypatch.setattr(loading, "Settings", settings_factory)
    result = loading.load_settings(
        {"progress_tail": {"tools": {"lines": "bad"}, "renderer": [], "telegram": None}}
    )
    assert calls == 1
    assert (result.enabled, result.tools.lines, result.renderer.edit_interval) == (False, 17, 2.5)
    assert result.telegram.max_table_rows == 19


def test_malformed_sections_fall_back_to_settings_defaults():
    malformed = {
        name: object() for name in asdict(Settings()) if name not in {"enabled", "platforms"}
    }
    expected = replace(Settings(), platforms={})
    assert loading.load_settings({"progress_tail": malformed}) == expected


def test_loading_does_not_mutate_or_take_ownership_of_caller_mapping():
    platforms = {"telegram": {"lines": 4}}
    config = {"progress_tail": {"tools": {"lines": 6}, "platforms": platforms}}
    result = loading.load_settings(config)
    assert config == {"progress_tail": {"tools": {"lines": 6}, "platforms": platforms}}
    assert result.platforms == platforms
    assert result.platforms is not platforms
    assert result.platforms["telegram"] is not platforms["telegram"]
