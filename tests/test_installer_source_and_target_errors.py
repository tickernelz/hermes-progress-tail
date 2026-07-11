from __future__ import annotations

import runpy
from pathlib import Path

import pytest
import yaml

from hermes_progress_tail.cli import installer


def _plugin_source(root: Path) -> Path:
    source = root / "source"
    (source / "hermes_progress_tail").mkdir(parents=True)
    (source / "plugin.yaml").write_text("name: hermes-progress-tail\n", encoding="utf-8")
    (source / "payload.txt").write_text("new", encoding="utf-8")
    return source


def _package_source(root: Path) -> Path:
    source = root / "package"
    (source / "runtime").mkdir(parents=True)
    (source / "rendering").mkdir()
    for relative in ("__init__.py", "runtime/plugin.py", "rendering/renderer.py"):
        (source / relative).write_text("", encoding="utf-8")
    return source


@pytest.mark.parametrize("text", ["null\n", "[]\n", "plain scalar\n"])
def test_read_yaml_treats_non_mapping_documents_as_empty(tmp_path, text):
    config = tmp_path / "home" / "config.yaml"
    config.parent.mkdir()
    config.write_text(text, encoding="utf-8")

    assert installer._read_yaml(config) == {}


def test_read_yaml_missing_file_is_empty_and_malformed_yaml_is_reported(tmp_path):
    missing = tmp_path / "home" / "config.yaml"
    assert installer._read_yaml(missing) == {}

    missing.parent.mkdir()
    missing.write_text("plugins: [\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        installer._read_yaml(missing)


def test_copy_plugin_replaces_directory_and_filters_nested_artifacts(tmp_path):
    source = _plugin_source(tmp_path)
    (source / "nested" / "__pycache__").mkdir(parents=True)
    (source / "nested" / "__pycache__" / "cache.pyc").write_bytes(b"cache")
    target = tmp_path / "target"
    target.mkdir()
    (target / "stale.txt").write_text("stale", encoding="utf-8")

    installer._copy_plugin(source, target)

    assert (target / "payload.txt").read_text(encoding="utf-8") == "new"
    assert not (target / "stale.txt").exists()
    assert not (target / "nested" / "__pycache__").exists()


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_copy_plugin_rejects_non_directory_target_without_damaging_it(tmp_path, kind):
    source = _plugin_source(tmp_path)
    target = tmp_path / "target"
    if kind == "file":
        target.write_text("keep", encoding="utf-8")
    else:
        referent = tmp_path / "referent"
        referent.mkdir()
        (referent / "keep.txt").write_text("keep", encoding="utf-8")
        target.symlink_to(referent, target_is_directory=True)

    with pytest.raises(OSError):
        installer._copy_plugin(source, target)

    if kind == "file":
        assert target.read_text(encoding="utf-8") == "keep"
    else:
        assert target.is_symlink()
        assert (target / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_copy_failure_leaves_no_partial_target_when_copytree_fails_before_creation(
    tmp_path, monkeypatch
):
    source = _plugin_source(tmp_path)
    target = tmp_path / "target"

    def fail_copytree(*args, **kwargs):
        raise OSError("copy denied")

    monkeypatch.setattr(installer.shutil, "copytree", fail_copytree)
    with pytest.raises(OSError, match="copy denied"):
        installer._copy_plugin(source, target)

    assert not target.exists()
    assert source.exists()


def test_install_write_failure_preserves_backup_and_copied_plugin(tmp_path, monkeypatch):
    source = _plugin_source(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    original = "plugins:\n  enabled: [other]\n"
    (home / "config.yaml").write_text(original, encoding="utf-8")

    def fail_write(path, data):
        raise OSError("write denied")

    monkeypatch.setattr(installer, "_write_yaml", fail_write)
    with pytest.raises(OSError, match="write denied"):
        installer.install(home, source)

    assert (home / "config.yaml").read_text(encoding="utf-8") == original
    assert (home / "plugins" / installer.PLUGIN_NAME / "payload.txt").exists()
    backups = list((home / installer.PLUGIN_NAME / "backups").glob("*/config.yaml"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_install_copy_failure_keeps_config_and_backup_but_removes_old_target(tmp_path, monkeypatch):
    source = _plugin_source(tmp_path)
    home = tmp_path / "home"
    target = home / "plugins" / installer.PLUGIN_NAME
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old", encoding="utf-8")
    original = "plugins:\n  enabled: []\n"
    (home / "config.yaml").write_text(original, encoding="utf-8")

    def fail_copytree(*args, **kwargs):
        raise OSError("copy denied")

    monkeypatch.setattr(installer.shutil, "copytree", fail_copytree)
    with pytest.raises(OSError, match="copy denied"):
        installer.install(home, source)

    assert (home / "config.yaml").read_text(encoding="utf-8") == original
    assert not target.exists()
    backups = list((home / installer.PLUGIN_NAME / "backups").glob("*/config.yaml"))
    assert len(backups) == 1


def test_default_source_search_uses_package_layout_and_reports_no_match(tmp_path, monkeypatch):
    package = _package_source(tmp_path)
    fake_module = package / "cli" / "installer.py"
    fake_module.parent.mkdir()
    fake_module.write_text("", encoding="utf-8")
    monkeypatch.setattr(installer, "__file__", str(fake_module))
    assert installer._default_source_dir() == package

    isolated = tmp_path / "isolated" / "cli" / "installer.py"
    isolated.parent.mkdir(parents=True)
    isolated.write_text("", encoding="utf-8")
    monkeypatch.setattr(installer, "__file__", str(isolated))
    with pytest.raises(FileNotFoundError, match="could not locate"):
        installer._default_source_dir()


def test_compatibility_module_main_forwards_fully_patched_entrypoint(monkeypatch):
    import hermes_progress_tail.cli.interactive as interactive

    calls = []

    def fake_main():
        calls.append("called")
        return 23

    monkeypatch.setattr(interactive, "main", fake_main)
    with pytest.raises(SystemExit) as raised:
        runpy.run_module("hermes_progress_tail.installer", run_name="__main__")

    assert raised.value.code == 23
    assert calls == ["called"]
