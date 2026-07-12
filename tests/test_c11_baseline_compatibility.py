"""C11 characterization that is intentionally runnable against the exported base."""

import tests.test_b6_corrective_facade_ports as _b6_ports
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering.footer import footer_body
from hermes_progress_tail.runtime.commands import _is_newer_version, _version_parts
from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.state import EnvironmentSnapshot, SessionContext
from tests.test_sticky_footer import (
    test_focused_footer_shows_github_latest_release_update_only_when_newer as _release_output,
)
from tests.test_sticky_footer import (
    test_footer_update_check_uses_github_release_not_local_git_status as _release_source,
)


def _context() -> SessionContext:
    ctx = SessionContext("s", "k", "telegram", "chat", None, None, None)
    ctx.environment = EnvironmentSnapshot(model="model", git_branch="main")
    return ctx


def test_standalone_renderer_and_footer_modes_have_no_update_by_default():
    for mode in ("focused", "sectioned"):
        settings = load_settings({"progress_tail": {"renderer": {"mode": mode}}})
        renderer = ProgressRenderer(settings)
        assert renderer.settings is settings
        body = footer_body(_context(), settings=settings)
        assert "git main" in body
        assert "⬆️ update" not in body


def test_version_comparison_characterization():
    assert _version_parts("release-v1.2.3") == (1, 2, 3)
    assert _is_newer_version("1.2", "v1.2.1")
    assert not _is_newer_version("1.2.0", "v1.2")
    assert not _is_newer_version("2.0", "v1.9.9")
    assert not _is_newer_version("unknown", "v2")


def test_release_notice_and_github_source_characterization(monkeypatch):
    _release_output(monkeypatch)
    _release_source(monkeypatch)


def test_footer_version_provider_seam_characterization():
    legacy = getattr(
        _b6_ports,
        "test_footer_version_is_obtained_from_configured_provider_and_has_safe_default",
        None,
    )
    if legacy is not None:
        legacy()
    else:
        _b6_ports.test_footer_information_has_safe_immutable_default()


def test_copied_namespace_provider_independence_characterization(tmp_path, monkeypatch):
    _b6_ports.test_copied_namespace_provider_ports_are_independent(tmp_path, monkeypatch)
