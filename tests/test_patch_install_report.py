from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hermes_progress_tail.hooks.install_report import (
    PatchFailureCategory,
    PatchInstallReport,
    PatchStatus,
    safe_patch_reason,
)


def _installed(name: str = "agent") -> PatchStatus:
    return PatchStatus(name=name, installed=True, target="Agent.run")


def _failed(name: str = "telegram") -> PatchStatus:
    return PatchStatus(
        name=name,
        installed=False,
        target="Telegram.send",
        failure_category=PatchFailureCategory.IMPORT_UNAVAILABLE,
        reason="module unavailable",
    )


def test_failure_category_values_are_stable() -> None:
    assert [category.value for category in PatchFailureCategory] == [
        "",
        "import_unavailable",
        "target_api_missing",
        "install_failed",
    ]


def test_empty_all_success_and_partial_reports() -> None:
    empty = PatchInstallReport()
    successful = PatchInstallReport((_installed(),))
    partial = PatchInstallReport((_installed(), _failed()))
    assert (empty.any_installed, empty.degraded) == (False, False)
    assert (successful.any_installed, successful.degraded) == (True, False)
    assert (partial.any_installed, partial.degraded) == (True, True)
    assert partial.get("telegram") == _failed()
    assert partial.get("missing") is None


def test_reports_and_statuses_are_frozen() -> None:
    status = _installed()
    report = PatchInstallReport((status,))
    with pytest.raises(FrozenInstanceError):
        status.installed = False  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        report.statuses = ()  # type: ignore[misc]


def test_duplicate_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        PatchInstallReport((_installed("same"), _failed("same")))


def test_status_coherence_normalizes_success_and_rejects_uncategorized_failure() -> None:
    status = PatchStatus(
        name="agent",
        installed=True,
        target="Agent.run",
        failure_category=PatchFailureCategory.INSTALL_FAILED,
        reason="ignored",
    )
    assert status.failure_category is PatchFailureCategory.NONE
    assert status.reason == ""
    with pytest.raises(ValueError, match="failure category"):
        PatchStatus(name="agent", installed=False, target="Agent.run")


def test_safe_reason_redacts_normalizes_whitespace_and_truncates() -> None:
    reason = safe_patch_reason(
        "token=supersecretvalue\n password=hunter2\t"
        "/home/alice/private/plugin.py   " + "diagnostic " * 50
    )
    assert "supersecretvalue" not in reason
    assert "hunter2" not in reason
    assert "\n" not in reason and "\t" not in reason and "  " not in reason
    assert len(reason) == 240


def test_safe_reason_accepts_non_string_without_using_repr() -> None:
    class Diagnostic:
        def __str__(self) -> str:
            return "Bearer abcdefghijklmnop"

        def __repr__(self) -> str:
            return "SECRET_REPR"

    reason = safe_patch_reason(Diagnostic())
    assert reason == "Bearer [redacted]"
    assert "SECRET_REPR" not in reason
