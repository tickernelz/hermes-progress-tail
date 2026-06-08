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

    async def original_without_bot(chat_id, message_id, content, *, finalize=False, metadata=None):
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


def test_telegram_format_monkeypatch_forwards_metadata_to_original(monkeypatch):
    """Regression: wrapper must accept and forward `metadata` keyword.

    Hermes core's `inspect.signature(adapter.edit_message)` walks `__wrapped__`
    past the @wraps decorator and reports `metadata` as a supported kwarg. The
    stream consumer then calls `edit_message(..., metadata={...})`. If the
    wrapper's own signature lacks `metadata`, the call raises
    `TypeError: got an unexpected keyword argument 'metadata'`, which prevents
    the final-progress/edit path from completing and the gateway then resends
    the full final response as a fresh Telegram message — visibly duplicated
    next to the progress bubble.
    """
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
    sent_metadata = {"stream_id": "abc-123", "final": True}

    # Non-final path: wrapper renders Markdown, so it does NOT call original
    # with metadata. The call must still succeed.
    result = asyncio.run(
        adapter.edit_message("123", "456", "progress **bold**", metadata=sent_metadata)
    )
    assert result.success is True
    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress *bold*",
        parse_mode="MarkdownV2",
    )

    # Final path: wrapper falls through to original(); metadata must reach it.
    adapter2 = FakeTelegramAdapter()
    final_metadata = {"stream_id": "abc-123", "final": True, "edit_id": 99}
    result2 = asyncio.run(
        adapter2.edit_message(
            "123", "456", "final **bold**", finalize=True, metadata=final_metadata
        )
    )
    assert result2.success is True
    assert adapter2.last_metadata == final_metadata
    adapter2._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="final **bold**",
    )

    # Bot-missing path: wrapper short-circuits to original(); metadata must
    # still reach original() — this is the path the stream consumer hits when
    # the live edit fails.
    adapter3 = FakeTelegramAdapter()
    adapter3._bot = None
    fallback_metadata = {"stream_id": "abc-123", "fallback": True}

    async def original_without_bot(chat_id, message_id, content, *, finalize=False, metadata=None):
        adapter3.last_metadata = metadata
        return SendResult(True, message_id=message_id)

    adapter3._original_edit_message = original_without_bot
    result3 = asyncio.run(
        adapter3.edit_message("123", "456", "fallback", metadata=fallback_metadata)
    )
    assert result3.success is True
    assert adapter3.last_metadata == fallback_metadata

    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
