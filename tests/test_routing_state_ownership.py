import ast
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from hermes_progress_tail.models import state

ROUTING_NAMES = [
    "strategy",
    "lines",
    "preview_length",
    "edit_interval",
    "tools_enabled",
    "assistant_enabled",
    "reasoning_enabled",
    "delegates_enabled",
    "background_jobs_enabled",
    "timestamp",
    "timestamp_format",
    "agent_label",
    "chat_type",
    "source_message_id",
]
ROUTING_TYPES = [
    "str",
    "int",
    "int",
    "float",
    "bool",
    "bool",
    "bool",
    "bool",
    "bool",
    "bool | None",
    "str",
    "str",
    "str",
    "str | None",
]
ROUTING_DEFAULTS = [
    "auto",
    3,
    120,
    1.5,
    True,
    True,
    True,
    True,
    True,
    None,
    "",
    "",
    "",
    None,
]
FINAL_FIELDS = [
    "session_id",
    "session_key",
    "platform",
    "chat_id",
    "thread_id",
    "adapter",
    "loop",
    "routing",
    "generation",
    "owner_thread_id",
    "owner_thread_name",
    "delivery",
    "started_at",
    "tool",
    "delegate",
    "background",
    "assistant",
    "reasoning",
    "diagnostics",
    "lock",
    "environment",
]


def prerequisites():
    assert hasattr(state, "RoutingState"), "RoutingState owner is missing"
    assert hasattr(state, "SessionContext"), "SessionContext is missing"
    return state.SessionContext


def context_without_strategy(**kwargs):
    cls = prerequisites()
    return cls("s", "k", "discord", "c", None, None, None, **kwargs)


def test_routing_state_exact_contract_defaults_identity_and_conflicts():
    prerequisites()
    routing_fields = fields(state.RoutingState)
    assert [item.name for item in routing_fields] == ROUTING_NAMES
    assert [item.type for item in routing_fields] == ROUTING_TYPES
    owner = state.RoutingState()
    assert [getattr(owner, name) for name in ROUTING_NAMES] == ROUTING_DEFAULTS
    assert context_without_strategy(routing=owner).routing is owner
    first, second = context_without_strategy(), context_without_strategy()
    assert first.routing is not second.routing
    for legacy, value in zip(ROUTING_NAMES, ROUTING_DEFAULTS, strict=True):
        with pytest.raises(TypeError, match="routing cannot be combined"):
            context_without_strategy(routing=state.RoutingState(), **{legacy: value})


def test_final_session_context_shape_factories_and_import_identity():
    cls = prerequisites()
    assert [item.name for item in fields(cls)] == FINAL_FIELDS
    assert len(cls.__annotations__) == 21
    by_name = {item.name: item for item in fields(cls)}
    expected = {
        "routing": state.RoutingState,
        "delivery": state.DeliveryState,
        "tool": state.ToolState,
        "delegate": state.DelegateState,
        "background": state.BackgroundState,
        "assistant": state.AssistantState,
        "reasoning": state.ReasoningState,
        "diagnostics": state.DiagnosticsState,
    }
    assert {name: by_name[name].default_factory for name in expected} == expected
    checks = """
r = importlib.import_module('hermes_progress_tail.models.state_records')
c = importlib.import_module('hermes_progress_tail.models.state_compat')
s = importlib.import_module('hermes_progress_tail.models.state')
assert s.RoutingState is r.RoutingState
assert issubclass(s.SessionContext, c.SessionStateCompatibility)
"""
    for first in ("state_records", "state_compat", "state"):
        code = (
            f"import importlib\nimportlib.import_module('hermes_progress_tail.models.{first}')\n"
            + checks
        )
        subprocess.run([sys.executable, "-c", code], check=True)


def test_production_uses_nested_routing_access_and_owner_construction():
    prerequisites()
    package = Path(__file__).parents[1] / "hermes_progress_tail"
    names = set(ROUTING_NAMES)
    context_variables = {"ctx", "candidate", "existing", "incoming", "replacement"}
    offenders = []
    for root_name in ("rendering", "runtime"):
        for path in sorted((package / root_name).glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr in names
                    and isinstance(node.value, ast.Name)
                    and node.value.id in context_variables
                ):
                    offenders.append(f"{path.relative_to(package)}:{node.lineno}:{node.attr}")
    assert offenders == [], "flat routing access in production: " + ", ".join(offenders)
    context_source = (package / "runtime" / "context.py").read_text()
    demo_source = (package / "runtime" / "demo.py").read_text()
    assert "routing=RoutingState(" in context_source
    assert "routing=RoutingState(" in demo_source
