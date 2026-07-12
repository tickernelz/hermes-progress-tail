from __future__ import annotations

import ast
from dataclasses import asdict
from pathlib import Path

import yaml

from hermes_progress_tail.cli.installer_defaults import DEFAULT_CONFIG
from hermes_progress_tail.settings.types import Settings

ROOT = Path(__file__).parents[1]
LOADING = ROOT / "hermes_progress_tail/settings/loading.py"
FACADE = ROOT / "hermes_progress_tail/settings/config.py"
EXPECTED_BUILDERS = {
    "_build_tools",
    "_build_delegates",
    "_build_todo",
    "_build_patch",
    "_build_assistant",
    "_build_reasoning",
    "_build_background_jobs",
    "_build_native_gateway",
    "_build_cleanup",
    "_build_footer",
    "_build_telegram",
    "_build_renderer",
    "_build_no_edit",
}


def _functions(path: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}


def _read_readme_config() -> dict:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    marker = "## Expected config"
    section = text.split(marker, 1)[1]
    yaml_text = section.split("```yaml", 1)[1].split("```", 1)[0]
    return yaml.safe_load(yaml_text)["progress_tail"]


def test_loader_has_cohesive_private_section_builders_and_thin_root():
    functions = _functions(LOADING)
    assert functions.keys() >= EXPECTED_BUILDERS
    root = functions["load_settings"]
    assert root.end_lineno - root.lineno + 1 <= 55
    assert all(len(functions[name].args.args) == 2 for name in EXPECTED_BUILDERS)


def test_load_settings_has_one_owner_and_private_builders_are_not_public():
    owners = []
    for path in (ROOT / "hermes_progress_tail").rglob("*.py"):
        if "tests" not in path.parts and "load_settings" in _functions(path):
            owners.append(path.relative_to(ROOT).as_posix())
    assert owners == ["hermes_progress_tail/settings/loading.py"]
    assert len(FACADE.read_text(encoding="utf-8").splitlines()) < 300
    loading_tree = ast.parse(LOADING.read_text(encoding="utf-8"))
    public = next(
        ast.literal_eval(node.value)
        for node in loading_tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    )
    assert public == ("load_settings",)


def _applicable_canonical(example: dict, canonical: dict) -> dict:
    return {
        key: _applicable_canonical(value, canonical[key])
        if isinstance(value, dict)
        else canonical[key]
        for key, value in example.items()
    }


def test_readme_and_installer_examples_match_canonical_settings():
    canonical = asdict(Settings())
    canonical.pop("platforms")
    assert _applicable_canonical(DEFAULT_CONFIG, canonical) == DEFAULT_CONFIG
    readme = _read_readme_config()
    readme.pop("platforms", None)
    assert readme == _applicable_canonical(readme, canonical)


def test_docs_and_installer_are_not_runtime_default_sources():
    imports = {
        alias.name
        for node in ast.walk(ast.parse(LOADING.read_text(encoding="utf-8")))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    from_imports = {
        node.module
        for node in ast.walk(ast.parse(LOADING.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    }
    assert not ({"yaml", "hermes_progress_tail.cli.installer_defaults"} & (imports | from_imports))
    assert "README" not in LOADING.read_text(encoding="utf-8")


def test_old_config_imports_are_confined_to_compatibility_suite():
    offenders = []
    for path in (ROOT / "tests").glob("test_*.py"):
        if path.name == "test_settings_compatibility.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        legacy_modules = {"hermes_progress_tail.config", "hermes_progress_tail.settings.config"}
        if (modules | imports) & legacy_modules:
            offenders.append(path.name)
    assert offenders == []
