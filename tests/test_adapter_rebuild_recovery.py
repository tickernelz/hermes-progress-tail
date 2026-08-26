"""Regression: a mid-turn platform adapter rebuild must not freeze the card.

Hermes core rebuilds a platform adapter in place (``gateway.adapters[platform] =
adapter``) when the transport dies. A ``SessionContext`` that froze the adapter
at registration time keeps editing the dead object forever, so the progress card
never updates again. ``ctx.adapter`` must resolve the currently live adapter.
"""

from __future__ import annotations

import asyncio
from enum import Enum

import hermes_progress_tail
from hermes_progress_tail.runtime import context as rc
from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.state import SessionContext
from tests.support.gateway import Adapter, Event, Gateway, SessionStore, Source


class Platform(Enum):
    TELEGRAM = "telegram"


class FakeAdapter:
    def __init__(self, name, platform=None):
        self.name = name
        if platform is not None:
            self.platform = platform


class FakeGateway:
    def __init__(self, adapters):
        self.adapters = adapters


class ExplodingGateway:
    @property
    def adapters(self):
        raise RuntimeError("gateway.adapters exploded")


def make_ctx(adapter, platform="telegram"):
    return SessionContext("s", "k", platform, "chat", None, adapter, None, "live_tail")


def test_rebuilt_adapter_is_resolved_at_use_time():
    old = FakeAdapter("old")
    new = FakeAdapter("new")
    adapters = {"telegram": old}
    ctx = make_ctx(old)
    ctx.attach_gateway(FakeGateway(adapters))

    assert ctx.adapter is old
    adapters["telegram"] = new
    assert ctx.adapter is new


def test_without_gateway_the_constructor_argument_is_returned_unchanged():
    original = FakeAdapter("original")
    ctx = make_ctx(original)

    assert ctx.adapter is original


def test_missing_platform_entry_falls_back_to_the_original_reference():
    original = FakeAdapter("original")
    ctx = make_ctx(original)
    ctx.attach_gateway(FakeGateway({"discord": FakeAdapter("other")}))

    assert ctx.adapter is original


def test_exploding_gateway_degrades_to_the_original_reference():
    original = FakeAdapter("original")
    ctx = make_ctx(original)
    ctx.attach_gateway(ExplodingGateway())

    assert ctx.adapter is original


def test_enum_keyed_adapters_resolve_via_the_adapter_platform_attribute():
    old = FakeAdapter("old", platform=Platform.TELEGRAM)
    new = FakeAdapter("new", platform=Platform.TELEGRAM)
    adapters = {Platform.TELEGRAM: old}
    ctx = make_ctx(old)
    ctx.attach_gateway(FakeGateway(adapters))

    assert ctx.adapter is old
    adapters[Platform.TELEGRAM] = new
    assert ctx.adapter is new


def test_setter_assignment_is_honoured_by_later_reads():
    original = FakeAdapter("original")
    replacement = FakeAdapter("replacement")
    ctx = make_ctx(original)

    ctx.adapter = replacement
    assert ctx.adapter is replacement

    ctx.attach_gateway(FakeGateway({}))
    assert ctx.adapter is replacement


def test_registration_attaches_the_gateway_so_rebuilds_are_picked_up(monkeypatch):
    async def run():
        old = Adapter()
        new = Adapter()
        gateway = Gateway(old)
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {}}),
        )

        hermes_progress_tail._on_pre_gateway_dispatch(Event(), gateway, SessionStore())
        ctx = hermes_progress_tail._get_renderer().find_context("session-1")
        assert ctx.adapter is old

        gateway.adapters[Source.platform] = new
        assert ctx.adapter is new

    asyncio.run(run())


def test_adapter_event_registration_resolves_through_the_stamped_gateway(monkeypatch):
    async def run():
        old = Adapter()
        new = Adapter()
        gateway = Gateway(old)
        old._session_store = SessionStore()
        old._hermes_progress_tail_gateway = gateway
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {}}),
        )

        rc.register_context_from_adapter_event(old, Event())
        ctx = hermes_progress_tail._get_renderer().find_context("session-1")
        assert ctx.adapter is old

        gateway.adapters[Source.platform] = new
        assert ctx.adapter is new

    asyncio.run(run())


def test_multiplex_registration_resolves_through_the_gateway_runner_backref(monkeypatch):
    """Acceptance criterion 6: the multiplex shape must still resolve.

    Under ``multiplex_profiles`` Hermes core's ``_primary_message_handler()``
    returns a plain closure with no ``__self__``, so the plugin's
    ``set_message_handler`` monkeypatch *deletes* the
    ``_hermes_progress_tail_gateway`` stamp. Core still injects
    ``adapter.gateway_runner = self`` (``gateway/run.py``) and never sets
    ``adapter.gateway``, so the back-reference is the only surviving link to
    the live ``gateway.adapters`` mapping.
    """

    async def run():
        from hermes_progress_tail.hooks.monkeypatches import (
            install_adapter_monkeypatches,
            uninstall_adapter_monkeypatches,
        )

        old = Adapter()
        new = Adapter()
        gateway = Gateway(old)
        old._session_store = SessionStore()
        # Pre-stamp so the deletion below is an observed action, not a no-op.
        old._hermes_progress_tail_gateway = gateway
        hermes_progress_tail.plugin._renderer = None
        monkeypatch.setattr(
            hermes_progress_tail.plugin,
            "_load_runtime_settings",
            lambda: load_settings({"progress_tail": {}}),
        )

        async def multiplex_handler(event):  # a plain closure: no __self__
            return None

        assert getattr(multiplex_handler, "__self__", None) is None

        uninstall_adapter_monkeypatches(Adapter)
        install_adapter_monkeypatches(Adapter)
        try:
            old.set_message_handler(multiplex_handler)
        finally:
            uninstall_adapter_monkeypatches(Adapter)

        # The stamp must be genuinely gone, otherwise this test proves nothing.
        assert not hasattr(old, "_hermes_progress_tail_gateway")
        assert getattr(old, "gateway", None) is None

        old.gateway_runner = gateway  # exactly as Hermes core injects it

        rc.register_context_from_adapter_event(old, Event())
        ctx = hermes_progress_tail._get_renderer().find_context("session-1")
        assert ctx.adapter is old

        gateway.adapters[Source.platform] = new
        assert ctx.adapter is new

    asyncio.run(run())
