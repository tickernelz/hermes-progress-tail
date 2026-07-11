from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from hermes_progress_tail.cli import installer
from hermes_progress_tail.cli.profiles import _resolve_profile_targets


def _source(root: Path) -> Path:
    source = root / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    return source


def test_update_config_repairs_malformed_plugin_containers_and_deduplicates_legacy():
    config = {
        "plugins": "invalid",
        "progress_tail": "invalid",
    }

    updated, changed, added = installer._update_config(config, set_display_off=False)

    assert changed is True
    assert updated["plugins"]["enabled"] == [installer.PLUGIN_NAME]
    assert updated["progress_tail"] == installer.DEFAULT_CONFIG
    assert added == ["progress_tail"]

    config = {
        "plugins": {"enabled": "invalid"},
        "progress_tail": copy.deepcopy(installer.DEFAULT_CONFIG),
    }
    updated, changed, added = installer._update_config(config, set_display_off=False)
    assert changed is True
    assert updated["plugins"]["enabled"] == [installer.PLUGIN_NAME]
    assert added == []


def test_update_config_migrates_legacy_name_and_settings_exactly():
    config = {
        "plugins": {"enabled": [installer.LEGACY_PLUGIN_NAME, "other"]},
        "tool_progress_tail": {
            "enabled": False,
            "defaults": {"lines": 8, "preview_length": 44, "edit_interval": 2.5},
            "no_edit": {"interval_seconds": 11},
            "platforms": {"telegram": {"enabled": False}},
        },
    }

    updated, changed, _ = installer._update_config(config, set_display_off=False)

    assert changed is True
    assert updated["plugins"]["enabled"] == [installer.PLUGIN_NAME, "other"]
    assert "tool_progress_tail" not in updated
    progress = updated["progress_tail"]
    assert progress["enabled"] is False
    assert progress["tools"]["lines"] == 8
    assert progress["tools"]["preview_length"] == 44
    assert progress["renderer"]["edit_interval"] == 2.5
    assert progress["no_edit"] == {
        "interval_seconds": 11,
        "min_new_events": 3,
        "final_summary": True,
        "max_snapshots_per_turn": 5,
    }
    assert progress["platforms"] == {"telegram": {"enabled": False}}


def test_update_config_discards_legacy_settings_when_modern_section_exists():
    modern = {"enabled": False}
    config = {"tool_progress_tail": {"enabled": True}, "progress_tail": modern}

    updated, changed, added = installer._update_config(config, set_display_off=False)

    assert changed is True
    assert "tool_progress_tail" not in updated
    assert updated["progress_tail"]["enabled"] is False
    assert "progress_tail.tools" in added


def test_update_config_force_defaults_then_merges_nested_overrides_without_aliasing():
    overrides = {"renderer": {"style": "plain"}, "custom": {"items": ["one"]}}
    config = {
        "plugins": {"enabled": [installer.PLUGIN_NAME]},
        "progress_tail": {"renderer": {"style": "custom"}},
    }

    updated, changed, added = installer._update_config(
        config,
        set_display_off=True,
        feature_overrides=overrides,
        force_default_config=True,
    )
    overrides["custom"]["items"].append("two")

    assert changed is True
    assert added == ["progress_tail"]
    assert updated["progress_tail"]["renderer"]["style"] == "plain"
    assert updated["progress_tail"]["custom"]["items"] == ["one"]
    assert updated["progress_tail"]["native_gateway"]["suppress"] is True


@pytest.mark.parametrize(
    ("config", "feature", "default", "expected"),
    [
        ({}, "reasoning", True, True),
        ({"progress_tail": False}, "reasoning", False, False),
        ({"progress_tail": {"enabled": False}}, "reasoning", True, False),
        ({"progress_tail": {"reasoning": False}}, "reasoning", False, False),
        ({"progress_tail": {"reasoning": {"enabled": False}}}, "reasoning", True, False),
        ({"progress_tail": {"reasoning": {}}}, "reasoning", False, True),
    ],
)
def test_feature_enabled_handles_malformed_configuration(config, feature, default, expected):
    assert installer._feature_enabled(config, feature, default) is expected


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({}, False),
        ({"display": "invalid"}, False),
        ({"display": {"show_reasoning": True}}, True),
        (
            {
                "display": {"show_reasoning": True},
                "progress_tail": {"reasoning": {"enabled": False}},
            },
            False,
        ),
    ],
)
def test_builtin_reasoning_conflict_requires_both_features(config, expected):
    assert installer._builtin_reasoning_conflict(config) is expected


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"progress_tail": {"enabled": False}}, False),
        ({"agent": "invalid"}, True),
        ({"agent": {"gateway_notify_interval": "invalid"}}, True),
        ({"agent": {"gateway_notify_interval": "0"}}, False),
        ({"agent": {"gateway_notify_interval": 0.1}}, True),
    ],
)
def test_core_notifier_conflict_is_conservative_for_malformed_values(config, expected):
    assert installer._core_notifier_conflict(config) is expected


