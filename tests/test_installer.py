import yaml

from hermes_progress_tail.installer import install, uninstall


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
