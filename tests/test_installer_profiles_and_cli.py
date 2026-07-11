import subprocess
import sys

import yaml

from hermes_progress_tail.installer import (
    DEFAULT_CONFIG,
    _default_source_dir,
    _generated_plugin_yaml,
    install,
    install_many,
)


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
    assert config["display"]["tool_progress"] == "all"
    assert config["display"]["show_reasoning"] is True
    assert "streaming" not in config
    assert "agent" not in config


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
        "compact\n",  # renderer.density
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
    assert "display" not in config
    assert "streaming" not in config


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
        "\n",  # no_edit.max_snapshots_per_turn
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
