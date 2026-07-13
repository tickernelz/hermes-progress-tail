import tarfile

import pytest

import hermes_progress_tail.plugin as plugin
from hermes_progress_tail.runtime import commands


def test_update_command_requires_explicit_dry_run_or_apply(monkeypatch):
    called = False

    def fake_install(*args, **kwargs):
        nonlocal called
        called = True
        return ["should not happen"]

    monkeypatch.setattr(commands, "_run_update_install", fake_install, raising=False)

    output = plugin._command("update")

    assert "Usage: /progresstail update --dry-run" in output
    assert "--apply" in output
    assert called is False


def test_update_command_dry_run_uses_latest_release_without_writing(monkeypatch):
    calls = []
    monkeypatch.setattr(
        commands,
        "_latest_release_info",
        lambda: {"tag_name": "v0.2.10", "html_url": "https://example.test/v0.2.10"},
    )

    def fake_install(ref, *, dry_run, profiles=None, all_profiles=False):
        calls.append(
            {
                "ref": ref,
                "dry_run": dry_run,
                "profiles": profiles,
                "all_profiles": all_profiles,
            }
        )
        return ["[default] Would copy plugin to /tmp/hermes/plugins/hermes-progress-tail"]

    monkeypatch.setattr(commands, "_run_update_install", fake_install, raising=False)
    monkeypatch.setattr(commands, "_hermes_home", lambda: "/tmp/hermes")

    output = plugin._command("update --dry-run")

    assert calls == [{"ref": "v0.2.10", "dry_run": True, "profiles": None, "all_profiles": False}]
    assert "Update dry-run: v0.2.09 → v0.2.10" in output
    assert "[default] Would copy plugin" in output
    assert "No files changed" in output
    assert "--apply" in output


def test_update_command_apply_can_use_explicit_ref(monkeypatch):
    calls = []
    monkeypatch.setattr(commands, "_latest_release_info", lambda: None)

    def fake_install(ref, *, dry_run, profiles=None, all_profiles=False):
        calls.append((ref, dry_run, profiles, all_profiles))
        return ["[default] Updated plugin at /tmp/hermes/plugins/hermes-progress-tail"]

    monkeypatch.setattr(commands, "_run_update_install", fake_install, raising=False)

    output = plugin._command("update --apply --ref v0.2.10 --profile default")

    assert calls == [("v0.2.10", False, ["default"], False)]
    assert "Update applied: v0.2.09 → v0.2.10" in output
    assert "Updated plugin" in output
    assert "Restart Hermes gateway" in output


def test_update_command_skips_when_latest_is_not_newer(monkeypatch):
    called = False
    monkeypatch.setattr(
        commands,
        "_latest_release_info",
        lambda: {"tag_name": "v0.2.09", "html_url": "https://example.test/v0.2.09"},
    )

    def fake_install(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(commands, "_run_update_install", fake_install, raising=False)

    output = plugin._command("update --dry-run")

    assert called is False
    assert "Already up to date: v0.2.09" in output
    assert "--force" in output


def test_update_command_rejects_unsafe_ref_before_download(monkeypatch):
    called = False

    def fake_install(*args, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(commands, "_run_update_install", fake_install, raising=False)

    output = plugin._command("update --dry-run --ref ../evil")

    assert called is False
    assert "Invalid update ref" in output


def test_update_command_alias_defaults_to_apply(monkeypatch):
    calls = []
    monkeypatch.setattr(
        commands,
        "_latest_release_info",
        lambda: {"tag_name": "v0.2.10", "html_url": "https://example.test/v0.2.10"},
    )

    def fake_install(ref, *, dry_run, profiles=None, all_profiles=False):
        calls.append((ref, dry_run, profiles, all_profiles))
        return ["[default] Copied plugin"]

    monkeypatch.setattr(commands, "_run_update_install", fake_install, raising=False)

    output = plugin._progresstail_update_alias("")

    assert calls == [("v0.2.10", False, None, False)]
    assert "Update applied" in output
    assert "Restart Hermes gateway" in output


def test_update_archive_extract_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload.txt"
    payload.write_text("evil", encoding="utf-8")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(payload, arcname="../evil.txt")

    with pytest.raises(ValueError, match="unsafe archive member"):
        commands._extract_update_archive(archive_path, tmp_path / "extract")
