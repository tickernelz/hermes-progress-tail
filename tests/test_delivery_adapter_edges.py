import asyncio

import pytest

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering import delivery
from tests.support.rendering import EditableAdapter, Result, make_live_context


def renderer(config=None):
    return ProgressRenderer(load_settings({"progress_tail": config or {}}))


class EdgeAdapter(EditableAdapter):
    def __init__(self, *, edit=None, send=None, delete=True):
        super().__init__()
        self.edit_outcome = edit
        self.send_outcome = send
        self.delete_outcome = delete
        self.deleted = []

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        if isinstance(self.edit_outcome, Exception):
            raise self.edit_outcome
        return self.edit_outcome or Result(True, message_id)

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        if isinstance(self.send_outcome, Exception):
            raise self.send_outcome
        return self.send_outcome or Result(True, "m1")

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        if isinstance(self.delete_outcome, Exception):
            raise self.delete_outcome
        return self.delete_outcome


def test_edit_exception_and_not_modified_success():
    async def run():
        r = renderer()
        ctx = make_live_context(EdgeAdapter(edit=RuntimeError("boom")))
        ctx.message_id = "m1"
        ctx.tool_lines.append("work")
        await r._render_live(ctx, force=True, ignore_backoff=True)
        assert ctx.last_error == "boom"
        assert ctx.edit_state == "unknown_transient"
        assert ctx.edit_failure_count == 1

        ctx.adapter.edit_outcome = Result(False, "m1", "message is not modified")
        ctx.assistant_pending_chars = 4
        await r._render_live(ctx, force=True)
        assert ctx.edit_state == "editable"
        assert ctx.assistant_pending_chars == 0

    asyncio.run(run())


def test_repeated_failed_send_state():
    async def run():
        r = renderer()
        ctx = make_live_context(EdgeAdapter(send=Result(False, error="timeout")))
        ctx.tool_lines.append("work")
        await r._send_live_message(ctx, "work")
        task = ctx.delayed_flush_task
        assert ctx.edit_failure_count == 1
        if task:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        ctx.delayed_flush_task = None
        await r._send_live_message(ctx, "work")
        task = ctx.delayed_flush_task
        assert ctx.edit_failure_count == 2
        if task:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        r._cancel_delayed_flush(ctx)
        assert ctx.delayed_flush_task is None

    asyncio.run(run())


def test_delayed_flush_guards_success_and_cancellation(monkeypatch):
    async def run():
        r = renderer()
        ctx = make_live_context(EdgeAdapter())
        ctx.loop = None
        r._schedule_delayed_live_flush(ctx, 1)
        assert ctx.delayed_flush_task is None
        ctx.loop = asyncio.get_running_loop()

        async def instant(_delay):
            await asyncio.sleep(0)

        real_sleep = asyncio.sleep

        async def yielding(_delay):
            await real_sleep(0)

        monkeypatch.setattr(delivery.asyncio, "sleep", yielding)
        ctx.tool_lines.append("work")
        r._schedule_delayed_live_flush(ctx, 1)
        task = ctx.delayed_flush_task
        r._schedule_delayed_live_flush(ctx, 1)
        assert ctx.delayed_flush_task is task
        ctx.generation += 1
        await task
        assert ctx.delayed_flush_task is None

        for mutate in (lambda: setattr(ctx, "disabled", True), lambda: ctx.tool_lines.clear()):
            ctx.disabled = False
            ctx.tool_lines.clear()
            ctx.tool_lines.append("work")
            r._schedule_delayed_live_flush(ctx, 0)
            task = ctx.delayed_flush_task
            mutate()
            await task
            assert ctx.delayed_flush_task is None

        ctx.disabled = False
        ctx.tool_lines.append("work")
        r._schedule_delayed_live_flush(ctx, 0)
        await ctx.delayed_flush_task
        assert ctx.message_id == "m1"

        gate = asyncio.Event()

        async def blocked(_delay):
            await gate.wait()

        monkeypatch.setattr(delivery.asyncio, "sleep", blocked)
        r._schedule_delayed_live_flush(ctx, 0)
        task = ctx.delayed_flush_task
        await real_sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ctx.delayed_flush_task is None

    asyncio.run(run())


