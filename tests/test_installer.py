import yaml

from hermes_progress_tail.installer import (
    TELEGRAM_FLOOD_SAFE_CONFIG,
    _builtin_reasoning_conflict,
    _copy_plugin,
    _core_notifier_conflict,
    install,
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
    assert "display" not in config
    assert "streaming" not in config
    assert "agent" not in config
    assert config["progress_tail"]["native_gateway"]["suppress"] is True
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
    assert config["progress_tail"]["assistant"]["min_update_chars"] == 160
    assert config["progress_tail"]["reasoning"]["min_update_chars"] == 300
    assert config["progress_tail"]["renderer"]["style"] == "emoji"
    assert config["progress_tail"]["renderer"]["density"] == "normal"
    assert config["progress_tail"]["renderer"]["edit_interval"] == 5.0
    assert config["progress_tail"]["renderer"]["agent_label"] == ""
    assert config["progress_tail"]["footer"]["enabled"] is True
    assert config["progress_tail"]["footer"]["density"] == "normal"
    assert config["progress_tail"]["footer"]["max_path_chars"] == 56
    assert config["progress_tail"]["background_jobs"]["update_interval_seconds"] == 10
    assert config["progress_tail"]["cleanup"]["auto_delete"] is False
    assert config["progress_tail"]["telegram"]["rich_messages"] is True
    assert "default_notify_on_complete" not in config["progress_tail"]["background_jobs"]
    assert "finalization" not in config["progress_tail"]
    assert "progress_tail" in config
    assert (hermes_home / "hermes-progress-tail" / "backups").exists()


def test_install_does_not_overwrite_explicit_native_gateway_opt_out(tmp_path):
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
                    "native_gateway": {"suppress": False},
                }
            }
        ),
        encoding="utf-8",
    )

    install(hermes_home, source, set_display_off=True, dry_run=False)

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["progress_tail"]["native_gateway"]["suppress"] is False


def test_copy_plugin_ignores_local_generated_artifacts(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    generated_files = ["uv.lock", ".ruff_cache", ".venv", "build", "dist", "pkg.egg-info"]
    for name in generated_files:
        path = source / name
        if "." in name and not name.endswith(".lock"):
            path.mkdir()
            (path / "cache").write_text("generated\n", encoding="utf-8")
        elif name in {"build", "dist"} or name.endswith(".egg-info"):
            path.mkdir()
            (path / "artifact").write_text("generated\n", encoding="utf-8")
        else:
            path.write_text("generated\n", encoding="utf-8")
    (source / "hermes_progress_tail").mkdir()
    (source / "hermes_progress_tail" / "__init__.py").write_text("", encoding="utf-8")

    target = tmp_path / "target"
    _copy_plugin(source, target)

    assert (target / "plugin.yaml").exists()
    for name in generated_files:
        assert not (target / name).exists()


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
                    "telegram": {"collapsible_details": True, "details_open_on_failure": True},
                    "finalization": {"policy": "delete"},
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
    assert "collapsible_details" not in config["progress_tail"]["telegram"]
    assert "details_open_on_failure" not in config["progress_tail"]["telegram"]
    assert "default_notify_on_complete" not in config["progress_tail"]["background_jobs"]
    assert config["progress_tail"]["patch"]["detail"] == "smart"
    assert "finalization" not in config["progress_tail"]
    assert config["progress_tail"]["renderer"]["strategy"] == "live_tail"
    assert config["progress_tail"]["renderer"]["style"] == "emoji"
    assert config["progress_tail"]["renderer"]["density"] == "normal"
    assert config["progress_tail"]["renderer"]["edit_interval"] == 5.0
    assert config["progress_tail"]["renderer"]["agent_label"] == ""
    assert any("progress_tail.todo" in message for message in result.messages)
    assert any(
        "Removed retired config keys: progress_tail.finalization" in message
        and "progress_tail.telegram.collapsible_details" in message
        and "progress_tail.telegram.details_open_on_failure" in message
        for message in result.messages
    )


def test_install_flood_safe_overrides_existing_telegram_cadence(tmp_path):
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
                    "assistant": {"min_update_chars": 40},
                    "reasoning": {"min_update_chars": 80},
                    "background_jobs": {"update_interval_seconds": 3},
                    "cleanup": {"auto_delete": True},
                    "telegram": {"rich_messages": True},
                    "renderer": {"edit_interval": 1.5, "density": "verbose"},
                }
            }
        ),
        encoding="utf-8",
    )

    result = install(
        hermes_home,
        source,
        dry_run=False,
        feature_overrides=TELEGRAM_FLOOD_SAFE_CONFIG,
    )

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    progress_tail = config["progress_tail"]
    assert progress_tail["assistant"]["min_update_chars"] == 160
    assert progress_tail["reasoning"]["min_update_chars"] == 300
    assert progress_tail["background_jobs"]["update_interval_seconds"] == 10
    assert progress_tail["cleanup"]["auto_delete"] is False
    assert progress_tail["telegram"]["rich_messages"] is True
    assert progress_tail["renderer"]["edit_interval"] == 5.0
    assert progress_tail["renderer"]["density"] == "normal"
    assert result.changed is True


def test_install_preserves_core_notifier_with_recommended_display_defaults(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    (source / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump({"agent": {"gateway_notify_interval": 180}}),
        encoding="utf-8",
    )

    result = install(hermes_home, source, set_display_off=True, dry_run=False)

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["agent"]["gateway_notify_interval"] == 180
    assert config["progress_tail"]["native_gateway"]["suppress"] is True
    assert not any("gateway_notify_interval" in message for message in result.messages)


def test_install_preserves_native_streaming_with_recommended_display_defaults(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    (source / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "display": {"streaming": True, "tool_progress": "all", "keep_me": "yes"},
                "streaming": {"enabled": True, "chunk_delay": 0.2},
            }
        ),
        encoding="utf-8",
    )

    install(hermes_home, source, set_display_off=True, dry_run=False)

    config = yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    assert config["display"]["streaming"] is True
    assert config["display"]["tool_progress"] == "all"
    assert config["display"]["keep_me"] == "yes"
    assert config["streaming"]["enabled"] is True
    assert config["streaming"]["chunk_delay"] == 0.2
    assert config["progress_tail"]["native_gateway"]["suppress"] is True


def test_install_does_not_warn_for_core_notifier_when_native_gateway_suppression_is_default(
    tmp_path,
):
    source = tmp_path / "source"
    source.mkdir()
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    (source / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {"agent": {"gateway_notify_interval": 180}, "progress_tail": {"enabled": True}}
        ),
        encoding="utf-8",
    )

    result = install(hermes_home, source, dry_run=True)

    assert _core_notifier_conflict(
        yaml.safe_load((hermes_home / "config.yaml").read_text(encoding="utf-8"))
    )
    assert not any("gateway_notify_interval" in message for message in result.messages)


def test_install_does_not_warn_when_builtin_reasoning_is_preserved(tmp_path):
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
    assert not any("display.show_reasoning=true" in message for message in result.messages)
