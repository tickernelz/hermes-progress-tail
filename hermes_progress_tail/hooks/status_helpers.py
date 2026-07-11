from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .install_report import PatchFailureCategory, PatchStatus, safe_patch_reason


def structured_patch_status(
    *,
    name: str,
    target_label: str,
    target: Any | None,
    resolver: Callable[[], Any],
    members: tuple[str, ...],
    mutate: Callable[[Any], bool],
) -> PatchStatus:
    """Resolve, validate, mutate, and classify one independently owned patch seam."""
    if target is None:
        try:
            target = resolver()
        except Exception as exc:
            return PatchStatus(
                name,
                False,
                target_label,
                PatchFailureCategory.IMPORT_UNAVAILABLE,
                safe_patch_reason(exc),
            )
    if not all(callable(getattr(target, member, None)) for member in members):
        return PatchStatus(
            name,
            False,
            target_label,
            PatchFailureCategory.TARGET_API_MISSING,
            "required callable API missing",
        )
    try:
        installed = mutate(target)
    except Exception as exc:
        return PatchStatus(
            name,
            False,
            target_label,
            PatchFailureCategory.INSTALL_FAILED,
            safe_patch_reason(exc),
        )
    if not installed:
        return PatchStatus(
            name,
            False,
            target_label,
            PatchFailureCategory.INSTALL_FAILED,
            "installer returned false",
        )
    return PatchStatus(name, True, target_label)
