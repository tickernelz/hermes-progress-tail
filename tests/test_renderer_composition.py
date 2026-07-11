import ast
import asyncio
import importlib
import inspect
from pathlib import Path

import pytest

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.models.state import SessionContext
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering.delegate import DelegateProgressRenderer
from tests.support.rendering import EditableAdapter

DELIVERY_MODULE = "hermes_progress_tail.rendering.delivery"
RENDERER_PATH = Path(__file__).parents[1] / "hermes_progress_tail" / "rendering" / "renderer.py"
DELIVERY_PATH = Path(__file__).parents[1] / "hermes_progress_tail" / "rendering" / "delivery.py"
DELEGATE_PATH = Path(__file__).parents[1] / "hermes_progress_tail" / "rendering" / "delegate.py"
DELEGATE_SECTIONS_PATH = (
    Path(__file__).parents[1] / "hermes_progress_tail" / "rendering" / "delegate_sections.py"
)


def test_architecture_delegate_section_result_formatting_is_a_cohesive_collaborator():
    assert DELEGATE_SECTIONS_PATH.is_file()
    tree = ast.parse(DELEGATE_PATH.read_text(encoding="utf-8"))
    delegate_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DelegateProgressRenderer"
    )
    section = next(
        node
        for node in delegate_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "section"
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "section"
        for node in ast.walk(section)
    )


def test_architecture_delegate_compatibility_forwards_are_owned_by_class_body():
    expected = {
        "_status_symbol": ("status",),
        "_duration": ("seconds",),
        "_delegate_cwd": ("value",),
        "_terminal_first_line": ("command",),
        "_strip_tool_emoji": ("text",),
        "_simplify_known_plugin_paths": ("text",),
    }
    tree = ast.parse(DELEGATE_PATH.read_text(encoding="utf-8"))
    delegate_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DelegateProgressRenderer"
    )
    owned_methods = {
        node.name
        for node in delegate_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert expected.keys() <= owned_methods
    for name, parameters in expected.items():
        assert (
            tuple(inspect.signature(DelegateProgressRenderer.__dict__[name]).parameters)
            == parameters
        )


def test_architecture_moved_delegate_forwards_preserve_exact_signatures_and_static_semantics():
    expected = {
        "_completion_result_text": "(self, branch)",
        "_completion_result_lines": "(self, text)",
        "_prepare_completion_block_text": "(self, text)",
        "_split_inline_markdown_sections": "(text)",
        "_simplify_long_paths": "(text)",
        "_completed_result_line_limit": "(self)",
        "_completed_result_line_char_limit": "(self)",
        "_middle_truncate_lines": "(lines, limit)",
        "_simplify_completion_line": "(self, text, *, branch=None)",
        "_delegate_result_only": "(self, branch)",
        "_completed_result_limit": "(self)",
        "_delegate_display_lines": "(self, branch)",
        "_delegate_title": "(self, branch, *, inferred_task_count=None)",
        "_delegate_connector": "(self, index, total)",
        "_delegate_compact_line": "(self, item)",
        "_delegate_event_label": "(self, item)",
        "_delegate_tool_name": "(item)",
        "_simplify_delegate_tool_text": "(self, text)",
    }
    static_names = {
        "_split_inline_markdown_sections",
        "_simplify_long_paths",
        "_middle_truncate_lines",
        "_delegate_tool_name",
    }
    for name, signature in expected.items():
        descriptor = inspect.getattr_static(DelegateProgressRenderer, name)
        callable_ = descriptor.__func__ if isinstance(descriptor, staticmethod) else descriptor
        signature_value = inspect.signature(callable_)
        signature_value = signature_value.replace(
            parameters=[
                parameter.replace(annotation=inspect.Signature.empty)
                for parameter in signature_value.parameters.values()
            ],
            return_annotation=inspect.Signature.empty,
        )
        assert str(signature_value) == signature
        assert isinstance(descriptor, staticmethod) == (name in static_names)


def test_delegate_sections_keeps_live_renderer_settings_identity():
    settings = load_settings({})
    renderer = DelegateProgressRenderer(settings)
    assert hasattr(renderer, "_sections")
    assert renderer._sections.settings is settings
    replacement = load_settings({"renderer": {"density": "verbose"}})
    renderer.settings = replacement
    assert renderer._sections.settings is replacement


def _delegate_dynamic_assembly_violations(source: str) -> list[str]:
    """Return prohibited ways a module can assemble the delegate class dynamically."""
    tree = ast.parse(source)
    class_name = "DelegateProgressRenderer"
    violations: list[str] = []
    mutating_helpers: set[str] = set()

    def targets_delegate(node):
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == class_name
        )

    for node in tree.body:
        if (
            isinstance(node, ast.ClassDef)
            and node.name == class_name
            and (node.bases or node.keywords or node.decorator_list)
        ):
            violations.append("class customization")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            (
                isinstance(item, (ast.Assign, ast.AnnAssign))
                and any(
                    targets_delegate(target)
                    for target in (item.targets if isinstance(item, ast.Assign) else [item.target])
                )
            )
            or (
                isinstance(item, ast.Call)
                and isinstance(item.func, ast.Name)
                and item.func.id in {"setattr", "exec"}
            )
            for item in ast.walk(node)
        ):
            mutating_helpers.add(node.name)

    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        if any(targets_delegate(target) for target in targets):
            violations.append("post-definition assignment")
        for item in ast.walk(node):
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Name):
                if item.func.id == "exec":
                    violations.append("exec")
                elif (
                    item.func.id == "setattr"
                    and item.args
                    and isinstance(item.args[0], ast.Name)
                    and item.args[0].id == class_name
                ):
                    violations.append("setattr")
                elif item.func.id in mutating_helpers and not isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    violations.append("helper-driven mutation")
    return violations


