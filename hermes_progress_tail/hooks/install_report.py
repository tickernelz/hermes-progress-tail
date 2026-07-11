from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..utils.redaction import redact_text


class PatchFailureCategory(str, Enum):
    NONE = ""
    IMPORT_UNAVAILABLE = "import_unavailable"
    TARGET_API_MISSING = "target_api_missing"
    INSTALL_FAILED = "install_failed"


@dataclass(frozen=True)
class PatchStatus:
    name: str
    installed: bool
    target: str
    failure_category: PatchFailureCategory = PatchFailureCategory.NONE
    reason: str = ""

    def __post_init__(self) -> None:
        if self.installed:
            object.__setattr__(self, "failure_category", PatchFailureCategory.NONE)
            object.__setattr__(self, "reason", "")
        elif self.failure_category is PatchFailureCategory.NONE:
            raise ValueError("failed patch status requires a failure category")


@dataclass(frozen=True)
class PatchInstallReport:
    statuses: tuple[PatchStatus, ...] = ()

    def __post_init__(self) -> None:
        names = [status.name for status in self.statuses]
        if len(names) != len(set(names)):
            raise ValueError("duplicate patch status names are not allowed")

    @property
    def any_installed(self) -> bool:
        return any(status.installed for status in self.statuses)

    @property
    def degraded(self) -> bool:
        return any(not status.installed for status in self.statuses)

    def get(self, name: str) -> PatchStatus | None:
        return next((status for status in self.statuses if status.name == name), None)


def safe_patch_reason(value: object) -> str:
    return " ".join(redact_text(str(value)).split())[:240]
