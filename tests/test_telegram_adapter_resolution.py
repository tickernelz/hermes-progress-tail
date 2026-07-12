import asyncio
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from hermes_progress_tail.monkeypatches import (
    install_telegram_format_monkeypatch,
    uninstall_telegram_format_monkeypatch,
)
from hermes_progress_tail.runtime import context


class SendResult:
    def __init__(self, success, message_id=None, error=None, retryable=None):
        self.success = success
        self.message_id = message_id
        self.error = error
        self.retryable = retryable


class _BaseTelegramAdapter:
    MAX_MESSAGE_LENGTH = 4096

    def __init__(self):
        self._bot = SimpleNamespace(edit_message_text=AsyncMock())

    def format_message(self, content):
        return str(content).replace("**bold**", "*bold*")

    async def edit_message(self, chat_id, message_id, content, *, finalize=False, metadata=None):
        await self._bot.edit_message_text(
            chat_id=int(chat_id), message_id=int(message_id), text=content
        )
        return SendResult(True, message_id=message_id)


class StaticTelegramAdapter(_BaseTelegramAdapter):
    pass


class RuntimeTelegramAdapter(_BaseTelegramAdapter):
    pass


def _install_module(monkeypatch, module_name, adapter_cls):
    for index in range(1, len(module_name.split("."))):
        package_name = ".".join(module_name.split(".")[:index])
        if package_name not in __import__("sys").modules:
            monkeypatch.setitem(__import__("sys").modules, package_name, ModuleType(package_name))
    module = ModuleType(module_name)
    module.ParseMode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    module.TelegramAdapter = adapter_cls
    monkeypatch.setitem(__import__("sys").modules, module_name, module)


def test_monkeypatches_loaded_hermes_plugin_telegram_adapter_when_static_path_exists(monkeypatch):
    _install_module(monkeypatch, "plugins.platforms.telegram.adapter", StaticTelegramAdapter)
    _install_module(monkeypatch, "hermes_plugins.telegram_platform.adapter", RuntimeTelegramAdapter)
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(StaticTelegramAdapter)
    uninstall_telegram_format_monkeypatch(RuntimeTelegramAdapter)

    assert install_telegram_format_monkeypatch() is True

    runtime_adapter = RuntimeTelegramAdapter()
    result = asyncio.run(runtime_adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is True
    runtime_adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress *bold*",
        parse_mode="MarkdownV2",
    )
    uninstall_telegram_format_monkeypatch(StaticTelegramAdapter)
    uninstall_telegram_format_monkeypatch(RuntimeTelegramAdapter)


def test_first_telegram_dispatch_patches_adapter_materialized_after_registration(monkeypatch):
    _install_module(monkeypatch, "plugins.platforms.telegram.adapter", StaticTelegramAdapter)
    monkeypatch.delitem(
        __import__("sys").modules, "hermes_plugins.telegram_platform.adapter", raising=False
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(StaticTelegramAdapter)
    uninstall_telegram_format_monkeypatch(RuntimeTelegramAdapter)
    assert install_telegram_format_monkeypatch() is True

    _install_module(monkeypatch, "hermes_plugins.telegram_platform.adapter", RuntimeTelegramAdapter)
    runtime_adapter = RuntimeTelegramAdapter()
    source = SimpleNamespace(platform="telegram")
    monkeypatch.setattr(
        context, "_operational_renderer", lambda: SimpleNamespace(settings=object())
    )
    monkeypatch.setattr(context, "platform_name", lambda _source: "telegram")
    monkeypatch.setattr(
        context,
        "resolve_platform_settings",
        lambda _settings, _platform: SimpleNamespace(enabled=True, strategy="tail"),
    )
    monkeypatch.setattr(
        context,
        "_pre_gateway_session_context",
        lambda _gateway, _store, current_source: (current_source, object(), "session"),
    )
    monkeypatch.setattr(context, "_adapter_for", lambda _gateway, _source: runtime_adapter)
    monkeypatch.setattr(context, "_register_context", lambda **_kwargs: None)

    context._on_pre_gateway_dispatch(
        SimpleNamespace(source=source), SimpleNamespace(), SimpleNamespace()
    )
    result = asyncio.run(runtime_adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is True
    runtime_adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress *bold*",
        parse_mode="MarkdownV2",
    )
    uninstall_telegram_format_monkeypatch(StaticTelegramAdapter)
    uninstall_telegram_format_monkeypatch(RuntimeTelegramAdapter)