@pytest.mark.parametrize(
    "mutation",
    [
        "DelegateProgressRenderer.x = value",
        "DelegateProgressRenderer.x: object = value",
        "setattr(DelegateProgressRenderer, 'x', value)",
        "exec('DelegateProgressRenderer.x = value')",
        "def mutate():\n    setattr(DelegateProgressRenderer, 'x', value)\nmutate()",
    ],
)
def test_delegate_dynamic_assembly_detector_rejects_post_definition_mutations(mutation):
    source = f"class DelegateProgressRenderer:\n    pass\n{mutation}\n"
    assert _delegate_dynamic_assembly_violations(source)


@pytest.mark.parametrize(
    "declaration",
    [
        "class DelegateProgressRenderer(Base):\n    pass",
        "class DelegateProgressRenderer(metaclass=Meta):\n    pass",
        "@decorate\nclass DelegateProgressRenderer:\n    pass",
    ],
)
def test_delegate_dynamic_assembly_detector_rejects_customized_class_declarations(declaration):
    assert _delegate_dynamic_assembly_violations(declaration)


def test_architecture_delegate_has_no_post_definition_class_assembly():
    source = DELEGATE_PATH.read_text(encoding="utf-8")
    assert not _delegate_dynamic_assembly_violations(source)


class Collaborator:
    def __init__(self, settings):
        self.settings = settings

    def replace_settings(self, settings):
        self.settings = settings


class DeliverySpy:
    def __init__(self):
        self.calls = []

    async def render_live(self, *args, **kwargs):
        self.calls.append(("render_live", args, kwargs))
        return "render-live"

    async def send_live_message(self, *args, **kwargs):
        self.calls.append(("send_live_message", args, kwargs))
        return "send-live"

    async def downgrade_to_snapshot(self, *args, **kwargs):
        self.calls.append(("downgrade_to_snapshot", args, kwargs))
        return "downgrade"

    def schedule_delayed_live_flush(self, *args, **kwargs):
        self.calls.append(("schedule_delayed_live_flush", args, kwargs))
        return "delayed"

    def cancel_delayed_flush(self, *args, **kwargs):
        self.calls.append(("cancel_delayed_flush", args, kwargs))
        return "cancel-flush"

    def cancel_delete(self, *args, **kwargs):
        self.calls.append(("cancel_delete", args, kwargs))
        return "cancel-delete"

    def schedule_auto_delete(self, *args, **kwargs):
        self.calls.append(("schedule_auto_delete", args, kwargs))
        return "auto-delete"

    async def render_snapshot(self, *args, **kwargs):
        self.calls.append(("render_snapshot", args, kwargs))
        return "snapshot"

    def prepare_message(self, *args, **kwargs):
        self.calls.append(("prepare_message", args, kwargs))
        return "prepared"

    def prepare_telegram_rich_message(self, *args, **kwargs):
        self.calls.append(("prepare_telegram_rich_message", args, kwargs))
        return "rich"


def _renderer_with_delivery(delivery):
    renderer = ProgressRenderer.__new__(ProgressRenderer)
    renderer.delivery = delivery
    return renderer


def _parameter_contract(callable_):
    return tuple(
        (parameter.name, parameter.kind, parameter.default)
        for parameter in inspect.signature(callable_).parameters.values()
    )


