import asyncio
import time

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import (
    ToolEvent,
)
from tests.support.rendering import (
    ExceptionSendAdapter,
    SequenceSendAdapter,
)
from tests.support.rendering import (
    make_live_context as make_ctx,
)


def test_initial_send_bad_gateway_backs_off_without_disabling_context():
    async def run():
        adapter = SequenceSendAdapter(["Bad Gateway"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.message_id is None
        assert ctx.edit_state == "transient"
        assert ctx.edit_backoff_until > 0
        assert len(adapter.sent) == 1

        ctx.edit_backoff_until = 0
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "two"), force=True)

        assert ctx.disabled is False
        assert ctx.message_id == "m1"
        assert len(adapter.sent) == 2
        assert adapter.sent[-1][1] == "▰ 🧰 Tools\none\ntwo"

    asyncio.run(run())


def test_initial_send_exception_bad_gateway_backs_off_without_disabling_context():
    async def run():
        adapter = ExceptionSendAdapter(["Bad Gateway"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.message_id is None
        assert ctx.edit_state == "transient"
        assert ctx.edit_backoff_until > 0
        assert len(adapter.sent) == 1

        ctx.edit_backoff_until = 0
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "two"), force=True)

        assert ctx.disabled is False
        assert ctx.message_id == "m1"
        assert len(adapter.sent) == 2
        assert adapter.sent[-1][1] == "▰ 🧰 Tools\none\ntwo"

    asyncio.run(run())


def test_initial_send_flood_control_uses_backoff_without_disabling_context():
    async def run():
        adapter = SequenceSendAdapter(["flood_control:5"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.edit_state == "rate_limited"
        assert ctx.edit_backoff_until > time.monotonic()
        assert ctx.edit_backoff_until - time.monotonic() <= 5.5

    asyncio.run(run())


def test_initial_send_flood_control_retry_in_format_uses_server_backoff():
    """Telegram sendRichMessage flood errors say 'Retry in N seconds'.

    The old regex only matched 'retry after' / 'flood_control:' / 'retry_after='.
    'Retry in' was missed, so the plugin retried every 30s against a multi-hour
    penalty and spiraled into worse flood control.
    """

    async def run():
        adapter = SequenceSendAdapter(["Flood control exceeded. Retry in 120 seconds"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.edit_state == "rate_limited"
        assert ctx.edit_backoff_until > time.monotonic()
        # Should honor the 120s server request, capped at 600s max.
        assert ctx.edit_backoff_until - time.monotonic() >= 100.0
        assert ctx.edit_backoff_until - time.monotonic() <= 120.5

    asyncio.run(run())


def test_flood_control_severe_backoff_is_capped_at_600s_not_30s():
    """A multi-hour penalty must not be capped to 30s — that causes a spiral."""

    async def run():
        adapter = SequenceSendAdapter(["Flood control exceeded. Retry in 11220 seconds"])
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter, platform="telegram")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "one"), force=True)

        assert ctx.disabled is False
        assert ctx.edit_state == "rate_limited"
        # Must be capped at 600s (10 min), not 30s.
        assert ctx.edit_backoff_until - time.monotonic() > 60.0

    asyncio.run(run())
