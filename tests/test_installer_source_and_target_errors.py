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


@pytest.mark.parametrize("existing", [False, True])
def test_copy_failure_cleans_partial_stage_and_restores_target(tmp_path, monkeypatch, existing):
    source = _plugin_source(tmp_path)
    target = tmp_path / "target"
    if existing:
        target.mkdir()
        (target / "old.txt").write_text("old", encoding="utf-8")

    def fail_copytree(source, destination, **kwargs):
        destination.mkdir()
        (destination / "partial.txt").write_text("partial", encoding="utf-8")
        raise OSError("copy denied")

    monkeypatch.setattr(installer.shutil, "copytree", fail_copytree)
    with pytest.raises(OSError, match="copy denied"):
        installer._copy_plugin(source, target)

    assert target.is_dir() is existing
    if existing:
        assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert source.exists()
    assert not list(tmp_path.glob(".target.*"))


@pytest.mark.parametrize("existing", [False, True])
def test_install_write_failure_restores_plugin_and_config(tmp_path, monkeypatch, existing):
    source = _plugin_source(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    original = "plugins:\n  enabled: [other]\n"
    (home / "config.yaml").write_text(original, encoding="utf-8")
    target = home / "plugins" / installer.PLUGIN_NAME
    if existing:
        target.mkdir(parents=True)
        (target / "old.txt").write_text("old", encoding="utf-8")

    def fail_write(path, data):
        raise OSError("write denied")

    monkeypatch.setattr(installer, "_write_yaml", fail_write)
    with pytest.raises(OSError, match="write denied"):
        installer.install(home, source)

    assert (home / "config.yaml").read_text(encoding="utf-8") == original
    assert target.is_dir() is existing
    if existing:
        assert (target / "old.txt").read_text(encoding="utf-8") == "old"
        assert not (target / "payload.txt").exists()
    assert not list((home / "plugins").glob(f".{installer.PLUGIN_NAME}.*"))
    backups = list((home / installer.PLUGIN_NAME / "backups").glob("*/config.yaml"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


def test_install_copy_failure_restores_old_target_and_keeps_config(tmp_path, monkeypatch):
    source = _plugin_source(tmp_path)
    home = tmp_path / "home"
    target = home / "plugins" / installer.PLUGIN_NAME
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old", encoding="utf-8")
    original = "plugins:\n  enabled: []\n"
    (home / "config.yaml").write_text(original, encoding="utf-8")

    def fail_copytree(source, destination, **kwargs):
        destination.mkdir()
        (destination / "partial.txt").write_text("partial", encoding="utf-8")
        raise OSError("copy denied")

    monkeypatch.setattr(installer.shutil, "copytree", fail_copytree)
    with pytest.raises(OSError, match="copy denied"):
        installer.install(home, source)

    assert (home / "config.yaml").read_text(encoding="utf-8") == original
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert not list(target.parent.glob(f".{installer.PLUGIN_NAME}.*"))
    backups = list((home / installer.PLUGIN_NAME / "backups").glob("*/config.yaml"))
    assert len(backups) == 1


def test_write_yaml_replace_failure_preserves_old_config_and_cleans_temp(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("old: true\n", encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace denied")

    monkeypatch.setattr(installer.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace denied"):
        installer._write_yaml(config, {"new": True})

    assert config.read_text(encoding="utf-8") == "old: true\n"
    assert not list(tmp_path.glob(".config.yaml.*"))


def test_config_rollback_restores_before_cleanup_failure(tmp_path, monkeypatch):
    source = _plugin_source(tmp_path)
    home = tmp_path / "home"
    target = home / "plugins" / installer.PLUGIN_NAME
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old", encoding="utf-8")
    original = OSError("config denied")
    monkeypatch.setattr(installer, "_write_yaml", lambda *args: (_ for _ in ()).throw(original))
    real_rmtree = installer.shutil.rmtree

    def fail_new_cleanup(path, *args, **kwargs):
        if ".discard." in Path(path).name:
            raise OSError("cleanup denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(installer.shutil, "rmtree", fail_new_cleanup)
    with pytest.raises(OSError, match="config denied") as raised:
        installer.install(home, source)
    assert raised.value is original
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"


def test_replace_failure_restores_before_stage_cleanup(tmp_path, monkeypatch):
    source = _plugin_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    real_rename = Path.rename
    real_rmtree = installer.shutil.rmtree

    def fail_stage_rename(path, destination):
        if ".stage." in path.name:
            raise OSError("placement denied")
        return real_rename(path, destination)

    def fail_stage_cleanup(path, *args, **kwargs):
        if ".stage." in Path(path).name:
            raise OSError("cleanup denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(Path, "rename", fail_stage_rename)
    monkeypatch.setattr(installer.shutil, "rmtree", fail_stage_cleanup)
    with pytest.raises(OSError, match="placement denied"):
        installer._copy_plugin(source, target)
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"


def test_rollback_collision_sentinel_is_untouched(tmp_path):
    source = _plugin_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    sentinel = tmp_path / ".target.rollback"
    sentinel.mkdir()
    (sentinel / "keep.txt").write_text("keep", encoding="utf-8")
    installer._copy_plugin(source, target)
    assert (sentinel / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_copy_committed_cleanup_failure_is_successful(tmp_path, monkeypatch):
    source = _plugin_source(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    real_rmtree = installer.shutil.rmtree

    def fail_rollback_cleanup(path, *args, **kwargs):
        if ".rollback" in Path(path).name:
            raise OSError("cleanup denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(installer.shutil, "rmtree", fail_rollback_cleanup)
    installer._copy_plugin(source, target)
    assert (target / "payload.txt").read_text(encoding="utf-8") == "new"


def test_install_committed_cleanup_failure_still_removes_legacy(tmp_path, monkeypatch):
    source = _plugin_source(tmp_path)
    home = tmp_path / "home"
    target = home / "plugins" / installer.PLUGIN_NAME
    target.mkdir(parents=True)
    legacy = home / "plugins" / installer.LEGACY_PLUGIN_NAME
    legacy.mkdir()
    real_rmtree = installer.shutil.rmtree

    def fail_rollback_cleanup(path, *args, **kwargs):
        if ".rollback" in Path(path).name:
            raise OSError("cleanup denied")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(installer.shutil, "rmtree", fail_rollback_cleanup)
    installer.install(home, source)
    assert not legacy.exists()
    assert (target / "payload.txt").exists()


def test_write_yaml_preserves_original_error_when_temp_unlink_fails(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    original = OSError("replace denied")
    monkeypatch.setattr(installer.os, "replace", lambda *args: (_ for _ in ()).throw(original))
    monkeypatch.setattr(
        Path, "unlink", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("unlink denied"))
    )
    with pytest.raises(OSError, match="replace denied") as raised:
        installer._write_yaml(config, {"new": True})
    assert raised.value is original


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
