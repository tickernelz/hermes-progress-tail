from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FooterInfo:
    current_version: str = ""
    latest_tag: str = ""
    latest_url: str = ""


class FooterInfoProvider(Protocol):
    def __call__(self) -> FooterInfo: ...


def no_footer_info() -> FooterInfo:
    return FooterInfo()


def version_parts(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+(?:\.\d+){0,3})", str(value or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer_version(current: str, latest: str) -> bool:
    current_parts = version_parts(current)
    latest_parts = version_parts(latest)
    if not current_parts or not latest_parts:
        return False
    width = max(len(current_parts), len(latest_parts))
    return latest_parts + (0,) * (width - len(latest_parts)) > current_parts + (0,) * (
        width - len(current_parts)
    )
