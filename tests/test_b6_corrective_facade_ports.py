from __future__ import annotations

import ast
import importlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.check_structure import dependency_violations, repository_violations, tracked_text_files

ROOT = Path(__file__).parents[1]
PACKAGE = ROOT / "hermes_progress_tail"
CANONICAL_ROOT = "hermes_progress_tail.runtime.plugin"
CHECKED_AREAS = ("hooks", "runtime", "rendering")


def _init_git_repo(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=root, check=True)


def _track(root: Path) -> None:
    import subprocess

    subprocess.run(["git", "add", "."], cwd=root, check=True)


@pytest.mark.parametrize(
    ("area", "source"),
    [
        (area, source)
        for area in CHECKED_AREAS
        for source in (
            "from hermes_progress_tail import plugin\n",
            "from hermes_progress_tail import plugin as p\n",
            "import hermes_progress_tail.plugin\n",
            "import hermes_progress_tail.plugin as p\n",
            "from .. import plugin\n",
            "from .. import plugin as p\n",
        )
    ]
    + [
        ("runtime", "from . import plugin\n"),
        ("runtime", "from . import plugin as p\n"),
        ("runtime", "from hermes_progress_tail.runtime import plugin\n"),
        ("runtime", "from hermes_progress_tail.runtime import plugin as p\n"),
    ],
)
def test_facade_equivalent_imports_report_canonical_composition_root(tmp_path, area, source):
    _init_git_repo(tmp_path)
    path = tmp_path / "hermes_progress_tail" / area / "consumer.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    _track(tmp_path)

    violations = dependency_violations(tracked_text_files(tmp_path), tmp_path.resolve())

    assert [(v.path.relative_to(tmp_path).as_posix(), v.line, v.imported) for v in violations] == [
        (f"hermes_progress_tail/{area}/consumer.py", 1, CANONICAL_ROOT)
    ]


def test_only_facade_and_composition_root_modules_may_import_root_or_facade(tmp_path):
    _init_git_repo(tmp_path)
    fixtures = {
        "hermes_progress_tail/plugin.py": "from .runtime import plugin\n",
        "hermes_progress_tail/runtime/plugin.py": "from .. import plugin\n",
    }
    for name, source in fixtures.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    _track(tmp_path)
    assert dependency_violations(tracked_text_files(tmp_path), tmp_path.resolve()) == ()


def test_checked_source_tree_has_no_facade_or_composition_root_dependencies():
    assert [v for v in repository_violations(ROOT) if hasattr(v, "imported")] == []


def _configure(module, provider):
    candidates = [
        value
        for name, value in vars(module).items()
        if name.startswith("configure_")
        and callable(value)
        and ("provider" in name or "port" in name)
    ]
    assert len(candidates) == 1, (
        f"{module.__name__} must expose one clear configure_* provider/port seam"
    )
    candidates[0](provider)


def test_context_renderer_is_obtained_from_module_local_injected_port():
    context = importlib.import_module("hermes_progress_tail.runtime.context")
    sentinel = object()
    _configure(context, SimpleNamespace(get_renderer=lambda: sentinel))
    assert context._renderer_provider() is sentinel


def test_footer_version_is_obtained_from_configured_provider_and_has_safe_default():
    footer = importlib.reload(importlib.import_module("hermes_progress_tail.rendering.footer"))
    assert isinstance(footer._current_version(), str)
    _configure(footer, lambda: "9.8.7-test")
    assert footer._current_version() == "9.8.7-test"


def test_runtime_event_modules_accept_independent_local_ports():
    for module_name in ("agent_events", "tool_events"):
        module = importlib.import_module(f"hermes_progress_tail.runtime.{module_name}")
        sentinel = object()
        provider = SimpleNamespace(get_renderer=lambda value=sentinel: value)
        _configure(module, provider)
        assert module._runtime_provider is provider


