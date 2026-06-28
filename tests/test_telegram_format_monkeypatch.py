import asyncio
from types import ModuleType, SimpleNamespace
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


def test_telegram_format_monkeypatch_resolves_legacy_gateway_adapter_path(monkeypatch):
    _install_telegram_modules(monkeypatch, "gateway.platforms.telegram")
    monkeypatch.delitem(
        __import__("sys").modules, "hermes_plugins.telegram_platform.adapter", raising=False
    )
    monkeypatch.delitem(
        __import__("sys").modules, "plugins.platforms.telegram.adapter", raising=False
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    original = FakeTelegramAdapter.edit_message

    assert install_telegram_format_monkeypatch() is True

    assert FakeTelegramAdapter.edit_message is not original
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


def test_telegram_format_monkeypatch_resolves_new_plugin_adapter_path(monkeypatch):
    monkeypatch.delitem(
        __import__("sys").modules, "hermes_plugins.telegram_platform.adapter", raising=False
    )
    monkeypatch.setitem(__import__("sys").modules, "gateway.platforms.telegram", None)
    _install_telegram_modules(monkeypatch, "plugins.platforms.telegram.adapter")
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    original = FakeTelegramAdapter.edit_message

    assert install_telegram_format_monkeypatch() is True

    assert FakeTelegramAdapter.edit_message is not original
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


def test_telegram_format_monkeypatch_uses_rich_edit_when_supported(monkeypatch):
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
    adapter._bot.do_api_request = AsyncMock(return_value={"result": {"message_id": 456}})

    result = asyncio.run(
        adapter.edit_message(
            "123",
            "456",
            "**__Tools__**\n✅ terminal: pytest -q · done · 0.4s",
            metadata={"thread_id": "99"},
        )
    )

    assert result.success is True
    adapter._bot.edit_message_text.assert_not_awaited()
    adapter._bot.do_api_request.assert_awaited_once()
    (method,) = adapter._bot.do_api_request.await_args.args
    kwargs = adapter._bot.do_api_request.await_args.kwargs["api_kwargs"]
    assert method == "editMessageText"
    assert kwargs["chat_id"] == 123
    assert kwargs["message_id"] == 456
    assert "text" not in kwargs
    assert "parse_mode" not in kwargs
    assert kwargs["rich_message"]["markdown"].startswith("## Tools")
    assert "| Command | Result |" in kwargs["rich_message"]["markdown"]
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_falls_back_to_markdownv2_when_rich_unsupported(monkeypatch):
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
    adapter._bot.do_api_request = AsyncMock(side_effect=AttributeError("no rich endpoint"))

    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is True
    adapter._bot.do_api_request.assert_awaited_once()
    adapter._bot.edit_message_text.assert_awaited_once_with(
        chat_id=123,
        message_id=456,
        text="progress *bold*",
        parse_mode="MarkdownV2",
    )
    assert adapter._hermes_progress_tail_rich_disabled is True
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_parse_error_falls_back_without_latching(monkeypatch):
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
    adapter._bot.do_api_request = AsyncMock(
        side_effect=RuntimeError("Bad Request: can't parse rich message")
    )

    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is True
    adapter._bot.do_api_request.assert_awaited_once()
    adapter._bot.edit_message_text.assert_awaited_once()
    assert not getattr(adapter, "_hermes_progress_tail_rich_disabled", False)
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_telegram_format_monkeypatch_transient_rich_error_does_not_duplicate_fallback(monkeypatch):
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
    adapter._bot.do_api_request = AsyncMock(side_effect=RuntimeError("Bad Gateway"))

    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))

    assert result.success is False
    assert result.retryable is True
    assert "Bad Gateway" in result.error
    adapter._bot.edit_message_text.assert_not_awaited()
    assert not getattr(adapter, "_hermes_progress_tail_rich_disabled", False)
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


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
    metadata = {"stream_id": "abc-123", "fallback": "format_failure"}

    result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**", metadata=metadata))

    assert result.success is True
    assert adapter.last_metadata == metadata
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


def test_rich_edit_flood_control_falls_back_to_markdownv2(monkeypatch):
    """Flood control on rich endpoint must fallback to MarkdownV2, not block."""
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
    monkeypatch.delitem(
        __import__("sys").modules, "hermes_plugins.telegram_platform.adapter", raising=False
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()
    adapter._bot.do_api_request = AsyncMock(
        side_effect=RuntimeError("Flood control exceeded. Retry in 11220 seconds")
    )

    result = asyncio.run(adapter.edit_message("123", "456", "**__Tools__**\n✓ done"))

    assert result.success is True
    adapter._bot.do_api_request.assert_awaited_once()
    adapter._bot.edit_message_text.assert_awaited_once()
    assert adapter._hermes_progress_tail_rich_disabled is True
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)


