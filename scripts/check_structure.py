#!/usr/bin/env python3
"""Enforce repository-wide structural constraints."""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_LINES = 600
PACKAGE = "hermes_progress_tail"


@dataclass(frozen=True)
class DependencyViolation:
    path: Path
    line: int
    imported: str


def tracked_text_files(root: Path) -> tuple[Path, ...]:
    root = root.resolve()
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
    paths = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = root / raw.decode("utf-8", errors="surrogateescape")
        try:
            content = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue
        if b"\0" not in content:
            paths.append(path)
    return tuple(sorted(paths, key=str))


def line_limit_violations(
    paths: Iterable[Path], max_lines: int = MAX_LINES
) -> tuple[tuple[Path, int], ...]:
    violations = []
    for path in paths:
        try:
            content = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            continue
        if b"\0" in content:
            continue
        count = len(content.splitlines())
        if count > max_lines:
            violations.append((path, count))
    return tuple(sorted(violations, key=lambda item: (-item[1], str(item[0]))))


def _resolved_imports(path: Path, root: Path) -> Iterable[tuple[int, str]]:
    rel = path.relative_to(root).with_suffix("")
    module = ".".join(rel.parts)
    package = module.rsplit(".", 1)[0]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
                target = ".".join(filter(None, (base, node.module or "")))
            else:
                target = node.module or ""
            yield node.lineno, target
            for alias in node.names:
                yield node.lineno, ".".join(filter(None, (target, alias.name)))


def dependency_violations(paths: Iterable[Path], root: Path) -> tuple[DependencyViolation, ...]:
    forbidden = f"{PACKAGE}.runtime.plugin"
    found = []
    seen: set[tuple[Path, int]] = set()
    for path in paths:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if path.suffix != ".py" or len(rel.parts) < 3 or rel.parts[0] != PACKAGE:
            continue
        area = rel.parts[1]
        checked = area in {"hooks", "rendering"} or (area == "runtime" and rel.name != "plugin.py")
        if not checked:
            continue
        facade = f"{PACKAGE}.plugin"
        for line, imported in _resolved_imports(path, root):
            if (imported in {facade, forbidden} or imported.startswith(forbidden + ".")) and (
                path,
                line,
            ) not in seen:
                found.append(DependencyViolation(path, line, forbidden))
                seen.add((path, line))
    return tuple(sorted(set(found), key=lambda item: (str(item.path), item.line, item.imported)))


def repository_violations(root: Path):
    paths = tracked_text_files(root)
    return (*line_limit_violations(paths), *dependency_violations(paths, root.resolve()))


def main() -> int:
    root = Path.cwd().resolve()
    violations = repository_violations(root)
    for violation in violations:
        if isinstance(violation, DependencyViolation):
            print(
                f"{violation.path.relative_to(root)}:{violation.line}: forbidden dependency on {violation.imported}"
            )
        else:
            path, count = violation
            print(f"{path.relative_to(root)}: {count} lines (limit {MAX_LINES})")
    return int(bool(violations))


if __name__ == "__main__":
    raise SystemExit(main())
