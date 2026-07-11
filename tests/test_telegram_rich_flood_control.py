import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hermes_progress_tail.monkeypatches import (
    install_telegram_format_monkeypatch,
    uninstall_telegram_format_monkeypatch,
)
from tests.support.telegram import FakeTelegramAdapter, SendResult


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
