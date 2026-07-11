#!/usr/bin/env python3
"""Enforce repository-wide structural constraints."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

MAX_LINES = 600


def tracked_text_files(root: Path) -> tuple[Path, ...]:
    """Return existing, non-NUL files tracked by Git under *root*."""
    root = root.resolve()
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    paths: list[Path] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        path = root / raw_path.decode("utf-8", errors="surrogateescape")
        try:
            content = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue
        if b"\0" not in content:
            paths.append(path)
    return tuple(sorted(paths, key=lambda path: str(path)))


def line_limit_violations(
    paths: Iterable[Path], max_lines: int = MAX_LINES
) -> tuple[tuple[Path, int], ...]:
    """Return text files exceeding *max_lines* in diagnostic order."""
    violations: list[tuple[Path, int]] = []
    for path in paths:
        try:
            content = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue
        if b"\0" in content:
            continue
        line_count = len(content.splitlines())
        if line_count > max_lines:
            violations.append((path, line_count))
    return tuple(sorted(violations, key=lambda item: (-item[1], str(item[0]))))


def repository_violations(root: Path) -> tuple[tuple[Path, int], ...]:
    """Return structure violations among files tracked below *root*."""
    return line_limit_violations(tracked_text_files(root))


def main() -> int:
    root = Path.cwd().resolve()
    violations = repository_violations(root)
    for path, line_count in violations:
        print(f"{path.relative_to(root)}: {line_count} lines (limit {MAX_LINES})")
    return int(bool(violations))


if __name__ == "__main__":
    raise SystemExit(main())
