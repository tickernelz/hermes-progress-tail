import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.monkeypatches import (
    install_telegram_format_monkeypatch,
    uninstall_telegram_format_monkeypatch,
)
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import SessionContext, ToolEvent


class Result:
    def __init__(self, success=True, message_id=None, error="", retryable=None):
        self.success = success
        self.message_id = message_id
        self.error = error
        self.retryable = retryable


class CapturingAdapter:
    name = "telegram"

    def __init__(self):
        self.sent = []
        self.edits = []
        self._bot = type("Bot", (), {"do_api_request": AsyncMock(return_value={"ok": True})})()

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        return Result(True, "m1")

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return Result(True, message_id)


class GatewayLikeTelegramAdapter(CapturingAdapter):
    def __init__(self):
        super().__init__()
        self.legacy_sent = []
        self._bot = SimpleNamespace(
            do_api_request=AsyncMock(return_value={"result": {"message_id": 999}}),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=111)),
        )

    def _should_attempt_rich(self, content, metadata=None):
        return bool(
            not getattr(self, "_rich_send_disabled", False)
            and not (metadata or {}).get("expect_edits")
            and inspectable_async(self._bot.do_api_request)
        )

    async def _try_send_rich(self, chat_id, content, reply_to=None, metadata=None):
        try:
            msg = await self._bot.do_api_request(
                "sendRichMessage",
                api_kwargs={
                    "chat_id": int(chat_id),
                    "rich_message": {"markdown": content},
                },
            )
        except Exception as exc:
            if (
                isinstance(exc, (AttributeError, TypeError, NotImplementedError))
                or "no rich" in str(exc).lower()
            ):
                self._rich_send_disabled = True
                return None
            if "bad request" in str(exc).lower() or "can't parse" in str(exc).lower():
                return None
            return Result(False, error=str(exc), retryable=True)
        message_id = (msg.get("result") or {}).get("message_id") if isinstance(msg, dict) else None
        return Result(True, str(message_id or "999"))

    def format_message(self, content):
        return str(content).replace("**bold**", "*bold*")

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        formatted = self.format_message(content)
        self.legacy_sent.append((chat_id, formatted, metadata))
        await self._bot.send_message(
            chat_id=int(chat_id),
            text=formatted,
            parse_mode="MarkdownV2",
        )
        self.sent.append((chat_id, content, metadata, "legacy"))
        return Result(True, "111")


class ProgressTailPatchedTelegramAdapter(GatewayLikeTelegramAdapter):
    async def edit_message(self, chat_id, message_id, content, *, finalize=False, metadata=None):
        self.edits.append((chat_id, message_id, content, metadata))
        return Result(True, message_id)


def inspectable_async(value):
    import inspect

    return inspect.iscoroutinefunction(value)


def make_ctx(adapter):
    return SessionContext(
        "s1",
        "k1",
        "telegram",
        "chat",
        "thread",
        adapter,
        asyncio.get_running_loop(),
        "live_tail",
        timestamp=False,
    )


def test_telegram_renderer_keeps_raw_markdown_without_send_patch():
    async def run():
        adapter = CapturingAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        ctx.chat_id = "123"
        ctx.thread_id = None
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "✅ terminal: pytest -q · done · 0.3s"),
            force=True,
        )

        sent = adapter.sent[-1][1]
        assert "**__Tools__**" in sent
        assert "## Tools" not in sent
        assert "| Command | Result |" not in sent

    asyncio.run(run())


def test_telegram_renderer_uses_raw_rich_send_for_initial_capable_messages():
    async def run():
        uninstall_telegram_format_monkeypatch(GatewayLikeTelegramAdapter)
        assert install_telegram_format_monkeypatch(GatewayLikeTelegramAdapter) is True
        adapter = GatewayLikeTelegramAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        ctx.chat_id = "123"
        ctx.thread_id = None
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "✅ terminal: pytest -q · done · 0.3s"),
            force=True,
        )

        adapter._bot.do_api_request.assert_awaited_once()
        method = adapter._bot.do_api_request.await_args.args[0]
        kwargs = adapter._bot.do_api_request.await_args.kwargs["api_kwargs"]
        assert method == "sendRichMessage"
        assert "## Tools" in kwargs["rich_message"]["markdown"]
        assert "| Command | Result |" in kwargs["rich_message"]["markdown"]
        adapter._bot.send_message.assert_not_awaited()

    asyncio.run(run())


