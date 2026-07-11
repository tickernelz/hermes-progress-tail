from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from hermes_progress_tail.hooks.agent import install_agent_monkeypatches
from hermes_progress_tail.hooks.compression import (
    install_compression_lifecycle_monkeypatch,
    install_compression_status_monkeypatch,
)
from hermes_progress_tail.hooks.contracts import inert_hook_callbacks
from hermes_progress_tail.hooks.delegate import install_delegate_monkeypatches
from hermes_progress_tail.hooks.platform import (
    install_adapter_monkeypatches,
    install_gateway_interrupt_monkeypatch,
)
from hermes_progress_tail.hooks.telegram import install_telegram_format_monkeypatch

INSTALLERS = (
    (install_agent_monkeypatches, "agent_cls"),
    (install_compression_status_monkeypatch, "agent_cls"),
    (install_compression_lifecycle_monkeypatch, "agent_cls"),
    (install_delegate_monkeypatches, "delegate_module"),
    (install_adapter_monkeypatches, "adapter_cls"),
    (install_gateway_interrupt_monkeypatch, "gateway_runner_cls"),
    (install_telegram_format_monkeypatch, "telegram_adapter_cls"),
)


@pytest.mark.parametrize(("installer", "target_name"), INSTALLERS)
def test_all_callback_installers_preserve_positional_target_and_keyword_only_callback(
    installer: Any, target_name: str
) -> None:
    parameters = tuple(inspect.signature(installer).parameters.values())
    assert parameters[0].name == target_name
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[1].name == "callbacks"
    assert parameters[1].kind is inspect.Parameter.KEYWORD_ONLY
    missing_api_target = type("MissingApi", (), {})
    assert installer(missing_api_target) is False
    assert type(installer(missing_api_target)) is bool


def _closure_contains(function: Any, expected: object) -> bool:
    return any(cell.cell_contents is expected for cell in (function.__closure__ or ()))


def _targets() -> list[tuple[Any, object, Any]]:
    class Agent:
        def __init__(self):
            pass

        def _fire_reasoning_delta(self, text):
            return text

    class Status:
        def _emit_status(self, text):
            return text

    class Lifecycle:
        def _compress_context(self, messages, system_message):
            return messages

    delegate = SimpleNamespace(_build_child_progress_callback=lambda *args, **kwargs: None)

    class Adapter:
        def set_message_handler(self, handler):
            return None

        async def handle_message(self, event):
            return None

    class Runner:
        async def _interrupt_and_clear_session(self, session_key, source):
            return None

    class Telegram:
        async def edit_message(self, *args, **kwargs):
            return None

        async def send(self, *args, **kwargs):
            return None

    return [
        (install_agent_monkeypatches, Agent, "_fire_reasoning_delta"),
        (install_compression_status_monkeypatch, Status, "_emit_status"),
        (install_compression_lifecycle_monkeypatch, Lifecycle, "_compress_context"),
        (install_delegate_monkeypatches, delegate, "_build_child_progress_callback"),
        (install_adapter_monkeypatches, Adapter, "handle_message"),
        (install_gateway_interrupt_monkeypatch, Runner, "_interrupt_and_clear_session"),
        (install_telegram_format_monkeypatch, Telegram, "edit_message"),
    ]


@pytest.mark.parametrize(("installer", "target", "wrapper_name"), _targets())
def test_all_installed_wrapper_families_lexically_snapshot_injected_callbacks(
    installer: Any, target: object, wrapper_name: str
) -> None:
    callbacks = inert_hook_callbacks()
    result = installer(target, callbacks=callbacks)
    assert result is True
    assert type(result) is bool
    assert _closure_contains(getattr(target, wrapper_name), callbacks)

    # A mutable target attribute must not become a callback dependency.
    target._hermes_progress_tail_callbacks = inert_hook_callbacks()
    assert _closure_contains(getattr(target, wrapper_name), callbacks)