def test_install_dry_run_reports_existing_and_legacy_targets_without_changes(tmp_path):
    source = _source(tmp_path)
    home = tmp_path / "home"
    target = home / "plugins" / installer.PLUGIN_NAME
    legacy = home / "plugins" / installer.LEGACY_PLUGIN_NAME
    target.mkdir(parents=True)
    legacy.mkdir()
    config = home / "config.yaml"
    config.write_text("{}\n", encoding="utf-8")

    result = installer.install(home, source, dry_run=True)

    assert result.changed is True
    assert result.messages == [
        "Added missing default config keys: progress_tail",
        f"Would copy plugin to {target}",
        f"Would update existing plugin {target}",
        f"Would remove legacy plugin {legacy}",
        f"Would update {config}",
    ]
    assert legacy.is_dir()
    assert config.read_text(encoding="utf-8") == "{}\n"


def test_uninstall_dry_run_reports_both_targets_and_preserves_everything(tmp_path):
    home = tmp_path / "home"
    target = home / "plugins" / installer.PLUGIN_NAME
    legacy = home / "plugins" / installer.LEGACY_PLUGIN_NAME
    target.mkdir(parents=True)
    legacy.mkdir()
    config = home / "config.yaml"
    original = yaml.safe_dump(
        {"plugins": {"enabled": [installer.PLUGIN_NAME, installer.LEGACY_PLUGIN_NAME]}}
    )
    config.write_text(original, encoding="utf-8")

    result = installer.uninstall(home, dry_run=True)

    assert result.changed is True
    assert result.messages == [
        f"Would remove {target}",
        f"Would remove {legacy}",
        f"Would update {config}",
    ]
    assert target.is_dir() and legacy.is_dir()
    assert config.read_text(encoding="utf-8") == original


def test_profile_resolution_normalizes_aliases_deduplicates_and_rejects_traversal(tmp_path):
    home = tmp_path / "home"
    work = home / "profiles" / "work"
    work.mkdir(parents=True)
    (work / "config.yaml").write_text("{}\n", encoding="utf-8")

    targets = _resolve_profile_targets(home, [" base ", "main", "work", "work", ""])
    assert targets == [("default", home.resolve()), ("work", work.resolve())]

    with pytest.raises(ValueError, match="unknown Hermes profile '../escape'"):
        _resolve_profile_targets(home, ["../escape"])
    assert not (tmp_path / "escape").exists()


def test_install_many_stops_on_second_profile_failure_after_first_success(tmp_path, monkeypatch):
    source = _source(tmp_path)
    home = tmp_path / "home"
    for name in ("one", "two"):
        profile = home / "profiles" / name
        profile.mkdir(parents=True)
        (profile / "config.yaml").write_text("{}\n", encoding="utf-8")

    real_install = installer.install
    calls = []

    def fail_second(profile_home, *args, **kwargs):
        calls.append(Path(profile_home))
        if Path(profile_home).name == "two":
            raise OSError("second profile failed")
        return real_install(profile_home, *args, **kwargs)

    monkeypatch.setattr(installer, "install", fail_second)
    with pytest.raises(OSError, match="second profile failed"):
        installer.install_many(home, source, profiles=["one", "two"])

    one = home / "profiles" / "one"
    two = home / "profiles" / "two"
    assert calls == [one.resolve(), two.resolve()]
    assert (one / "plugins" / installer.PLUGIN_NAME / "plugin.yaml").exists()
    assert not (two / "plugins").exists()
    assert yaml.safe_load((two / "config.yaml").read_text(encoding="utf-8")) == {}


def test_uninstall_many_stops_on_second_profile_failure_after_first_success(tmp_path, monkeypatch):
    home = tmp_path / "home"
    for name in ("one", "two"):
        plugin = home / "profiles" / name / "plugins" / installer.PLUGIN_NAME
        plugin.mkdir(parents=True)
        (plugin / "plugin.yaml").write_text("name: test\n", encoding="utf-8")

    real_uninstall = installer.uninstall

    def fail_second(profile_home, *args, **kwargs):
        if Path(profile_home).name == "two":
            raise OSError("second profile failed")
        return real_uninstall(profile_home, *args, **kwargs)

    monkeypatch.setattr(installer, "uninstall", fail_second)
    with pytest.raises(OSError, match="second profile failed"):
        installer.uninstall_many(home, profiles=["one", "two"])

    assert not (home / "profiles" / "one" / "plugins" / installer.PLUGIN_NAME).exists()
    assert (home / "profiles" / "two" / "plugins" / installer.PLUGIN_NAME).exists()
