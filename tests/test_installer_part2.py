import subprocess
import sys

import yaml

from hermes_progress_tail.installer import (
    DEFAULT_CONFIG,
    install,
    uninstall,
    uninstall_many,
)


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
        "sectioned\n"  # renderer.mode
        "plain\n"  # renderer.style
        "compact\n"  # renderer.density
        "2.5\n"  # renderer.edit_interval
        "5\n"  # renderer.message_rollover_minutes
        "1200\n"  # renderer.stale_ttl_seconds
        "y\n"  # renderer.redact_secrets
        "45\n"  # no_edit.interval_seconds
        "4\n"  # no_edit.min_new_events
        "y\n"  # no_edit.final_summary
        "6\n",  # no_edit.max_snapshots_per_turn
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
    assert progress_tail["renderer"]["mode"] == "sectioned"
    assert progress_tail["renderer"]["style"] == "plain"
    assert progress_tail["renderer"]["density"] == "compact"
    assert progress_tail["renderer"]["edit_interval"] == 2.5
    assert progress_tail["renderer"]["stale_ttl_seconds"] == 1200
    assert progress_tail["renderer"]["redact_secrets"] is True
    assert (
        "compact"
        not in result.stdout.split("Renderer layout mode", 1)[1].split("Renderer style", 1)[0]
    )
    assert progress_tail["no_edit"]["interval_seconds"] == 45
    assert progress_tail["no_edit"]["min_new_events"] == 4
    assert progress_tail["no_edit"]["final_summary"] is True
    assert progress_tail["no_edit"]["max_snapshots_per_turn"] == 6
    assert "display" not in config
    assert "streaming" not in config
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
    assert DEFAULT_CONFIG["renderer"]["mode"] == "sectioned"
    assert DEFAULT_CONFIG["renderer"]["style"] == "emoji"
    assert DEFAULT_CONFIG["renderer"]["density"] == "normal"
    assert DEFAULT_CONFIG["renderer"]["edit_interval"] == 5.0
    assert DEFAULT_CONFIG["assistant"]["min_update_chars"] == 160
    assert DEFAULT_CONFIG["reasoning"]["min_update_chars"] == 300
    assert DEFAULT_CONFIG["background_jobs"]["update_interval_seconds"] == 10
    assert DEFAULT_CONFIG["cleanup"]["auto_delete"] is False
    assert DEFAULT_CONFIG["telegram"]["rich_messages"] is True


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
