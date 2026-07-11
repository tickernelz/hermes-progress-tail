from __future__ import annotations

import asyncio
import builtins
from types import SimpleNamespace

import pytest

from hermes_progress_tail.hooks import platform


class Adapter:
    def set_message_handler(self, handler):
        self.native_handler = handler
        return "installed"

    async def handle_message(self, event):
        self.native_events = getattr(self, "native_events", []) + [event]
        return "handled"


class Runner:
    async def _interrupt_and_clear_session(self, session_key, source, *args, **kwargs):
        self.native_call = (session_key, source, args, kwargs)
        return "cleared"


@pytest.fixture(autouse=True)
def restore_platform_patches():
    yield
    platform.uninstall_adapter_monkeypatches(Adapter)
    platform.uninstall_gateway_interrupt_monkeypatch(Runner)


def test_adapter_install_remembers_bound_owner_passthrough_and_teardown():
    original_set = Adapter.set_message_handler
    original_handle = Adapter.handle_message
    adapter = Adapter()
    gateway = SimpleNamespace()

    assert platform.install_adapter_monkeypatches(Adapter) is True
    assert platform.install_adapter_monkeypatches(Adapter) is True
    assert (
        adapter.set_message_handler(gateway.callback if hasattr(gateway, "callback") else gateway)
        == "installed"
    )
    # An unbound/callable object does not claim gateway ownership.
    assert not hasattr(adapter, "_hermes_progress_tail_gateway")
    assert adapter._hermes_progress_tail_message_handler is gateway
    assert platform.uninstall_adapter_monkeypatches(Adapter) is True
    assert Adapter.set_message_handler is original_set
    assert Adapter.handle_message is original_handle
    assert not hasattr(Adapter, "_hermes_progress_tail_adapter_patched")
    assert platform.uninstall_adapter_monkeypatches(Adapter) is False
    assert platform.install_adapter_monkeypatches(Adapter) is True
    assert platform.uninstall_adapter_monkeypatches(Adapter) is True
    assert Adapter.set_message_handler is original_set
    assert Adapter.handle_message is original_handle
    assert not hasattr(Adapter, "_hermes_progress_tail_adapter_patched")


def test_adapter_bound_handler_and_internal_registration_are_observable(monkeypatch):
    calls = []

    class Gateway:
        def callback(self):
            return None

    gateway = Gateway()
    adapter = Adapter()
    monkeypatch.setattr(
        "hermes_progress_tail.runtime.plugin.register_context_from_adapter_event",
        lambda owner, event: calls.append((owner, event)),
    )
    assert platform.install_adapter_monkeypatches(Adapter)
    assert adapter.set_message_handler(gateway.callback) == "installed"
    internal = SimpleNamespace(internal=True)
    external = SimpleNamespace(internal=False)
    assert asyncio.run(adapter.handle_message(internal)) == "handled"
    assert asyncio.run(adapter.handle_message(external)) == "handled"
    assert adapter._hermes_progress_tail_gateway is gateway
    assert calls == [(adapter, internal)]
    assert adapter.native_events == [internal, external]


def test_adapter_callback_and_remembering_failures_do_not_break_passthrough(monkeypatch):
    calls = []

    def raising_registration(owner, event):
        calls.append((owner, event))
        raise RuntimeError("callback failed")

    class HostileAdapter(Adapter):
        def __setattr__(self, name, value):
            if name.startswith("_hermes_progress_tail"):
                raise RuntimeError("no metadata")
            super().__setattr__(name, value)

    monkeypatch.setattr(
        "hermes_progress_tail.runtime.plugin.register_context_from_adapter_event",
        raising_registration,
    )
    adapter = HostileAdapter()
    event = SimpleNamespace(internal=True)
    assert platform.install_adapter_monkeypatches(HostileAdapter)
    assert adapter.set_message_handler(lambda: None) == "installed"
    assert asyncio.run(adapter.handle_message(event)) == "handled"
    assert calls == [(adapter, event)]
    assert platform.uninstall_adapter_monkeypatches(HostileAdapter)


def test_adapter_missing_api_and_scoped_import_failure(monkeypatch):
    assert platform.install_adapter_monkeypatches(type("Incomplete", (), {})) is False
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "gateway.platforms.base":
            raise ImportError("isolated missing gateway")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "__import__", blocked)
        assert platform.install_adapter_monkeypatches() is False
        assert platform.uninstall_adapter_monkeypatches() is False


@pytest.mark.parametrize(
    ("args", "kwargs"),
    [((" Stop Requested ", "other"), {}), ((), {"invalidation_reason": "stop_command:user"})],
)
def test_interrupt_stop_reasons_notify_after_native_call(monkeypatch, args, kwargs):
    calls = []
    monkeypatch.setattr(
        "hermes_progress_tail.runtime.plugin.on_gateway_stop_from_runner",
        lambda owner, **details: calls.append((owner.native_call, details)),
    )
    runner = Runner()
    original = Runner._interrupt_and_clear_session
    assert platform.install_gateway_interrupt_monkeypatch(Runner)
    assert platform.install_gateway_interrupt_monkeypatch(Runner)
    assert (
        asyncio.run(runner._interrupt_and_clear_session(12, "telegram", *args, **kwargs))
        == "cleared"
    )
    assert calls == [((12, "telegram", args, kwargs), {"session_key": "12", "source": "telegram"})]
    assert platform.uninstall_gateway_interrupt_monkeypatch(Runner)
    assert Runner._interrupt_and_clear_session is original
    assert not hasattr(Runner, "_hermes_progress_tail_gateway_interrupt_patched")
    assert platform.uninstall_gateway_interrupt_monkeypatch(Runner) is False
    assert platform.install_gateway_interrupt_monkeypatch(Runner) is True
    assert platform.uninstall_gateway_interrupt_monkeypatch(Runner) is True
    assert Runner._interrupt_and_clear_session is original
    assert not hasattr(Runner, "_hermes_progress_tail_gateway_interrupt_patched")


def test_interrupt_non_stop_and_callback_failure_preserve_native_result(monkeypatch):
    runner = Runner()
    assert platform.install_gateway_interrupt_monkeypatch(Runner)
    assert (
        asyncio.run(runner._interrupt_and_clear_session("s", "cli", "timeout", "expired"))
        == "cleared"
    )
    monkeypatch.setattr(
        "hermes_progress_tail.runtime.plugin.on_gateway_stop_from_runner",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("observer failed")),
    )
    assert (
        asyncio.run(
            runner._interrupt_and_clear_session("s", "cli", interrupt_reason="stop requested")
        )
        == "cleared"
    )


def test_interrupt_missing_api_and_scoped_import_failure(monkeypatch):
    assert platform.install_gateway_interrupt_monkeypatch(type("Incomplete", (), {})) is False
    real_import = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "gateway.run":
            raise ImportError("isolated missing runner")
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "__import__", blocked)
        assert platform.install_gateway_interrupt_monkeypatch() is False
        assert platform.uninstall_gateway_interrupt_monkeypatch() is False