def test_rich_send_flood_control_falls_back_to_legacy_send(monkeypatch):
    """Flood control on rich send must fallback to legacy send, not block."""
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
    monkeypatch.delitem(
        __import__("sys").modules, "hermes_plugins.telegram_platform.adapter", raising=False
    )

    class LegacySendAdapter(FakeTelegramAdapter):
        def __init__(self):
            super().__init__()
            self.send_calls = []

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.send_calls.append((chat_id, content))
            return SendResult(True, message_id="999")

    uninstall_telegram_format_monkeypatch(LegacySendAdapter)
    assert install_telegram_format_monkeypatch(LegacySendAdapter) is True
    adapter = LegacySendAdapter()
    adapter._bot.do_api_request = AsyncMock(
        side_effect=RuntimeError("Flood control exceeded. Retry in 11220 seconds")
    )

    result = asyncio.run(adapter.send("123", "**bold content**"))

    assert result.success is True
    assert len(adapter.send_calls) >= 1
    assert adapter._hermes_progress_tail_rich_disabled is True
    # Flood deadline must be set in the future
    assert adapter._hermes_progress_tail_rich_flood_until > 0
    uninstall_telegram_format_monkeypatch(LegacySendAdapter)


def test_rich_send_flood_in_sendresult_triggers_fallback(monkeypatch):
    """Core adapter catches flood errors internally and returns a failed SendResult.

    The plugin must detect flood in the result's error string, latch rich off,
    and return None so the caller falls back to legacy send.
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
    monkeypatch.delitem(
        __import__("sys").modules, "hermes_plugins.telegram_platform.adapter", raising=False
    )

    class CoreFloodAdapter(FakeTelegramAdapter):
        def __init__(self):
            super().__init__()
            self.send_calls = []
            self._rich_messages_enabled = True
            self._rich_send_disabled = False

        def _should_attempt_rich(self, content, metadata=None):
            return True

        async def _try_send_rich(self, chat_id, content, reply_to, metadata):
            # Core catches flood and returns a failure result (no exception)
            return SendResult(
                success=False,
                error="Flood control exceeded. Retry in 5568 seconds",
                retryable=True,
            )

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.send_calls.append((chat_id, content))
            return SendResult(True, message_id="999")

    uninstall_telegram_format_monkeypatch(CoreFloodAdapter)
    assert install_telegram_format_monkeypatch(CoreFloodAdapter) is True
    adapter = CoreFloodAdapter()

    result = asyncio.run(adapter.send("123", "**bold content**"))

    assert result.success is True
    assert len(adapter.send_calls) >= 1
    assert adapter._hermes_progress_tail_rich_disabled is True
    assert adapter._hermes_progress_tail_rich_flood_until > 0
    uninstall_telegram_format_monkeypatch(CoreFloodAdapter)


def test_rich_auto_re_enables_after_flood_cooldown(monkeypatch):
    """Rich must auto re-enable after the flood cooldown period passes."""
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
    monkeypatch.delitem(
        __import__("sys").modules, "hermes_plugins.telegram_platform.adapter", raising=False
    )
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
    assert install_telegram_format_monkeypatch(FakeTelegramAdapter) is True
    adapter = FakeTelegramAdapter()

    # Simulate flood latch with a deadline already in the past
    adapter._hermes_progress_tail_rich_disabled = True
    adapter._hermes_progress_tail_rich_flood_until = 0.01  # already expired

    # Sleep briefly so monotonic clock is definitely past the deadline
    import time as _time

    _time.sleep(0.02)

    adapter._bot.do_api_request = AsyncMock(return_value={"message_id": 456})

    result = asyncio.run(
        adapter.edit_message(
            "123",
            "456",
            "**__Tools__**\n✅ terminal: pytest -q · done",
        )
    )

    # Rich should have been re-enabled and used (not MarkdownV2 fallback)
    assert result.success is True
    adapter._bot.do_api_request.assert_awaited_once()
    (method,) = adapter._bot.do_api_request.await_args.args
    assert method == "editMessageText"
    # Rich is re-enabled
    assert adapter._hermes_progress_tail_rich_disabled is False
    uninstall_telegram_format_monkeypatch(FakeTelegramAdapter)
