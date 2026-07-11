#!/usr/bin/env python3
"""Enforce repository-wide structural constraints."""

from __future__ import annotations

import ast
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_LINES = 600
MAX_RENDERER_LINES = 399
MAX_SESSION_FIELDS = 30
PACKAGE = "hermes_progress_tail"
TARGET_CLASSES = frozenset({"ProgressRenderer", "DelegateProgressRenderer"})


@dataclass(frozen=True)
class DependencyViolation:
    path: Path
    line: int
    imported: str


@dataclass(frozen=True)
class RendererSizeViolation:
    path: Path
    count: int


@dataclass(frozen=True)
class SessionContextFieldViolation:
    path: Path
    line: int
    count: int


@dataclass(frozen=True)
class ClassMutationViolation:
    path: Path
    line: int
    target: str
    operation: str


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
        if area not in {"hooks", "rendering", "runtime"} or (
            area == "runtime" and rel.name == "plugin.py"
        ):
            continue
        facade = f"{PACKAGE}.plugin"
        for line, imported in _resolved_imports(path, root):
            rendering_runtime = area == "rendering" and (
                imported == f"{PACKAGE}.runtime" or imported.startswith(f"{PACKAGE}.runtime.")
            )
            old_boundary = imported in {facade, forbidden} or imported.startswith(forbidden + ".")
            if (rendering_runtime or old_boundary) and (path, line) not in seen:
                found.append(
                    DependencyViolation(
                        path, line, f"{PACKAGE}.runtime" if rendering_runtime else forbidden
                    )
                )
                seen.add((path, line))
    return tuple(sorted(set(found), key=lambda item: (str(item.path), item.line, item.imported)))


def renderer_size_violations(
    paths: Iterable[Path], root: Path
) -> tuple[RendererSizeViolation, ...]:
    wanted = root.resolve() / PACKAGE / "rendering" / "renderer.py"
    for path in paths:
        if path.resolve() == wanted:
            count = len(path.read_bytes().splitlines())
            return () if count <= MAX_RENDERER_LINES else (RendererSizeViolation(path, count),)
    return ()


def session_context_field_violations(
    paths: Iterable[Path], root: Path
) -> tuple[SessionContextFieldViolation, ...]:
    found = []
    for path in paths:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if path.suffix != ".py" or not rel.parts or rel.parts[0] != PACKAGE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "SessionContext":
                count = sum(isinstance(item, ast.AnnAssign) for item in node.body)
                if count > MAX_SESSION_FIELDS:
                    found.append(SessionContextFieldViolation(path, node.lineno, count))
    return tuple(sorted(found, key=lambda x: (str(x.path), x.line, x.count)))


def _attribute_target(node: ast.AST) -> str | None:
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in TARGET_CLASSES
    ):
        return node.value.id
    return None


