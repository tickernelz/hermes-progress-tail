from __future__ import annotations

import ast
import importlib
from pathlib import Path


def test_hooks_do_not_import_runtime_plugin() -> None:
    root = Path("hermes_progress_tail/hooks")
    offenders = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "hermes_progress_tail.runtime.plugin" or (
                    node.level and (module == "runtime.plugin" or module == "runtime")
                ):
                    offenders.append(f"{path}:{node.lineno}")
            elif isinstance(node, ast.Import):
                if any(alias.name == "hermes_progress_tail.runtime.plugin" for alias in node.names):
                    offenders.append(f"{path}:{node.lineno}")
    assert offenders == []


def test_hook_modules_import_without_runtime_plugin_registration() -> None:
    for path in Path("hermes_progress_tail/hooks").glob("*.py"):
        importlib.import_module(f"hermes_progress_tail.hooks.{path.stem}")
