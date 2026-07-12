import asyncio
import sys
from enum import Enum
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermes_progress_tail.hooks import platform as platform_hooks
from hermes_progress_tail.monkeypatches import uninstall_telegram_format_monkeypatch


class SendResult:
    def __init__(self, success, message_id=None):
        self.success = success
        self.message_id = message_id


class HookBaseAdapter:
    def set_message_handler(self, handler):
        self.handler = handler

    async def handle_message(self, event):
        await self.edit_message("123", "456", "progress **bold**")


class RuntimeTelegramAdapter(HookBaseAdapter):
    MAX_MESSAGE_LENGTH = 4096

    def __init__(self):
        self._bot = SimpleNamespace(edit_message_text=AsyncMock())

    def format_message(self, content):
        return str(content).replace("**bold**", "*bold*")

    async def edit_message(self, chat_id, message_id, content, *, finalize=False, metadata=None):
        await self._bot.edit_message_text(
            chat_id=int(chat_id), message_id=int(message_id), text=content
        )
        return SendResult(True, message_id)


class HostPlatform(Enum):
    TELEGRAM = "telegram"
    DISCORD = "discord"


def _callbacks():
    return SimpleNamespace(register_adapter_context=lambda _adapter, _event: None)


@pytest.fixture(autouse=True)
def _restore_adapter_patches():
    platform_hooks.uninstall_adapter_monkeypatches(HookBaseAdapter)
    uninstall_telegram_format_monkeypatch(RuntimeTelegramAdapter)
    yield
    platform_hooks.uninstall_adapter_monkeypatches(HookBaseAdapter)
    uninstall_telegram_format_monkeypatch(RuntimeTelegramAdapter)


@pytest.mark.parametrize("internal", [False, True], ids=["external", "internal-auto-resume"])
@pytest.mark.parametrize(
    "platform", ["telegram", HostPlatform.TELEGRAM], ids=["string", "host-enum"]
)
def test_handle_message_prepares_runtime_telegram_before_first_edit(
    monkeypatch, internal, platform
):
    module = ModuleType("hermes_plugins.telegram_platform.adapter")
    module.ParseMode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    module.TelegramAdapter = RuntimeTelegramAdapter
    monkeypatch.setitem(sys.modules, "hermes_plugins.telegram_platform.adapter", module)
    monkeypatch.setitem(
        sys.modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(RuntimeTelegramAdapter)
    assert platform_hooks._mutate_adapter_monkeypatches(HookBaseAdapter, callbacks=_callbacks())
    adapter = RuntimeTelegramAdapter()

    asyncio.run(
        adapter.handle_message(
            SimpleNamespace(internal=internal, source=SimpleNamespace(platform=platform))
        )
    )

    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress *bold*",
        parse_mode="MarkdownV2",
    )
    platform_hooks.uninstall_adapter_monkeypatches(HookBaseAdapter)
    uninstall_telegram_format_monkeypatch(RuntimeTelegramAdapter)


def test_non_telegram_flow_does_not_prepare_adapter(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "hermes_progress_tail.hooks.telegram.install_telegram_format_monkeypatch",
        lambda adapter_cls: calls.append(adapter_cls),
    )
    assert platform_hooks._mutate_adapter_monkeypatches(HookBaseAdapter, callbacks=_callbacks())
    adapter = RuntimeTelegramAdapter()

    asyncio.run(
        adapter.handle_message(
            SimpleNamespace(internal=False, source=SimpleNamespace(platform=HostPlatform.DISCORD))
        )
    )

    assert calls == []
    platform_hooks.uninstall_adapter_monkeypatches(HookBaseAdapter)


def test_telegram_prepare_failure_fails_open(monkeypatch):
    monkeypatch.setattr(
        "hermes_progress_tail.hooks.telegram.install_telegram_format_monkeypatch",
        lambda _adapter_cls: (_ for _ in ()).throw(RuntimeError("host drift")),
    )
    assert platform_hooks._mutate_adapter_monkeypatches(HookBaseAdapter, callbacks=_callbacks())
    adapter = RuntimeTelegramAdapter()

    asyncio.run(
        adapter.handle_message(
            SimpleNamespace(internal=True, source=SimpleNamespace(platform=HostPlatform.TELEGRAM))
        )
    )

    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123, message_id=456, text="progress **bold**"
    )
    platform_hooks.uninstall_adapter_monkeypatches(HookBaseAdapter)