def class_mutation_violations(
    paths: Iterable[Path], root: Path
) -> tuple[ClassMutationViolation, ...]:
    found = []
    for path in paths:
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if path.suffix != ".py" or not rel.parts or rel.parts[0] != PACKAGE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        class_lines = {
            node.name: node.lineno
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name in TARGET_CLASSES
        }
        helpers = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        active_helpers: set[int] = set()

        def scoped_helpers(
            statements: Iterable[ast.stmt],
        ) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
            result = {}

            class Collector(ast.NodeVisitor):
                def visit_FunctionDef(self, definition: ast.FunctionDef) -> None:
                    result[definition.name] = definition

                def visit_AsyncFunctionDef(self, definition: ast.AsyncFunctionDef) -> None:
                    result[definition.name] = definition

                def visit_ClassDef(self, definition: ast.ClassDef) -> None:
                    return None

            collector = Collector()
            for statement in statements:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result[statement.name] = statement
                else:
                    collector.visit(statement)
            return result

        class MutationVisitor(ast.NodeVisitor):
            def __init__(
                self,
                source_path: Path,
                class_definition_lines: dict[str, int],
                helper_definitions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
                active: set[int],
                reported_line: int | None = None,
                operation_override: str | None = None,
            ):
                self.source_path = source_path
                self.class_definition_lines = class_definition_lines
                self.helper_definitions = helper_definitions
                self.active = active
                self.reported_line = reported_line
                self.operation_override = operation_override

            def line(self, node: ast.AST) -> int:
                return self.reported_line or node.lineno

            def record(self, node: ast.AST, target: str, operation: str) -> None:
                line = self.line(node)
                class_line = self.class_definition_lines.get(target)
                if class_line is None or line > class_line:
                    found.append(
                        ClassMutationViolation(
                            self.source_path, line, target, self.operation_override or operation
                        )
                    )

            def inspect_targets(self, node: ast.AST, targets: Iterable[ast.AST]) -> None:
                for target_node in targets:
                    target = _attribute_target(target_node)
                    if target:
                        self.record(node, target, "assignment")

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                return None

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                return None

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                return None

            def visit_Assign(self, node: ast.Assign) -> None:
                self.inspect_targets(node, node.targets)
                self.generic_visit(node)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                self.inspect_targets(node, (node.target,))
                self.generic_visit(node)

            def visit_AugAssign(self, node: ast.AugAssign) -> None:
                self.inspect_targets(node, (node.target,))
                self.generic_visit(node)

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name) and node.func.id == "setattr" and node.args:
                    target_node = node.args[0]
                    if isinstance(target_node, ast.Name) and target_node.id in TARGET_CLASSES:
                        self.record(node, target_node.id, "setattr")
                elif (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "exec"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    try:
                        executed = ast.parse(node.args[0].value)
                    except SyntaxError:
                        executed = ast.Module(body=[], type_ignores=[])
                    MutationVisitor(
                        self.source_path,
                        self.class_definition_lines,
                        self.helper_definitions,
                        self.active,
                        self.line(node),
                        operation_override="exec",
                    ).visit(executed)
                elif isinstance(node.func, ast.Name) and node.func.id in self.helper_definitions:
                    helper_name = node.func.id
                    helper = self.helper_definitions[helper_name]
                    helper_id = id(helper)
                    if helper_id not in self.active:
                        self.active.add(helper_id)
                        nested_helpers = dict(self.helper_definitions)
                        nested_helpers.update(scoped_helpers(helper.body))
                        helper_visitor = MutationVisitor(
                            self.source_path,
                            self.class_definition_lines,
                            nested_helpers,
                            self.active,
                            self.line(node),
                        )
                        for statement in helper.body:
                            if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                helper_visitor.visit(statement)
                        self.active.remove(helper_id)
                self.generic_visit(node)

        visitor = MutationVisitor(path, class_lines, helpers, active_helpers)
        for node in tree.body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visitor.visit(node)
    return tuple(sorted(set(found), key=lambda x: (str(x.path), x.line, x.target, x.operation)))


def repository_violations(root: Path):
    root = root.resolve()
    paths = tracked_text_files(root)
    return (
        *line_limit_violations(paths),
        *renderer_size_violations(paths, root),
        *session_context_field_violations(paths, root),
        *class_mutation_violations(paths, root),
        *dependency_violations(paths, root),
    )


def main() -> int:
    root = Path.cwd().resolve()
    violations = repository_violations(root)
    for violation in violations:
        if isinstance(violation, DependencyViolation):
            print(
                f"{violation.path.relative_to(root)}:{violation.line}: forbidden dependency on {violation.imported}"
            )
        elif isinstance(violation, RendererSizeViolation):
            print(
                f"{violation.path.relative_to(root)}: {violation.count} lines (renderer limit {MAX_RENDERER_LINES})"
            )
        elif isinstance(violation, SessionContextFieldViolation):
            print(
                f"{violation.path.relative_to(root)}:{violation.line}: SessionContext has {violation.count} direct fields (limit {MAX_SESSION_FIELDS})"
            )
        elif isinstance(violation, ClassMutationViolation):
            print(
                f"{violation.path.relative_to(root)}:{violation.line}: post-definition {violation.operation} mutates {violation.target}"
            )
        else:
            path, count = violation
            print(f"{path.relative_to(root)}: {count} lines (limit {MAX_LINES})")
    return int(bool(violations))


if __name__ == "__main__":
    raise SystemExit(main())