def test_architecture_renderer_delivery_exists_and_owns_operations():
    delivery_type = getattr(importlib.import_module(DELIVERY_MODULE), "RendererDelivery", None)
    assert inspect.isclass(delivery_type)
    assert {
        "render_live",
        "send_live_message",
        "downgrade_to_snapshot",
        "schedule_delayed_live_flush",
        "cancel_delayed_flush",
        "cancel_delete",
        "schedule_auto_delete",
        "render_snapshot",
        "prepare_message",
        "prepare_telegram_rich_message",
    } <= delivery_type.__dict__.keys()


def test_architecture_constructor_contract_and_keyword_only_collaborators():
    parameters = inspect.signature(ProgressRenderer.__init__).parameters
    assert tuple(parameters) == (
        "self",
        "settings",
        "delivery",
        "registry",
        "reducer",
        "delegate_renderer",
        "footer_info_provider",
    )
    assert all(
        parameters[name].kind is inspect.Parameter.KEYWORD_ONLY for name in tuple(parameters)[2:]
    )


def test_architecture_default_composition_has_no_placeholder_collaborators():
    delivery_type = getattr(importlib.import_module(DELIVERY_MODULE), "RendererDelivery", None)
    assert inspect.isclass(delivery_type)
    settings = load_settings({})
    renderer = ProgressRenderer(settings)
    assert isinstance(renderer.delivery, delivery_type)
    registry_type = getattr(
        importlib.import_module("hermes_progress_tail.rendering.session"), "SessionRegistry", None
    )
    assert isinstance(renderer.registry, registry_type)
    reducer_type = getattr(
        importlib.import_module("hermes_progress_tail.rendering.event_reducer"),
        "EventReducer",
        None,
    )
    assert isinstance(renderer.reducer, reducer_type)
    assert "_SettingsCollaborator" not in RENDERER_PATH.read_text(encoding="utf-8")

    assert renderer.settings is settings
    assert renderer.delivery.settings is settings
    assert renderer.registry.settings is settings
    assert renderer.reducer.settings is settings
    assert renderer.delegate_renderer.settings is settings
    replacement = load_settings({"progress_tail": {"tools": {"lines": 7}}})
    renderer.replace_settings(replacement)
    assert renderer.settings is replacement
    assert renderer.delivery.settings is replacement
    assert renderer.registry.settings is replacement
    assert renderer.reducer.settings is replacement
    assert renderer.delegate_renderer.settings is replacement
    with pytest.raises(AttributeError):
        renderer.settings = settings


def test_architecture_injected_collaborators_are_preserved_and_receive_settings():
    delivery_type = getattr(importlib.import_module(DELIVERY_MODULE), "RendererDelivery", None)
    assert inspect.isclass(delivery_type)
    settings = load_settings({})
    delivery = delivery_type(settings, lambda _ctx: "")
    registry = Collaborator(settings)
    reducer = Collaborator(settings)
    delegate = Collaborator(settings)
    footer = object()
    renderer = ProgressRenderer(
        settings,
        delivery=delivery,
        registry=registry,
        reducer=reducer,
        delegate_renderer=delegate,
        footer_info_provider=footer,
    )
    assert (renderer.delivery, renderer.registry, renderer.reducer) == (delivery, registry, reducer)
    assert renderer.delegate_renderer is delegate
    assert renderer.footer_info_provider is footer
    replacement = load_settings({"progress_tail": {"tools": {"lines": 8}}})
    renderer.replace_settings(replacement)
    assert all(item.settings is replacement for item in (delivery, registry, reducer, delegate))
    with pytest.raises(TypeError):
        ProgressRenderer(settings, delivery)


def test_architecture_facade_methods_have_exact_signatures_and_class_body_ownership():
    expected = {
        "_render_live": ("self", "ctx", "force", "ignore_backoff"),
        "_send_live_message": ("self", "ctx", "content", "recovery"),
        "_downgrade_to_snapshot": ("self", "ctx", "error", "state"),
        "_schedule_delayed_live_flush": ("self", "ctx", "delay"),
        "_cancel_delayed_flush": ("self", "ctx"),
        "_cancel_delete": ("self", "ctx"),
        "_schedule_auto_delete": ("self", "ctx", "success"),
        "_render_snapshot": ("self", "ctx", "force", "final"),
        "_prepare_message": ("self", "ctx", "content"),
        "_prepare_telegram_rich_message": ("self", "ctx", "content"),
        "_fit_message": ("content", "limit"),
        "_message_limit": ("ctx",),
        "_classify_edit_error": ("error",),
        "_edit_backoff_seconds": ("error", "kind", "failure_count"),
        "register_context": ("self", "ctx"),
        "_same_source_message": ("existing", "incoming"),
        "find_context": ("self", "session_id", "session_key"),
        "migrate_context": ("self", "old_session_id", "new_session_id", "session_key"),
        "purge": ("self", "session_id", "platform"),
    }
    assert expected.keys() <= ProgressRenderer.__dict__.keys()
    for name, names in expected.items():
        assert tuple(inspect.signature(ProgressRenderer.__dict__[name]).parameters) == names

    tree = ast.parse(RENDERER_PATH.read_text(encoding="utf-8"))
    renderer_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ProgressRenderer"
    )
    assert renderer_class.bases == []
    assert not any(
        isinstance(node, (ast.Assign, ast.AnnAssign))
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "ProgressRenderer"
            for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        )
        for node in tree.body
    )


