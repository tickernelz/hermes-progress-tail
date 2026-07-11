import ast
import dataclasses
import importlib
import shutil
import sys
from pathlib import Path


def _release_contract():
    try:
        from hermes_progress_tail.models.release import FooterInfo
    except (ImportError, ModuleNotFoundError):
        raise AssertionError("FooterInfo DTO is missing") from None
    return FooterInfo


def _provider_contract():
    FooterInfo = _release_contract()
    from hermes_progress_tail.rendering.renderer import ProgressRenderer

    if "footer_info_provider" not in ProgressRenderer.__init__.__code__.co_varnames:
        raise AssertionError("footer information provider injection is missing")
    return FooterInfo, ProgressRenderer


def test_rendering_has_no_runtime_import_edge():
    _release_contract()
    root = Path(__file__).parents[1] / "hermes_progress_tail" / "rendering"
    violations = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations += [
                    f"{path.name}:{node.lineno}"
                    for item in node.names
                    if item.name.startswith("hermes_progress_tail.runtime")
                ]
            elif isinstance(node, ast.ImportFrom):
                if node.level >= 2 and (node.module or "").startswith("runtime"):
                    violations.append(f"{path.name}:{node.lineno}")
                if (node.module or "").startswith("hermes_progress_tail.runtime"):
                    violations.append(f"{path.name}:{node.lineno}")
    assert not violations


def test_footer_info_is_exact_and_frozen():
    FooterInfo = _release_contract()
    assert [(field.name, field.default) for field in dataclasses.fields(FooterInfo)] == [
        ("current_version", ""),
        ("latest_tag", ""),
        ("latest_url", ""),
    ]
    assert FooterInfo.__dataclass_params__.frozen


def test_renderer_default_and_falsey_provider_identity():
    FooterInfo, ProgressRenderer = _provider_contract()
    from hermes_progress_tail.config import load_settings

    class FalseyProvider:
        def __bool__(self):
            return False

        def __call__(self):
            return FooterInfo()

    provider = FalseyProvider()
    settings = load_settings({})
    assert callable(ProgressRenderer(settings).footer_info_provider)
    assert (
        ProgressRenderer(settings, footer_info_provider=provider).footer_info_provider is provider
    )


def test_provider_one_call_and_exception_safety_in_both_modes():
    FooterInfo, ProgressRenderer = _provider_contract()
    from hermes_progress_tail.config import load_settings
    from hermes_progress_tail.state import EnvironmentSnapshot, SessionContext

    for mode in ("focused", "sectioned"):
        calls = []

        def provider(calls=calls):
            calls.append(1)
            return FooterInfo("1.0", "v1.1", "https://example.test/release")

        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"renderer": {"mode": mode}}}),
            footer_info_provider=provider,
        )
        ctx = SessionContext("s", "k", "telegram", "chat", None, None, None)
        ctx.environment = EnvironmentSnapshot(model="m")
        assert "v1.1" in renderer._content(ctx)
        assert calls == [1]
        renderer.footer_info_provider = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        assert "update" not in renderer._content(ctx)


def test_runtime_provider_is_dynamic_and_global_seam_is_gone(monkeypatch):
    FooterInfo = _release_contract()
    from hermes_progress_tail.rendering import footer
    from hermes_progress_tail.runtime import commands, plugin

    monkeypatch.setattr(
        commands, "_latest_release_info", lambda **kwargs: {"tag_name": "v9", "html_url": "u"}
    )
    monkeypatch.setattr(plugin, "VERSION", "1")
    assert plugin._footer_info() == FooterInfo("1", "v9", "u")
    assert not hasattr(footer, "configure_version_provider")


def test_standalone_renderer_never_uses_runtime_release_lookup(monkeypatch):
    _, ProgressRenderer = _provider_contract()
    from hermes_progress_tail.config import load_settings
    from hermes_progress_tail.runtime import commands
    from hermes_progress_tail.state import EnvironmentSnapshot, SessionContext

    calls = []
    monkeypatch.setattr(
        commands,
        "_latest_release_info",
        lambda **kwargs: calls.append(kwargs) or {"tag_name": "v9", "html_url": "u"},
    )
    renderer = ProgressRenderer(load_settings({}))
    ctx = SessionContext("s", "k", "telegram", "chat", None, None, None)
    ctx.environment = EnvironmentSnapshot(model="m")
    assert "update" not in renderer._content(ctx)
    assert calls == []


def test_copied_namespace_runtime_footer_provider_is_independent(tmp_path, monkeypatch):
    FooterInfo = _release_contract()
    package = Path(__file__).parents[1] / "hermes_progress_tail"
    plugins = tmp_path / "hermes_plugins"
    copied = plugins / "c11_copy"
    plugins.mkdir()
    (plugins / "__init__.py").write_text("")
    shutil.copytree(package, copied)
    monkeypatch.syspath_prepend(str(tmp_path))

    source_commands = importlib.import_module("hermes_progress_tail.runtime.commands")
    source_plugin = importlib.import_module("hermes_progress_tail.runtime.plugin")
    copy_commands = importlib.import_module("hermes_plugins.c11_copy.runtime.commands")
    copy_plugin = importlib.import_module("hermes_plugins.c11_copy.runtime.plugin")
    monkeypatch.setattr(
        source_commands,
        "_latest_release_info",
        lambda **kwargs: {"tag_name": "source", "html_url": "source-url"},
    )
    monkeypatch.setattr(
        copy_commands,
        "_latest_release_info",
        lambda **kwargs: {"tag_name": "copy", "html_url": "copy-url"},
    )
    source = source_plugin._footer_info()
    copied_info = copy_plugin._footer_info()
    assert source == FooterInfo(source_plugin.VERSION, "source", "source-url")
    assert (copied_info.current_version, copied_info.latest_tag, copied_info.latest_url) == (
        copy_plugin.VERSION,
        "copy",
        "copy-url",
    )
    assert type(copied_info) is not FooterInfo

    for name in tuple(sys.modules):
        if name == "hermes_plugins" or name.startswith("hermes_plugins.c11_copy"):
            sys.modules.pop(name, None)
