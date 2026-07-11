import io
import json
import sys
import tarfile
from types import ModuleType, SimpleNamespace

import pytest

from hermes_progress_tail.runtime import commands


@pytest.mark.parametrize(
    ("args", "message"),
    [("--ref 'unterminated", "No closing quotation"), ("--wat", "unknown option --wat")],
)
def test_update_parser_rejects_malformed_and_unknown_arguments(args, message):
    result = commands._parse_update_tokens(args)
    assert isinstance(result, str)
    assert result.startswith("Invalid update arguments:")
    assert message in result


def test_update_parser_supports_flags_equals_and_ordered_profiles():
    result = commands._parse_update_tokens(
        "--dry-run --force --all-profiles --ref=v2.0.0 --profile=a,b --profile c,a"
    )
    assert result == {
        "dry_run": True,
        "apply": False,
        "force": True,
        "ref": "v2.0.0",
        "profiles": ["a", "b", "c", "a"],
        "all_profiles": True,
    }


@pytest.mark.parametrize(
    ("args", "message"),
    [("--ref", "--ref requires a value"), ("--profile", "--profile requires a value")],
)
def test_update_parser_requires_option_values(args, message):
    assert message in commands._parse_update_tokens(args)


def test_invalid_ref_and_versions_are_rejected():
    assert commands._validate_update_ref("v1;rm") == ""
    assert commands._version_parts("release-next") == ()
    assert commands._is_newer_version("invalid", "v2.0") is False
    assert commands._is_newer_version("v1.0", "invalid") is False


def test_update_parse_failure_includes_usage():
    output = commands._update_command("--dry-run --unknown")
    assert "unknown option --unknown" in output
    assert "Usage: /progresstail update --dry-run" in output
    assert "Options: --ref vX.Y.Z" in output


def test_update_without_latest_requests_explicit_ref(monkeypatch):
    monkeypatch.setattr(commands, "_fresh_latest_release_info", lambda: None)
    output = commands._update_command("--dry-run")
    assert output == ("Could not determine latest hermes-progress-tail release. Use --ref vX.Y.Z.")


def test_update_install_exception_is_redacted(monkeypatch):
    secret = "super-secret-value"
    monkeypatch.setattr(
        commands,
        "_run_update_install",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"token={secret}")),
    )
    output = commands._update_command("--apply --force --ref v9.0.0")
    assert output.startswith("Update failed:")
    assert secret not in output
    assert "[redacted_env]" in output


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


@pytest.mark.parametrize("payload", [None, {"html_url": "https://example.test/release"}])
def test_latest_release_tagless_payload_caches_none(monkeypatch, payload):
    old = dict(commands._LATEST_RELEASE_CACHE)
    try:
        body = json.dumps(payload).encode()
        monkeypatch.setattr(commands.urllib.request, "urlopen", lambda *a, **k: _Response(body))
        assert commands._latest_release_info(refresh=True) is None
        assert commands._LATEST_RELEASE_CACHE["info"] is None
        assert commands._LATEST_RELEASE_CACHE["checked_at"] > 0
    finally:
        commands._LATEST_RELEASE_CACHE.clear()
        commands._LATEST_RELEASE_CACHE.update(old)


def test_latest_release_network_failure_caches_none(monkeypatch):
    old = dict(commands._LATEST_RELEASE_CACHE)
    try:
        monkeypatch.setattr(
            commands.urllib.request,
            "urlopen",
            lambda *a, **k: (_ for _ in ()).throw(OSError("offline")),
        )
        assert commands._latest_release_info(refresh=True) is None
        assert commands._LATEST_RELEASE_CACHE["info"] is None
    finally:
        commands._LATEST_RELEASE_CACHE.clear()
        commands._LATEST_RELEASE_CACHE.update(old)


def test_latest_release_malformed_json_caches_none(monkeypatch):
    old = dict(commands._LATEST_RELEASE_CACHE)
    checked_at = 1234.5
    try:
        monkeypatch.setattr(commands.time, "time", lambda: checked_at)
        monkeypatch.setattr(commands.urllib.request, "urlopen", lambda *a, **k: _Response(b"{"))

        assert commands._latest_release_info(refresh=True) is None
        expected_cache = {"checked_at": checked_at, "info": None}
        assert expected_cache == commands._LATEST_RELEASE_CACHE
    finally:
        commands._LATEST_RELEASE_CACHE.clear()
        commands._LATEST_RELEASE_CACHE.update(old)