def test_architecture_async_delivery_facade_forwards_exactly_and_awaits_results():
    assert "delivery" in inspect.signature(ProgressRenderer.__init__).parameters

    async def exercise():
        spy = DeliverySpy()
        renderer = _renderer_with_delivery(spy)
        ctx, error, state = object(), object(), object()
        assert await renderer._render_live(ctx, True, ignore_backoff=True) == "render-live"
        assert await renderer._send_live_message(ctx, "body", recovery=True) == "send-live"
        assert await renderer._downgrade_to_snapshot(ctx, error, state) == "downgrade"
        assert await renderer._render_snapshot(ctx, True, True) == "snapshot"
        assert spy.calls == [
            ("render_live", (ctx, True), {"ignore_backoff": True}),
            ("send_live_message", (ctx, "body"), {"recovery": True}),
            ("downgrade_to_snapshot", (ctx, error, state), {}),
            ("render_snapshot", (ctx, True, True), {}),
        ]

    asyncio.run(exercise())


def test_architecture_sync_delivery_facade_forwards_exactly_and_returns_results():
    assert "delivery" in inspect.signature(ProgressRenderer.__init__).parameters
    spy = DeliverySpy()
    renderer = _renderer_with_delivery(spy)
    ctx = object()
    assert renderer._schedule_delayed_live_flush(ctx, 1.25) == "delayed"
    assert renderer._cancel_delayed_flush(ctx) == "cancel-flush"
    assert renderer._cancel_delete(ctx) == "cancel-delete"
    assert renderer._schedule_auto_delete(ctx, success=True) == "auto-delete"
    assert renderer._prepare_message(ctx, "body") == "prepared"
    assert renderer._prepare_telegram_rich_message(ctx, "body") == "rich"
    assert spy.calls == [
        ("schedule_delayed_live_flush", (ctx, 1.25), {}),
        ("cancel_delayed_flush", (ctx,), {}),
        ("cancel_delete", (ctx,), {}),
        ("schedule_auto_delete", (ctx,), {"success": True}),
        ("prepare_message", (ctx, "body"), {}),
        ("prepare_telegram_rich_message", (ctx, "body"), {}),
    ]


def test_architecture_delivery_has_no_runtime_dependency():
    tree = ast.parse(DELIVERY_PATH.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert all("runtime" not in module.split(".") for module in imported)


def test_characterization_session_context_constructor_repr_equality_and_surfaces():
    adapter = EditableAdapter()
    positional = SessionContext("s", "k", "discord", "c", "t", adapter, None, "live_tail")
    keyword = SessionContext(
        session_id="s",
        session_key="k",
        platform="discord",
        chat_id="c",
        thread_id="t",
        adapter=adapter,
        loop=None,
        strategy="live_tail",
    )
    assert positional != keyword
    assert repr(positional) != repr(keyword)
    keyword.started_at = positional.started_at
    keyword.last_event_at = positional.last_event_at
    keyword.lock = positional.lock
    assert positional == keyword
    assert repr(positional) == repr(keyword)
    assert repr(positional).startswith("SessionContext(session_id='s', session_key='k'")
    assert positional.line_buffer is positional.tool_lines
    assert positional.metadata == {"thread_id": "t"}
    positional.tool_lines.extend(("one", "two", "three"))
    positional.resize(2)
    assert list(positional.line_buffer) == ["two", "three"]


def test_architecture_delivery_coroutine_contracts():
    delivery_type = getattr(importlib.import_module(DELIVERY_MODULE), "RendererDelivery", None)
    assert inspect.isclass(delivery_type)
    for name in ("render_live", "send_live_message", "downgrade_to_snapshot", "render_snapshot"):
        assert inspect.iscoroutinefunction(delivery_type.__dict__[name])
    assert asyncio.iscoroutinefunction(ProgressRenderer._render_live)
