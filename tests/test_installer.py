import subprocess
import sys

import yaml

from hermes_progress_tail.installer import (
    DEFAULT_CONFIG,
    _builtin_reasoning_conflict,
    install,
    install_many,
    uninstall,
    uninstall_many,
)


def test_install_copies_plugin_and_updates_config(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    (source / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
    (source / "hermes_progress_tail").mkdir()
    (source / "hermes_progress_tail" / "__init__.py").write_text("", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("plugins:\n  enabled: []\n", encoding="utf-8")

    result = install(hermes_home, source, set_display_off=True, dry_run=False)

    assert result.changed is True
    assert (hermes_home / "plugins" / "hermes-progress-tail" / "plugin.yaml").exists()
    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert "hermes-progress-tail" in config["plugins"]["enabled"]
    assert config["display"]["tool_progress"] == "off"
    assert config["display"]["show_reasoning"] is False
    assert config["progress_tail"]["tools"]["timestamp"] is True
    assert config["progress_tail"]["tools"]["timestamp_format"] == "%H:%M"
    assert config["progress_tail"]["delegates"]["enabled"] is True
    assert config["progress_tail"]["delegates"]["max_delegates"] == 4
    assert config["progress_tail"]["delegates"]["lines_per_delegate"] == 2
    assert config["progress_tail"]["delegates"]["thinking"] == "off"
    assert config["progress_tail"]["todo"]["sticky"] is True
    assert config["progress_tail"]["todo"]["hide_tool_line"] is True
    assert config["progress_tail"]["patch"]["detail"] == "smart"
    assert config["progress_tail"]["patch"]["preview_chars"] == 48
    assert config["progress_tail"]["patch"]["max_files"] == 3
    assert config["progress_tail"]["renderer"]["style"] == "emoji"
    assert "progress_tail" in config
    assert (hermes_home / "hermes-progress-tail" / "backups").exists()


def test_install_preserves_builtin_reasoning_when_plugin_reasoning_disabled(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    (source / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "display": {"show_reasoning": True},
                "progress_tail": {"enabled": True, "reasoning": {"enabled": False}},
            }
        ),
        encoding="utf-8",
    )

    install(hermes_home, source, set_display_off=True, dry_run=False)

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["display"]["show_reasoning"] is True


def test_install_dry_run_does_not_modify_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("{}\n", encoding="utf-8")

    result = install(hermes_home, source, dry_run=True)

    assert result.changed is True
    assert not (hermes_home / "plugins").exists()
    assert yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8")) == {}


def test_install_merges_new_default_keys_without_overwriting_existing_values(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    (source / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "progress_tail": {
                    "enabled": True,
                    "tools": {"lines": 5, "timestamp": False},
                    "renderer": {"strategy": "live_tail"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = install(hermes_home, source, dry_run=False)

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["progress_tail"]["tools"]["lines"] == 5
    assert config["progress_tail"]["tools"]["timestamp"] is False
    assert config["progress_tail"]["tools"]["timestamp_format"] == "%H:%M"
    assert config["progress_tail"]["delegates"]["enabled"] is True
    assert config["progress_tail"]["delegates"]["max_delegates"] == 4
    assert config["progress_tail"]["todo"]["hide_tool_line"] is True
    assert config["progress_tail"]["patch"]["detail"] == "smart"
    assert config["progress_tail"]["renderer"]["strategy"] == "live_tail"
    assert config["progress_tail"]["renderer"]["style"] == "emoji"
    assert any("progress_tail.todo" in message for message in result.messages)


def test_install_warns_when_builtin_reasoning_conflicts(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    (source / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "display": {"show_reasoning": True},
                "progress_tail": {"enabled": True, "reasoning": {"enabled": True}},
            }
        ),
        encoding="utf-8",
    )

    result = install(hermes_home, source, dry_run=True)

    assert _builtin_reasoning_conflict(
        yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    )
    assert any("display.show_reasoning=true" in message for message in result.messages)


def test_install_many_targets_selected_profiles_and_updates_existing_plugin(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    default_config = hermes_home / "config.yaml"
    work_config = hermes_home / "profiles" / "work" / "config.yaml"
    personal_config = hermes_home / "profiles" / "personal" / "config.yaml"
    default_config.parent.mkdir(parents=True)
    work_config.parent.mkdir(parents=True)
    personal_config.parent.mkdir(parents=True)
    default_config.write_text("{}\n", encoding="utf-8")
    work_config.write_text("{}\n", encoding="utf-8")
    personal_config.write_text("{}\n", encoding="utf-8")
    existing = hermes_home / "profiles" / "work" / "plugins" / "hermes-progress-tail"
    existing.mkdir(parents=True)
    (existing / "old.txt").write_text("old", encoding="utf-8")

    result = install_many(
        hermes_home,
        source,
        profiles=["work", "personal"],
        dry_run=False,
        feature_overrides={"delegates": {"enabled": False}, "renderer": {"style": "plain"}},
    )

    assert "[work]" in "\n".join(result.messages)
    assert "[personal]" in "\n".join(result.messages)
    assert not (hermes_home / "plugins" / "hermes-progress-tail").exists()
    assert not (existing / "old.txt").exists()
    for name in ("work", "personal"):
        home = hermes_home / "profiles" / name
        assert (home / "plugins" / "hermes-progress-tail" / "plugin.yaml").exists()
        config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
        assert "hermes-progress-tail" in config["plugins"]["enabled"]
        assert config["progress_tail"]["delegates"]["enabled"] is False
        assert config["progress_tail"]["renderer"]["style"] == "plain"


def test_install_many_all_profiles_includes_default(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles" / "worker").mkdir(parents=True)
    (hermes_home / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (hermes_home / "config.yaml").write_text("{}\n", encoding="utf-8")
    (hermes_home / "profiles" / "worker" / "config.yaml").write_text("{}\n", encoding="utf-8")

    install_many(hermes_home, source, all_profiles=True, dry_run=False)

    assert (hermes_home / "plugins" / "hermes-progress-tail" / "plugin.yaml").exists()
    assert (
        hermes_home / "profiles" / "worker" / "plugins" / "hermes-progress-tail" / "plugin.yaml"
    ).exists()


def test_interactive_cli_selects_profiles_and_features(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles" / "work").mkdir(parents=True)
    (hermes_home / "profiles" / "work" / "config.yaml").write_text("{}\n", encoding="utf-8")

    answers_path = tmp_path / "answers.txt"
    answers_path.write_text("1\ny\nn\ny\nn\ny\ny\ny\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_progress_tail.installer",
            "install",
            "--hermes-home",
            str(hermes_home),
            "--source-dir",
            str(source),
            "--interactive",
            "--prompt-input",
            str(answers_path),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    assert "interactive installer" in result.stdout
    config = yaml.safe_load(
        (hermes_home / "profiles" / "work" / "config.yaml").read_text(encoding="utf-8")
    )
    assert config["progress_tail"]["delegates"]["enabled"] is False
    assert config["progress_tail"]["renderer"]["style"] == "plain"
    assert config["display"]["tool_progress"] == "off"
    assert not (hermes_home / "plugins" / "hermes-progress-tail").exists()


def test_feature_overrides_do_not_mutate_default_config(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("{}\n", encoding="utf-8")

    install(
        hermes_home,
        source,
        feature_overrides={"delegates": {"enabled": False}, "renderer": {"style": "plain"}},
    )

    assert DEFAULT_CONFIG["delegates"]["enabled"] is True
    assert DEFAULT_CONFIG["renderer"]["style"] == "emoji"


def test_uninstall_many_targets_selected_profiles(tmp_path):
    hermes_home = tmp_path / "hermes"
    for name in ("work", "personal"):
        home = hermes_home / "profiles" / name
        plugin_dir = home / "plugins" / "hermes-progress-tail"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
        (home / "config.yaml").write_text(
            yaml.safe_dump({"plugins": {"enabled": ["hermes-progress-tail", "other"]}}),
            encoding="utf-8",
        )

    uninstall_result = uninstall_many(hermes_home, profiles=["work"], dry_run=False)

    assert "[work]" in "\n".join(uninstall_result.messages)
    assert not (hermes_home / "profiles" / "work" / "plugins" / "hermes-progress-tail").exists()
    assert (hermes_home / "profiles" / "personal" / "plugins" / "hermes-progress-tail").exists()
    config = yaml.safe_load((hermes_home / "profiles" / "work" / "config.yaml").read_text())
    assert config["plugins"]["enabled"] == ["other"]


def test_uninstall_removes_plugin_and_enabled_entry(tmp_path):
    hermes_home = tmp_path / "hermes"
    plugin_dir = hermes_home / "plugins" / "hermes-progress-tail"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["hermes-progress-tail", "other"]}}),
        encoding="utf-8",
    )

    result = uninstall(hermes_home, dry_run=False)

    assert result.changed is True
    assert not plugin_dir.exists()
    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["plugins"]["enabled"] == ["other"]