def _write_tar(path, members):
    with tarfile.open(path, "w:gz") as archive:
        for member, data in members:
            archive.addfile(member, io.BytesIO(data) if data is not None else None)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_archive_rejects_links_and_unsupported_members(tmp_path, kind):
    member = tarfile.TarInfo("root/item")
    if kind == "symlink":
        member.type, member.linkname = tarfile.SYMTYPE, "target"
    elif kind == "hardlink":
        member.type, member.linkname = tarfile.LNKTYPE, "target"
    else:
        member.type = tarfile.FIFOTYPE
    archive = tmp_path / f"{kind}.tar.gz"
    _write_tar(archive, [(member, None)])

    expected = "unsafe archive link" if kind != "fifo" else "unsupported archive member"
    with pytest.raises(ValueError, match=expected):
        commands._extract_update_archive(archive, tmp_path / "extract")


@pytest.mark.filterwarnings("ignore:Python 3.14 will, by default, filter extracted tar archives")
def test_archive_extracts_valid_multiple_members(tmp_path):
    root = tarfile.TarInfo("project")
    root.type = tarfile.DIRTYPE
    root.mode = 0o755
    directory = tarfile.TarInfo("project/nested")
    directory.type = tarfile.DIRTYPE
    directory.mode = 0o755
    file_member = tarfile.TarInfo("project/nested/data.txt")
    payload = b"characterized contents"
    file_member.size = len(payload)
    file_member.mode = 0o644
    archive = tmp_path / "valid.tar.gz"
    _write_tar(archive, [(root, None), (directory, None), (file_member, payload)])
    destination = tmp_path / "extract"

    commands._extract_update_archive(archive, destination)
    assert (destination / "project/nested").is_dir()
    assert (destination / "project/nested/data.txt").read_bytes() == payload


def test_download_source_uses_url_timeout_and_returns_root(monkeypatch, tmp_path):
    observed = {}

    def urlopen(request, timeout):
        observed.update(
            url=request.full_url, timeout=timeout, user_agent=request.headers["User-agent"]
        )
        return _Response(b"archive bytes")

    def extract(archive, destination):
        observed["archive"] = archive.read_bytes()
        (destination / "source-root").mkdir()

    monkeypatch.setattr(commands.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(commands, "_extract_update_archive", extract)
    root = commands._download_update_source("v1.2.3", tmp_path)
    assert root == tmp_path / "source-root"
    assert observed == {
        "url": "https://github.com/tickernelz/hermes-progress-tail/archive/v1.2.3.tar.gz",
        "timeout": 30,
        "user_agent": "hermes-progress-tail-update",
        "archive": b"archive bytes",
    }


def test_download_source_without_root_raises_exact_error(monkeypatch, tmp_path):
    monkeypatch.setattr(commands.urllib.request, "urlopen", lambda *a, **k: _Response(b"bytes"))
    monkeypatch.setattr(commands, "_extract_update_archive", lambda *a: None)
    with pytest.raises(
        FileNotFoundError, match="^downloaded archive did not contain a source directory$"
    ):
        commands._download_update_source("v1", tmp_path)


def test_run_update_install_forwards_options_and_cleans_temp(monkeypatch, tmp_path):
    installer = ModuleType("hermes_progress_tail.installer")
    observed = {}

    def download(ref, destination):
        observed.update(ref=ref, temporary=destination)
        source = destination / "source"
        source.mkdir()
        return source

    def install(home, source, **kwargs):
        observed.update(home=home, source=source, kwargs=kwargs, existed=source.is_dir())
        return SimpleNamespace(messages=["installed one", "installed two"])

    installer.install_many = install
    monkeypatch.setitem(sys.modules, "hermes_progress_tail.installer", installer)
    monkeypatch.setattr(commands, "_download_update_source", download)
    monkeypatch.setattr(commands, "_hermes_home", lambda: tmp_path / "hermes")

    result = commands._run_update_install(
        "v2", dry_run=True, profiles=["alpha", "beta"], all_profiles=True
    )
    assert result == ["installed one", "installed two"]
    assert observed["ref"] == "v2"
    assert observed["home"] == tmp_path / "hermes"
    assert observed["source"] == observed["temporary"] / "source"
    assert observed["existed"] is True
    assert observed["kwargs"] == {
        "profiles": ["alpha", "beta"],
        "all_profiles": True,
        "set_display_off": True,
        "dry_run": True,
    }
    assert not observed["temporary"].exists()
