import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from hermes_progress_tail.monkeypatches import (
    install_telegram_format_monkeypatch,
    uninstall_telegram_format_monkeypatch,
)


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

    async def edit_message(self, chat_id, message_id, content, *, finalize=False):
        self.original_calls.append((chat_id, message_id, content, finalize))
        return await self._original_edit_message(chat_id, message_id, content, finalize=finalize)

    async def _original_edit_message(self, chat_id, message_id, content, *, finalize=False):
        await self._bot.edit_message_text(
            chat_id=int(chat_id), message_id=int(message_id), text=content
        )
        return SendResult(True, message_id=message_id)


def test_telegram_format_monkeypatch_renders_focused_titles_and_italic_body(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()

    result = asyncio.run(
        adapter.edit_message(
            "123",
            "456",
            "**__Reasoning__**\n***Considering optimization response***\n*Reviewing repository analysis*\n\n**__Tools__**\n✓ tool",
        )
    )

    assert result.success is True
    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="*__Reasoning__*\n*_Considering optimization response_*\n_Reviewing repository analysis_\n\n*__Tools__*\n✓ tool",
        parse_mode="MarkdownV2",
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_formats_non_final_edits(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()

    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is True
    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress *bold*",
        parse_mode="MarkdownV2",
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_falls_back_to_original_on_format_failure(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()
    adapter.format_message = MagicMock(side_effect=Exception("format exploded"))

    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is True
    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress **bold**",
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_does_not_change_final_edits(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()

    result = asyncio.run(adapter.edit_message("123", "456", "final **bold**", finalize=True))

    assert result.success is True
    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="final **bold**",
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_is_idempotent_and_uninstall_restores_original(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    original = FakeTelegramAdapter.edit_message

    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    first_patch = FakeTelegramAdapter.edit_message
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    assert FakeTelegramAdapter.edit_message is first_patch
    assert uninstall_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    assert FakeTelegramAdapter.edit_message is original


def test_telegram_format_monkeypatch_uses_original_when_bot_missing(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()
    original_bot = adapter._bot
    adapter._bot = None

    async def original_without_bot(chat_id, message_id, content, *, finalize=False):
        adapter.original_calls.append((chat_id, message_id, content, finalize))
        return SendResult(True, message_id=message_id)

    adapter._original_edit_message = original_without_bot
    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is True
    assert adapter.original_calls == [("123", "456", "progress **bold**", False)] * 2
    original_bot.edit_message_text.assert_not_awaited()
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_uses_original_when_content_exceeds_limit(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()
    adapter.MAX_MESSAGE_LENGTH = 8

    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is True
    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress **bold**",
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_treats_not_modified_as_success(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()
    adapter._bot.edit_message_text.side_effect = Exception("Message is not modified")

    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is True
    assert result.message_id == "456"
    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress *bold*",
        parse_mode="MarkdownV2",
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_returns_message_lost_without_plain_retry(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=SendResult, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()
    adapter._bot.edit_message_text.side_effect = Exception("Message to edit not found")

    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is False
    assert result.message_id == "456"
    assert result.error == "message_lost: Message to edit not found"
    assert result.retryable is False
    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress *bold*",
        parse_mode="MarkdownV2",
    )
    assert adapter.original_calls == []
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
