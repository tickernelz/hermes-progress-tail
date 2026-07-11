from __future__ import annotations

from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock


class SendResult:
    def __init__(self, success, message_id=None, error=None, retryable=None):
        self.success = success
        self.message_id = message_id
        self.error = error
        self.retryable = retryable


class FakeTelegramAdapter:
    MAX_MESSAGE_LENGTH = 4096

    def __init__(self):
        self._bot = SimpleNamespace(edit_message_text=AsyncMock())
        self.original_calls = []

    def format_message(self, content):
        return (
            str(content)
            .replace("**bold**", "*bold*")
            .replace("*Reviewing repository analysis*", "_Reviewing repository analysis_")
        )

    async def edit_message(self, chat_id, message_id, content, *, finalize=False, metadata=None):
        self.original_calls.append((chat_id, message_id, content, finalize))
        return await self._original_edit_message(
            chat_id, message_id, content, finalize=finalize, metadata=metadata
        )

    async def _original_edit_message(
        self, chat_id, message_id, content, *, finalize=False, metadata=None
    ):
        self.last_metadata = metadata
        await self._bot.edit_message_text(
            chat_id=int(chat_id), message_id=int(message_id), text=content
        )
        return SendResult(True, message_id=message_id)


def _install_telegram_modules(monkeypatch, module_name, *, adapter_cls=FakeTelegramAdapter):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    for index in range(1, len(module_name.split("."))):
        package_name = ".".join(module_name.split(".")[:index])
        monkeypatch.setitem(__import__("sys").modules, package_name, ModuleType(package_name))
    module = ModuleType(module_name)
    module.ParseMode = parse_mode
    module.TelegramAdapter = adapter_cls
    monkeypatch.setitem(__import__("sys").modules, module_name, module)
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    return module