def test_plugin_import_wires_all_module_local_ports_and_repeated_register_is_idempotent(
    monkeypatch,
):
    plugin = importlib.import_module("hermes_progress_tail.runtime.plugin")
    modules = [
        importlib.import_module("hermes_progress_tail.runtime.agent_events"),
        importlib.import_module("hermes_progress_tail.runtime.context"),
        importlib.import_module("hermes_progress_tail.runtime.tool_events"),
        importlib.import_module("hermes_progress_tail.rendering.footer"),
    ]
    assert all(any(name.startswith("configure_") for name in vars(module)) for module in modules)

    monkeypatch.setattr(
        plugin, "install_monkeypatches_report", lambda callbacks: SimpleNamespace(statuses=())
    )
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: {})
    context = SimpleNamespace(
        register_hook=lambda *args: None, register_command=lambda *args, **kwargs: None
    )
    plugin.register(context)
    before = tuple(module._runtime_provider for module in modules[:3])
    plugin.register(context)
    assert tuple(module._runtime_provider for module in modules[:3]) == before


def test_plugin_wires_narrow_ports_not_the_composition_root_module():
    plugin = importlib.import_module("hermes_progress_tail.runtime.plugin")
    agent_events = importlib.import_module("hermes_progress_tail.runtime.agent_events")
    context = importlib.import_module("hermes_progress_tail.runtime.context")
    tool_events = importlib.import_module("hermes_progress_tail.runtime.tool_events")

    for provider in (
        agent_events._runtime_provider,
        context._runtime_provider,
        tool_events._runtime_provider,
    ):
        assert provider is not plugin
        assert callable(provider.get_renderer)
        assert not hasattr(provider, "_schedule_render")
        assert not hasattr(provider, "_should_suppress_agent_progress")
    assert agent_events._runtime_provider.assistant_capture is plugin._ASSISTANT_CAPTURE


def test_copied_namespace_provider_ports_are_independent(tmp_path, monkeypatch):
    plugins = tmp_path / "hermes_plugins"
    copied = plugins / "b6_copy"
    plugins.mkdir()
    (plugins / "__init__.py").write_text("")
    shutil.copytree(PACKAGE, copied)
    monkeypatch.syspath_prepend(str(tmp_path))

    source = importlib.import_module("hermes_progress_tail.runtime.context")
    copy = importlib.import_module("hermes_plugins.b6_copy.runtime.context")
    source_provider = SimpleNamespace(get_renderer=lambda: "source")
    copy_provider = SimpleNamespace(get_renderer=lambda: "copy")
    _configure(source, source_provider)
    _configure(copy, copy_provider)

    assert source._runtime_provider is source_provider
    assert copy._runtime_provider is copy_provider
    assert source._runtime_provider is not copy._runtime_provider

    for name in tuple(sys.modules):
        if name == "hermes_plugins" or name.startswith("hermes_plugins.b6_copy"):
            sys.modules.pop(name, None)


def test_compatibility_facade_identity_and_monkeypatch_seam_remain():
    facade = importlib.import_module("hermes_progress_tail.plugin")
    root = importlib.import_module("hermes_progress_tail.runtime.plugin")
    assert facade is root
    original = root._get_renderer
    try:
        sentinel = object()
        facade._get_renderer = lambda: sentinel
        assert root._get_renderer() is sentinel
    finally:
        root._get_renderer = original


def test_checked_modules_do_not_import_facade_or_root_even_inside_functions():
    forbidden = {"hermes_progress_tail.plugin", CANONICAL_ROOT}
    offenders = []
    for area in CHECKED_AREAS:
        for path in sorted((PACKAGE / area).glob("*.py")):
            if area == "runtime" and path.name == "plugin.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {alias.name for alias in node.names}
                    if names & forbidden:
                        offenders.append((path.relative_to(ROOT).as_posix(), node.lineno))
                elif isinstance(node, ast.ImportFrom):
                    if node.module in forbidden or (
                        node.module == "hermes_progress_tail"
                        and any(a.name == "plugin" for a in node.names)
                    ):
                        offenders.append((path.relative_to(ROOT).as_posix(), node.lineno))
                    if node.level and any(a.name == "plugin" for a in node.names):
                        offenders.append((path.relative_to(ROOT).as_posix(), node.lineno))
    assert offenders == []