def test_auto_delete_gates_outcomes_and_cancel(monkeypatch):
    async def run():
        r = renderer({"cleanup": {"auto_delete": True, "delay_seconds": 0}})
        ctx = make_live_context(EdgeAdapter())
        ctx.message_id = "m1"
        real_sleep = asyncio.sleep

        async def yielding(_delay):
            await real_sleep(0)

        monkeypatch.setattr(delivery.asyncio, "sleep", yielding)
        disabled = renderer({"cleanup": {"auto_delete": True, "delete_on_success": False}})
        disabled._schedule_auto_delete(ctx, success=True)
        assert ctx.delete_task is None
        ctx.progress_state = "background_active"
        r._schedule_auto_delete(ctx, success=True)
        assert ctx.delete_task is None
        ctx.progress_state = "active"

        for outcome in (False, RuntimeError("delete"), True):
            ctx.message_id = "m1"
            ctx.adapter.delete_outcome = outcome
            r._schedule_auto_delete(ctx, success=True)
            task = ctx.delete_task
            await task
            assert ctx.delete_task is None
        assert ctx.last_error == "delete"
        assert ctx.message_id is None and ctx.progress_state == "deleted"

        ctx.message_id = "m2"
        gate = asyncio.Event()

        async def blocked(_delay):
            await gate.wait()

        monkeypatch.setattr(delivery.asyncio, "sleep", blocked)
        r._schedule_auto_delete(ctx, success=True)
        task = ctx.delete_task
        await real_sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ctx.delete_task is None

    asyncio.run(run())


def test_snapshot_titles_caps_and_failures():
    async def run():
        r = renderer({"no_edit": {"max_snapshots_per_turn": 1}})
        ctx = make_live_context(EdgeAdapter(), strategy="snapshot")
        await r._render_snapshot(ctx, force=True)
        assert ctx.adapter.sent == []
        ctx.tool_lines.append("work")
        ctx.total_events = 4
        await r._render_snapshot(ctx, force=True)
        assert (
            ctx.adapter.sent[-1][1]
            == "Progress tail — latest 1 tools of 4 events\n▰ 🧰 Tools\nwork"
        )
        assert (ctx.snapshots_sent, ctx.fallback_send_count) == (1, 1)
        await r._render_snapshot(ctx, force=True)
        assert len(ctx.adapter.sent) == 1
        await r._render_snapshot(ctx, force=True, final=True)
        assert "Progress tail — final of 4 events" in ctx.adapter.sent[-1][1]

        bad = make_live_context(EdgeAdapter(send=RuntimeError("send")), strategy="snapshot")
        bad.tool_lines.append("work")
        await r._render_snapshot(bad, force=True)
        assert bad.disabled and bad.last_error == "send"
        failed = make_live_context(EdgeAdapter(send=Result(False, error="no")), strategy="snapshot")
        failed.tool_lines.append("work")
        await r._render_snapshot(failed, force=True)
        assert failed.disabled and failed.last_error == "no"

    asyncio.run(run())


def test_delivery_pure_boundaries():
    ctx = type("Ctx", (), {"platform": "telegram"})()
    assert delivery._prepare_telegram_rich_message(None, ctx, "**rich**") == "**rich**"
    assert delivery._fit_message("abc", 0) == "abc"
    assert delivery._fit_message("abcdef", 2) == "ab"
    fitted = delivery._fit_message("abcdefghijklmnopqrstuvwxyz", 10)
    assert "\n…\n" in fitted and len(fitted) == 10
    assert delivery._message_limit(ctx) == 4096
    ctx.platform = "discord"
    assert delivery._message_limit(ctx) == 0
    assert delivery._classify_edit_error("not modified") == "noop_success"
    assert delivery._edit_backoff_seconds("", "rate_limited", 20) == 30
    assert delivery._edit_backoff_seconds("", "too_long", 2) == 1
