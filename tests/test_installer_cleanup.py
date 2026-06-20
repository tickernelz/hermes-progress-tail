import yaml

from hermes_progress_tail.installer import cleanup_legacy_global_suppression


def test_cleanup_legacy_global_suppression_dry_run_reports_exact_changes_without_writing(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "display": {
                    "tool_progress": "off",
                    "streaming": False,
                    "show_reasoning": False,
                    "interim_assistant_messages": False,
                    "keep_me": "yes",
                },
                "streaming": {"enabled": False, "chunk_delay": 0.2},
                "agent": {"gateway_notify_interval": 0, "other": True},
            }
        ),
        encoding="utf-8",
    )
    before = config_path.read_text(encoding="utf-8")

    result = cleanup_legacy_global_suppression(hermes_home, dry_run=True)

    assert result.changed is True
    assert config_path.read_text(encoding="utf-8") == before
    assert "Would remove legacy global suppression keys" in "\n".join(result.messages)
    assert "display.tool_progress" in "\n".join(result.messages)
    assert "display.interim_assistant_messages" in "\n".join(result.messages)
    assert "streaming.enabled" in "\n".join(result.messages)
    assert "agent.gateway_notify_interval" in "\n".join(result.messages)


def test_cleanup_legacy_global_suppression_does_not_remove_falsy_lookalikes(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "display": {"streaming": 0, "show_reasoning": 0},
                "streaming": {"enabled": 0},
                "agent": {"gateway_notify_interval": False},
            }
        ),
        encoding="utf-8",
    )

    result = cleanup_legacy_global_suppression(hermes_home, dry_run=False)

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert result.changed is False
    assert "No legacy global suppression keys found" in "\n".join(result.messages)
    assert config == {
        "display": {"streaming": 0, "show_reasoning": 0},
        "streaming": {"enabled": 0},
        "agent": {"gateway_notify_interval": False},
    }


def test_cleanup_legacy_global_suppression_only_removes_exact_legacy_values(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    config_path = hermes_home / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "display": {
                    "tool_progress": "off",
                    "streaming": False,
                    "show_reasoning": True,
                    "interim_assistant_messages": False,
                    "keep_me": "yes",
                },
                "streaming": {"enabled": True, "chunk_delay": 0.2},
                "agent": {"gateway_notify_interval": 30, "other": True},
            }
        ),
        encoding="utf-8",
    )

    result = cleanup_legacy_global_suppression(hermes_home, dry_run=False)

    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert result.changed is True
    assert "display.tool_progress" in "\n".join(result.messages)
    assert "display.show_reasoning" not in "\n".join(result.messages)
    assert "streaming.enabled" not in "\n".join(result.messages)
    assert "agent.gateway_notify_interval" not in "\n".join(result.messages)
    assert config["display"] == {"show_reasoning": True, "keep_me": "yes"}
    assert config["streaming"] == {"enabled": True, "chunk_delay": 0.2}
    assert config["agent"] == {"gateway_notify_interval": 30, "other": True}
    assert (hermes_home / "hermes-progress-tail" / "backups").exists()
