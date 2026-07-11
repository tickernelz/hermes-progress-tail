#!/usr/bin/env python3
"""Deterministically audit SessionContext construction, aliases, and helpers."""

import ast
import json
import subprocess
import sys
from pathlib import Path


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def scan_sources(sources: dict[str, str]) -> dict[str, list[dict[str, object]]]:
    """Return deterministic constructor evidence for repository-relative sources."""
    records: dict[str, list[dict[str, object]]] = {"calls": [], "aliases": [], "helpers": []}
    for path, source_text in sources.items():
        tree = ast.parse(source_text, path)
        aliases = {"SessionContext": "direct"}
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.endswith("models.state")
            ):
                for item in node.names:
                    if item.name == "SessionContext":
                        name = item.asname or item.name
                        aliases[name] = "import_alias" if item.asname else "direct"
                        if item.asname:
                            records["aliases"].append(
                                {"path": path, "line": node.lineno, "name": name, "kind": "import"}
                            )
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                source = dotted(value)
                if source.split(".")[-1] in aliases or source.endswith(".SessionContext"):
                    for target in targets:
                        if isinstance(target, ast.Name):
                            aliases[target.id] = "assignment_alias"
                            records["aliases"].append(
                                {
                                    "path": path,
                                    "line": node.lineno,
                                    "name": target.id,
                                    "kind": "assignment",
                                }
                            )
        parents = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        helper_names = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted(node.func)
            kind = "qualified" if "." in name and name.endswith(".SessionContext") else None
            if kind is None:
                kind = aliases.get(name)
            if kind is None:
                continue
            records["calls"].append(
                {
                    "path": path,
                    "line": node.lineno,
                    "callee": name,
                    "kind": kind,
                    "positional_arity": len(node.args),
                    "keywords": sorted(k.arg or "**" for k in node.keywords),
                }
            )
            parent = parents.get(node)
            while parent is not None and not isinstance(
                parent, (ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                parent = parents.get(parent)
            if parent is not None:
                key = (path, parent.lineno, parent.name)
                if key not in helper_names:
                    helper_names.add(key)
                    records["helpers"].append(
                        {"path": path, "line": parent.lineno, "name": parent.name}
                    )
    for values in records.values():
        values.sort(
            key=lambda item: tuple(
                str(item.get(key, "")) for key in ("path", "line", "name", "callee")
            )
        )
    return records


def scan_repository(root: Path) -> dict[str, list[dict[str, object]]]:
    files = subprocess.check_output(["git", "ls-files", "*.py"], cwd=root, text=True).splitlines()
    return scan_sources({path: (root / path).read_text() for path in files})


def render_inventory(records: dict[str, list[dict[str, object]]]) -> str:
    return json.dumps(records, sort_keys=True, separators=(",", ":")) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parents[1]
    fixture = root / "tests/fixtures/session_context_constructors.json"
    records = scan_repository(root)
    actual = render_inventory(records)
    if "--write" in argv:
        fixture.write_text(actual)
    elif not fixture.exists() or fixture.read_text() != actual:
        raise SystemExit(
            "SessionContext constructor inventory is stale; run this script with --write"
        )
    print(
        f"SessionContext inventory: {len(records['calls'])} calls, "
        f"{len(records['aliases'])} aliases, {len(records['helpers'])} enclosing helpers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