def test_telegram_send_patch_keeps_expect_edits_messages_legacy():
    async def run():
        uninstall_telegram_format_monkeypatch(GatewayLikeTelegramAdapter)
        assert install_telegram_format_monkeypatch(GatewayLikeTelegramAdapter) is True
        adapter = GatewayLikeTelegramAdapter()

        result = await adapter.send(
            "123",
            "**__Tools__**\n✅ terminal: pytest -q · done · 0.3s",
            metadata={"expect_edits": True},
        )

        assert result.success is True
        adapter._bot.do_api_request.assert_not_awaited()
        assert adapter.legacy_sent
        assert "**__Tools__**" in adapter.legacy_sent[-1][1]
        assert "| Command | Result |" not in adapter.legacy_sent[-1][1]
        assert adapter.legacy_sent[-1][2]["expect_edits"] is True

    asyncio.run(run())


def test_telegram_renderer_skips_rich_preparation_after_adapter_latch():
    async def run():
        uninstall_telegram_format_monkeypatch(GatewayLikeTelegramAdapter)
        assert install_telegram_format_monkeypatch(GatewayLikeTelegramAdapter) is True
        adapter = GatewayLikeTelegramAdapter()
        adapter._hermes_progress_tail_rich_disabled = True
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused"},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        ctx.chat_id = "123"
        ctx.thread_id = None
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "✅ terminal: pytest -q · done · 0.3s"),
            force=True,
        )

        adapter._bot.do_api_request.assert_not_awaited()
        assert adapter.legacy_sent
        assert "**__Tools__**" in adapter.legacy_sent[-1][1]
        assert "| Command | Result |" not in adapter.legacy_sent[-1][1]

    asyncio.run(run())


def test_telegram_edit_capability_latch_disables_later_send_rich_preparation(monkeypatch):
    parse_mode = SimpleNamespace(MARKDOWN_V2="MarkdownV2")
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=parse_mode),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=Result, utf16_len=len),
    )
    uninstall_telegram_format_monkeypatch(ProgressTailPatchedTelegramAdapter)
    assert install_telegram_format_monkeypatch(ProgressTailPatchedTelegramAdapter) is True
    try:
        adapter = ProgressTailPatchedTelegramAdapter()
        adapter._bot.do_api_request = AsyncMock(side_effect=AttributeError("no rich endpoint"))
        edit_result = asyncio.run(adapter.edit_message("123", "456", "progress **bold**"))
        assert edit_result.success is True
        assert adapter._hermes_progress_tail_rich_disabled is True
        adapter._bot.do_api_request.reset_mock(side_effect=True)
        adapter._bot.do_api_request.side_effect = None

        async def run_send():
            renderer = ProgressRenderer(
                load_settings(
                    {
                        "progress_tail": {
                            "tools": {"timestamp": False},
                            "renderer": {"mode": "focused"},
                        }
                    }
                )
            )
            ctx = make_ctx(adapter)
            ctx.chat_id = "123"
            ctx.thread_id = None
            renderer.register_context(ctx)
            await renderer.handle_event(
                ToolEvent("s1", "k1", "telegram", "✅ terminal: pytest -q · done · 0.3s"),
                force=True,
            )

        asyncio.run(run_send())
        adapter._bot.do_api_request.assert_not_awaited()
        assert adapter.legacy_sent
        assert "**__Tools__**" in adapter.legacy_sent[-1][1]
        assert "| Command | Result |" not in adapter.legacy_sent[-1][1]
    finally:
        uninstall_telegram_format_monkeypatch(ProgressTailPatchedTelegramAdapter)


def test_telegram_renderer_can_disable_rich_markdown():
    async def run():
        adapter = CapturingAdapter()
        renderer = ProgressRenderer(
            load_settings(
                {
                    "progress_tail": {
                        "tools": {"timestamp": False},
                        "renderer": {"mode": "focused"},
                        "telegram": {"rich_messages": False},
                    }
                }
            )
        )
        ctx = make_ctx(adapter)
        ctx.chat_id = "123"
        ctx.thread_id = None
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "✅ terminal: pytest -q · done · 0.3s"),
            force=True,
        )

        sent = adapter.sent[-1][1]
        assert "**__Tools__**" in sent
        assert "| Command | Result |" not in sent

    asyncio.run(run())
