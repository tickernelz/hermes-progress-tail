from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from hermes_progress_tail.hooks import agent, compression, delegate, platform, telegram
from hermes_progress_tail.hooks.contracts import inert_hook_callbacks


def _closure_contains(function: Any, expected: object) -> bool:
    return any(cell.cell_contents is expected for cell in (function.__closure__ or ()))


def _cases() -> list[tuple[Any, Any, Any, object, str]]:
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

    child = SimpleNamespace(_build_child_progress_callback=lambda *args, **kwargs: None)

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
        (
            agent,
            agent.install_agent_monkeypatches,
            agent.uninstall_agent_monkeypatches,
            Agent,
            "_fire_reasoning_delta",
        ),
        (
            compression,
            compression.install_compression_status_monkeypatch,
            compression.uninstall_compression_status_monkeypatch,
            Status,
            "_emit_status",
        ),
        (
            compression,
            compression.install_compression_lifecycle_monkeypatch,
            compression.uninstall_compression_lifecycle_monkeypatch,
            Lifecycle,
            "_compress_context",
        ),
        (
            delegate,
            delegate.install_delegate_monkeypatches,
            delegate.uninstall_delegate_monkeypatches,
            child,
            "_build_child_progress_callback",
        ),
        (
            platform,
            platform.install_adapter_monkeypatches,
            platform.uninstall_adapter_monkeypatches,
            Adapter,
            "handle_message",
        ),
        (
            platform,
            platform.install_gateway_interrupt_monkeypatch,
            platform.uninstall_gateway_interrupt_monkeypatch,
            Runner,
            "_interrupt_and_clear_session",
        ),
        (
            telegram,
            telegram.install_telegram_format_monkeypatch,
            telegram.uninstall_telegram_format_monkeypatch,
            Telegram,
            "edit_message",
        ),
    ]


@pytest.mark.parametrize(("module", "install", "uninstall", "target", "wrapper"), _cases())
def test_default_callback_is_looked_up_once_and_retained_until_reinstall(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    install: Any,
    uninstall: Any,
    target: object,
    wrapper: str,
) -> None:
    callback_a = inert_hook_callbacks()
    callback_b = inert_hook_callbacks()
    selected = [callback_a]
    lookups = 0

    def current():
        nonlocal lookups
        lookups += 1
        return selected[0]

    monkeypatch.setattr(module, "current_hook_callbacks", current)
    assert install(target) is True
    assert lookups == 1
    installed = getattr(target, wrapper)
    assert _closure_contains(installed, callback_a)

    selected[0] = callback_b
    assert _closure_contains(getattr(target, wrapper), callback_a)
    assert not _closure_contains(getattr(target, wrapper), callback_b)
    assert lookups == 1

    assert type(install(target)) is bool
    assert lookups == 2
    assert uninstall(target) is True
    assert type(uninstall(target)) is bool
    assert install(target) is True
    assert lookups == 3
    assert _closure_contains(getattr(target, wrapper), callback_b)
    assert uninstall(target) is True


@pytest.mark.parametrize(("_module", "install", "_uninstall", "_target", "_wrapper"), _cases())
def test_default_target_unavailable_returns_literal_bool(
    _module: Any,
    install: Any,
    _uninstall: Any,
    _target: object,
    _wrapper: str,
) -> None:
    # Each leaf maps an unavailable host import through its default target resolver.
    result = install()
    assert result is False
    assert type(result) is bool
