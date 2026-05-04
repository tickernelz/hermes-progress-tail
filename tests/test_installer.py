import subprocess
import sys

import yaml

from hermes_progress_tail.installer import (
    DEFAULT_CONFIG,
    _builtin_reasoning_conflict,
    _default_source_dir,
    _generated_plugin_yaml,
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
    assert config["progress_tail"]["tools"]["show_completed"] is True
    assert config["progress_tail"]["tools"]["show_duration"] is True
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


def test_default_source_dir_resolves_plugin_root_after_package_restructure():
    source_dir = _default_source_dir()

    assert (source_dir / "plugin.yaml").exists()
    assert (source_dir / "hermes_progress_tail" / "__init__.py").exists()


def test_module_cli_default_source_dir_installs_valid_plugin_layout(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("{}\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_progress_tail.installer",
            "install",
            "--hermes-home",
            str(hermes_home),
        ],
        text=True,
        capture_output=True,
        check=True,
    )

    plugin_dir = hermes_home / "plugins" / "hermes-progress-tail"
    assert "Updated plugin" in result.stdout or "Installed plugin" in result.stdout
    assert (plugin_dir / "plugin.yaml").exists()
    assert (plugin_dir / "hermes_progress_tail" / "__init__.py").exists()


def test_package_source_install_generates_plugin_yaml(tmp_path):
    package_source = tmp_path / "hermes_progress_tail"
    (package_source / "runtime").mkdir(parents=True)
    (package_source / "rendering").mkdir()
    (package_source / "__init__.py").write_text("", encoding="utf-8")
    (package_source / "runtime" / "plugin.py").write_text("", encoding="utf-8")
    (package_source / "rendering" / "renderer.py").write_text("", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text("{}\n", encoding="utf-8")

    install(hermes_home, package_source, dry_run=False)

    plugin_yaml = hermes_home / "plugins" / "hermes-progress-tail" / "plugin.yaml"
    assert plugin_yaml.read_text(encoding="utf-8") == _generated_plugin_yaml()


def test_interactive_cli_default_mode_applies_recommended_defaults_after_profile_selection(
    tmp_path,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    work_home = hermes_home / "profiles" / "work"
    work_home.mkdir(parents=True)
    (work_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "display": {"tool_progress": "all", "show_reasoning": True},
                "progress_tail": {
                    "tools": {"enabled": False, "lines": 9},
                    "renderer": {"style": "plain", "density": "debug"},
                },
            }
        ),
        encoding="utf-8",
    )
    answers_path = tmp_path / "answers.txt"
    answers_path.write_text(
        "1\n"  # profile: work
        "\n",  # setup mode: default
        encoding="utf-8",
    )

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

    assert "Setup mode" in result.stdout
    assert "Applying recommended defaults" in result.stdout
    config = yaml.safe_load((work_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["progress_tail"] == DEFAULT_CONFIG
    assert config["display"]["tool_progress"] == "off"
    assert config["display"]["show_reasoning"] is False


def test_interactive_cli_simple_mode_asks_core_questions_only(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    work_home = hermes_home / "profiles" / "work"
    work_home.mkdir(parents=True)
    (work_home / "config.yaml").write_text("{}\n", encoding="utf-8")
    answers_path = tmp_path / "answers.txt"
    answers_path.write_text(
        "1\n"  # profile: work
        "simple\n"  # setup mode
        "n\n"  # tools.enabled
        "y\n"  # delegates.enabled
        "n\n"  # todo.sticky
        "y\n"  # reasoning.enabled
        "plain\n"  # renderer.style
        "compact\n"  # renderer.density
        "y\n",  # set_display_off
        encoding="utf-8",
    )

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

    assert "Simple setup" in result.stdout
    assert "Tool preview max characters" not in result.stdout
    assert "Patch formatter" not in result.stdout
    config = yaml.safe_load((work_home / "config.yaml").read_text(encoding="utf-8"))
    progress_tail = config["progress_tail"]
    assert progress_tail["tools"]["enabled"] is False
    assert progress_tail["tools"]["lines"] == DEFAULT_CONFIG["tools"]["lines"]
    assert progress_tail["delegates"]["enabled"] is True
    assert progress_tail["todo"]["sticky"] is False
    assert progress_tail["reasoning"]["enabled"] is True
    assert progress_tail["renderer"]["style"] == "plain"
    assert progress_tail["renderer"]["density"] == "compact"
    assert progress_tail["patch"] == DEFAULT_CONFIG["patch"]
    assert config["display"]["tool_progress"] == "off"


def test_interactive_cli_accepts_advanced_alias_for_full_setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    work_home = hermes_home / "profiles" / "work"
    work_home.mkdir(parents=True)
    (work_home / "config.yaml").write_text("{}\n", encoding="utf-8")
    answers_path = tmp_path / "answers.txt"
    answers_path.write_text(
        "1\n"  # profile: work
        "advanced\n"  # setup mode alias
        "\n"  # tools.enabled
        "\n"  # tools.lines
        "\n"  # tools.preview_length
        "\n"  # tools.show_completed
        "\n"  # tools.show_duration
        "\n"  # tools.timestamp
        "\n"  # delegates.enabled
        "\n"  # delegates.max_delegates
        "\n"  # delegates.lines_per_delegate
        "\n"  # delegates.max_goal_chars
        "\n"  # delegates.max_line_chars
        "\n"  # delegates.show_model
        "\n"  # delegates.show_tool_count
        "\n"  # delegates.show_completion
        "\n"  # delegates.thinking
        "\n"  # todo.sticky
        "\n"  # todo.hide_tool_line
        "\n"  # todo.max_pending
        "\n"  # todo.max_completed
        "\n"  # todo.max_cancelled
        "\n"  # todo.max_item_chars
        "\n"  # reasoning.enabled
        "\n"  # reasoning.max_lines
        "\n"  # reasoning.max_chars
        "\n"  # reasoning.min_update_chars
        "\n"  # reasoning.no_edit_strategy
        "\n"  # patch.detail
        "\n"  # patch.preview_chars
        "\n"  # patch.max_files
        "\n"  # renderer.strategy
        "\n"  # renderer.mode
        "\n"  # renderer.style
        "\n"  # renderer.density
        "\n"  # renderer.edit_interval
        "\n"  # renderer.stale_ttl_seconds
        "\n"  # renderer.redact_secrets
        "\n"  # no_edit.interval_seconds
        "\n"  # no_edit.min_new_events
        "\n"  # no_edit.final_summary
        "\n"  # no_edit.max_snapshots_per_turn
        "\n",  # set_display_off
        encoding="utf-8",
    )

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

    assert "Tool progress" in result.stdout
    config = yaml.safe_load((work_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["progress_tail"] == DEFAULT_CONFIG


def test_interactive_cli_invalid_setup_mode_exits_cleanly(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles" / "work").mkdir(parents=True)
    (hermes_home / "profiles" / "work" / "config.yaml").write_text("{}\n", encoding="utf-8")
    answers_path = tmp_path / "answers.txt"
    answers_path.write_text(
        "1\n"  # profile: work
        "expert\n",  # invalid setup mode
        encoding="utf-8",
    )

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
        check=False,
    )

    assert result.returncode == 2
    assert "invalid choice for 'Setup mode'" in result.stderr
    assert "Traceback" not in result.stderr


def test_interactive_cli_selects_profiles_and_features(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles" / "work").mkdir(parents=True)
    (hermes_home / "profiles" / "work" / "config.yaml").write_text("{}\n", encoding="utf-8")

    answers_path = tmp_path / "answers.txt"
    answers_path.write_text(
        "1\n"  # profile: work
        "advance\n"  # setup mode
        "y\n"  # tools.enabled
        "4\n"  # tools.lines
        "160\n"  # tools.preview_length
        "y\n"  # tools.show_completed
        "n\n"  # tools.show_duration
        "n\n"  # tools.timestamp
        "n\n"  # delegates.enabled
        "5\n"  # delegates.max_delegates
        "3\n"  # delegates.lines_per_delegate
        "60\n"  # delegates.max_goal_chars
        "140\n"  # delegates.max_line_chars
        "y\n"  # delegates.show_model
        "n\n"  # delegates.show_tool_count
        "n\n"  # delegates.show_completion
        "summary\n"  # delegates.thinking
        "y\n"  # todo.sticky
        "n\n"  # todo.hide_tool_line
        "4\n"  # todo.max_pending
        "5\n"  # todo.max_completed
        "2\n"  # todo.max_cancelled
        "48\n"  # todo.max_item_chars
        "n\n"  # reasoning.enabled
        "4\n"  # reasoning.max_lines
        "900\n"  # reasoning.max_chars
        "120\n"  # reasoning.min_update_chars
        "snapshot\n"  # reasoning.no_edit_strategy
        "smart\n"  # patch.detail
        "64\n"  # patch.preview_chars
        "4\n"  # patch.max_files
        "snapshot\n"  # renderer.strategy
        "compact\n"  # renderer.mode
        "plain\n"  # renderer.style
        "debug\n"  # renderer.density
        "2.5\n"  # renderer.edit_interval
        "1200\n"  # renderer.stale_ttl_seconds
        "y\n"  # renderer.redact_secrets
        "45\n"  # no_edit.interval_seconds
        "4\n"  # no_edit.min_new_events
        "y\n"  # no_edit.final_summary
        "6\n"  # no_edit.max_snapshots_per_turn
        "y\n",  # set_display_off
        encoding="utf-8",
    )
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
    assert "Tool progress" in result.stdout
    assert "Show completion status" in result.stdout
    assert "Delegate/subagent progress" in result.stdout
    assert "No-edit platform snapshots" in result.stdout
    config = yaml.safe_load(
        (hermes_home / "profiles" / "work" / "config.yaml").read_text(encoding="utf-8")
    )
    progress_tail = config["progress_tail"]
    assert progress_tail["tools"] == {
        "enabled": True,
        "lines": 4,
        "preview_length": 160,
        "show_completed": True,
        "show_duration": False,
        "timestamp": False,
        "timestamp_format": "%H:%M",
    }
    assert progress_tail["delegates"]["enabled"] is False
    assert progress_tail["delegates"]["max_delegates"] == 5
    assert progress_tail["delegates"]["lines_per_delegate"] == 3
    assert progress_tail["delegates"]["max_goal_chars"] == 60
    assert progress_tail["delegates"]["max_line_chars"] == 140
    assert progress_tail["delegates"]["show_model"] is True
    assert progress_tail["delegates"]["show_tool_count"] is False
    assert progress_tail["delegates"]["show_completion"] is False
    assert progress_tail["delegates"]["thinking"] == "summary"
    assert progress_tail["todo"]["hide_tool_line"] is False
    assert progress_tail["todo"]["max_pending"] == 4
    assert progress_tail["todo"]["max_completed"] == 5
    assert progress_tail["todo"]["max_cancelled"] == 2
    assert progress_tail["todo"]["max_item_chars"] == 48
    assert progress_tail["reasoning"]["enabled"] is False
    assert progress_tail["reasoning"]["max_lines"] == 4
    assert progress_tail["reasoning"]["max_chars"] == 900
    assert progress_tail["reasoning"]["min_update_chars"] == 120
    assert progress_tail["reasoning"]["no_edit_strategy"] == "snapshot"
    assert progress_tail["patch"]["detail"] == "smart"
    assert progress_tail["patch"]["preview_chars"] == 64
    assert progress_tail["patch"]["max_files"] == 4
    assert progress_tail["renderer"]["strategy"] == "snapshot"
    assert progress_tail["renderer"]["mode"] == "compact"
    assert progress_tail["renderer"]["style"] == "plain"
    assert progress_tail["renderer"]["density"] == "debug"
    assert progress_tail["renderer"]["edit_interval"] == 2.5
    assert progress_tail["renderer"]["stale_ttl_seconds"] == 1200
    assert progress_tail["renderer"]["redact_secrets"] is True
    assert progress_tail["no_edit"]["interval_seconds"] == 45
    assert progress_tail["no_edit"]["min_new_events"] == 4
    assert progress_tail["no_edit"]["final_summary"] is True
    assert progress_tail["no_edit"]["max_snapshots_per_turn"] == 6
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
