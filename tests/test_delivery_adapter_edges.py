import asyncio

import pytest

from hermes_progress_tail.models.state import AssistantLine
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering import delivery
from hermes_progress_tail.settings.loading import load_settings
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
        outcome = self.delete_outcome
        if isinstance(outcome, dict):
            outcome = outcome[message_id]
        if callable(outcome):
            outcome = await outcome(message_id)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SequentialAdapter(EdgeAdapter):
    def __init__(self, outcomes=None):
        super().__init__()
        self.outcomes = list(outcomes or [])
        self.next_id = 2

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        message_id = f"m{self.next_id}"
        self.next_id += 1
        return Result(True, message_id)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


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

        for mutate in (
            lambda: setattr(ctx, "strategy", "snapshot"),
            lambda: setattr(ctx, "disabled", True),
            lambda: ctx.tool_lines.clear(),
        ):
            ctx.disabled = False
            ctx.strategy = "live_tail"
            ctx.tool_lines.clear()
            ctx.tool_lines.append("work")
            activity = (list(ctx.adapter.edits), list(ctx.adapter.sent))
            r._schedule_delayed_live_flush(ctx, 0)
            task = ctx.delayed_flush_task
            mutate()
            await task
            assert ctx.delayed_flush_task is None
            assert (ctx.adapter.edits, ctx.adapter.sent) == activity

        ctx.disabled = False
        ctx.strategy = "live_tail"
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
            ctx.can_edit = True
            ctx.progress_state = "active"
            before = len(ctx.adapter.deleted)
            ctx.adapter.delete_outcome = outcome
            r._schedule_auto_delete(ctx, success=True)
            task = ctx.delete_task
            await task
            assert ctx.delete_task is None
            assert ctx.adapter.deleted[before:] == [(ctx.chat_id, "m1")]
            if outcome is False:
                assert (ctx.message_id, ctx.progress_state, ctx.can_edit) == ("m1", "active", True)
                assert ctx.last_error == ""
            elif isinstance(outcome, Exception):
                assert (ctx.message_id, ctx.progress_state, ctx.can_edit) == ("m1", "active", True)
                assert ctx.last_error == "delete"
            else:
                assert (ctx.message_id, ctx.progress_state, ctx.can_edit) == (
                    None,
                    "deleted",
                    False,
                )
                assert ctx.last_error == "delete"

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


def test_auto_delete_stale_generation_and_message_fences(monkeypatch):
    async def run():
        r = renderer({"cleanup": {"auto_delete": True, "delay_seconds": 0}})
        real_sleep = asyncio.sleep
        for stale_generation in (True, False):
            ctx = make_live_context(EdgeAdapter())
            ctx.message_id = "m1"
            gate = asyncio.Event()

            async def blocked(_delay, gate=gate):
                await gate.wait()

            monkeypatch.setattr(delivery.asyncio, "sleep", blocked)
            r._schedule_auto_delete(ctx, success=True)
            task = ctx.delete_task
            await real_sleep(0)
            if stale_generation:
                ctx.generation += 1
            else:
                ctx.message_id = "m2"
            gate.set()
            await task
            assert ctx.delete_task is None and ctx.adapter.deleted == []
            assert ctx.message_id == ("m1" if stale_generation else "m2")
            assert ctx.progress_state == "active" and ctx.can_edit

    asyncio.run(run())


def test_auto_delete_attempts_ordered_unique_history_and_retains_failures(monkeypatch):
    async def run():
        r = renderer({"cleanup": {"auto_delete": True, "delay_seconds": 0}})
        outcomes = {"m1": True, "m2": False, "m3": RuntimeError("m3 failed")}
        ctx = make_live_context(EdgeAdapter(delete=outcomes))
        ctx.message_id = "m3"
        ctx.delivery.progress_message_ids = ["m1", "m2", "m1"]
        real_sleep = asyncio.sleep

        async def yielding(_delay):
            await real_sleep(0)

        monkeypatch.setattr(delivery.asyncio, "sleep", yielding)
        r._schedule_auto_delete(ctx, success=True)
        await ctx.delete_task

        assert ctx.adapter.deleted == [
            (ctx.chat_id, "m1"),
            (ctx.chat_id, "m2"),
            (ctx.chat_id, "m3"),
        ]
        assert ctx.delivery.progress_message_ids == ["m2"]
        assert (ctx.message_id, ctx.can_edit, ctx.progress_state) == ("m3", True, "active")
        assert ctx.last_error == "m3 failed"

    asyncio.run(run())


def test_auto_delete_does_not_clear_new_active_target_during_delete(monkeypatch):
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked(message_id):
            if message_id == "m2":
                started.set()
                await release.wait()
            return True

        r = renderer({"cleanup": {"auto_delete": True, "delay_seconds": 0}})
        ctx = make_live_context(EdgeAdapter(delete=blocked))
        ctx.message_id = "m2"
        ctx.delivery.progress_message_ids = ["m1", "m2"]
        real_sleep = asyncio.sleep

        async def yielding(_delay):
            await real_sleep(0)

        monkeypatch.setattr(delivery.asyncio, "sleep", yielding)
        r._schedule_auto_delete(ctx, success=True)
        task = ctx.delete_task
        await started.wait()
        ctx.message_id = "m3"
        ctx.delivery.progress_message_ids.append("m3")
        release.set()
        await task

        assert ctx.adapter.deleted == [(ctx.chat_id, "m1"), (ctx.chat_id, "m2")]
        assert ctx.delivery.progress_message_ids == ["m3"]
        assert (ctx.message_id, ctx.can_edit, ctx.progress_state) == ("m3", True, "active")

    asyncio.run(run())


def test_snapshot_titles_caps_and_failures():
    async def run():
        r = renderer({"no_edit": {"max_snapshots_per_turn": 1}})
        ctx = make_live_context(EdgeAdapter(), strategy="snapshot")
        await r._render_snapshot(ctx, force=True)
        assert ctx.adapter.sent == []
        ctx.tool_lines.append("work")
        ctx.total_events = 4
        ctx.thread_id = "t1"
        await r._render_snapshot(ctx, force=True)
        assert ctx.adapter.sent[-1] == (
            ctx.chat_id,
            "Progress tail — latest 1 tools of 4 events\n▰ 🧰 Tools\nwork",
            ctx.metadata,
        )
        assert (ctx.snapshots_sent, ctx.fallback_send_count) == (1, 1)
        await r._render_snapshot(ctx, force=True)
        assert len(ctx.adapter.sent) == 1
        await r._render_snapshot(ctx, force=True, final=True)
        assert ctx.adapter.sent[-1] == (
            ctx.chat_id,
            "Progress tail — final of 4 events\n▰ 🧰 Tools\nwork",
            ctx.metadata,
        )
        assert (ctx.snapshots_sent, ctx.fallback_send_count) == (2, 2)

        updates = make_live_context(EdgeAdapter(), strategy="snapshot")
        updates.assistant_lines.append(AssistantLine("done"))
        updates.total_events = 2
        updates.thread_id = "edge"
        await r._render_snapshot(updates, force=True)
        assert updates.adapter.sent == [
            (
                updates.chat_id,
                "Progress tail — latest updates of 2 events\n▰ 💬 Progress\ndone",
                updates.metadata,
            )
        ]
        assert (updates.snapshots_sent, updates.fallback_send_count) == (1, 1)

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
    # Telegram rich conversion belongs to the send/edit monkeypatch, not delivery.
    assert not hasattr(delivery, "_prepare_telegram_rich_message")
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


def test_live_rollover_boundaries_and_failure_semantics(monkeypatch):
    async def run():
        r = renderer({"renderer": {"message_rollover_minutes": 5}})
        adapter = SequentialAdapter()
        ctx = make_live_context(adapter)
        ctx.tool_lines.append("work")
        ctx.message_id = "m1"
        ctx.delivery.message_started_at = 100.0
        ctx.delivery.progress_message_ids = ["m1"]
        monkeypatch.setattr(delivery.time, "monotonic", lambda: 399.999)
        await r._render_live(ctx, force=True)
        assert adapter.edits[-1][1] == "m1" and adapter.sent == []
        monkeypatch.setattr(delivery.time, "monotonic", lambda: 400.0)
        edits_before = list(adapter.edits)
        await r._render_live(ctx, force=True)
        assert (ctx.message_id, ctx.delivery.message_started_at) == ("m2", 400.0)
        assert ctx.delivery.progress_message_ids == ["m1", "m2"]
        assert adapter.edits == edits_before

        for error, expected_state in (("forbidden", "editable"), ("timeout", "transient")):
            failed = make_live_context(SequentialAdapter([Result(False, error=error)]))
            failed.loop = None
            failed.tool_lines.append("work")
            failed.message_id = "m1"
            failed.delivery.message_started_at = 100.0
            failed.delivery.progress_message_ids = ["m1"]
            await r._render_live(failed, force=True)
            assert (failed.message_id, failed.delivery.message_started_at) == ("m1", 100.0)
            assert failed.delivery.progress_message_ids == ["m1"]
            assert failed.edit_state == expected_state and failed.disabled is False
            assert failed.adapter.edits == []
        assert failed.edit_backoff_until == 401.0

    asyncio.run(run())


@pytest.mark.parametrize("replacement_id", [None, "", "m1"])
def test_rollover_malformed_success_preserves_transaction(monkeypatch, replacement_id):
    async def run():
        r = renderer()
        monkeypatch.setattr(delivery.time, "monotonic", Clock(400.0))
        ctx = make_live_context(EdgeAdapter(send=Result(True, replacement_id)))
        ctx.message_id = "m1"
        ctx.delivery.message_started_at = 100.0
        ctx.delivery.progress_message_ids = ["older", "m1"]
        ctx.assistant_pending_chars = 7
        ctx.reasoning_pending_chars = 11
        ctx.edit_recovery_sends = 2
        ctx.fallback_send_count = 3
        ctx.last_render_at = 90.0
        ctx.edit_state = "transient"
        ctx.edit_failure_count = 4
        ctx.edit_backoff_until = 450.0

        assert await r.delivery.send_live_message(ctx, "work", rollover=True) is False
        assert (ctx.message_id, ctx.delivery.message_started_at) == ("m1", 100.0)
        assert ctx.delivery.progress_message_ids == ["older", "m1"]
        assert (ctx.assistant_pending_chars, ctx.reasoning_pending_chars) == (7, 11)
        assert (ctx.edit_recovery_sends, ctx.fallback_send_count) == (2, 3)
        assert ctx.last_render_at == 90.0
        assert (ctx.edit_state, ctx.edit_failure_count, ctx.edit_backoff_until) == (
            "transient",
            4,
            450.0,
        )
        assert ctx.can_edit and not ctx.disabled

    asyncio.run(run())


def test_transient_rollover_retries_only_after_backoff(monkeypatch):
    async def run():
        r = renderer({"renderer": {"message_rollover_minutes": 5}})
        clock = Clock(400.0)
        monkeypatch.setattr(delivery.time, "monotonic", clock)
        adapter = SequentialAdapter(
            [Result(False, error="timeout"), Result(False, error="timeout")]
        )
        ctx = make_live_context(adapter)
        ctx.loop = None
        ctx.tool_lines.append("work")
        ctx.message_id = "m1"
        ctx.delivery.message_started_at = 100.0
        ctx.delivery.progress_message_ids = ["m1"]
        ctx.assistant_pending_chars = 7

        await r._render_live(ctx, force=True)
        assert len(adapter.sent) == 1
        assert (ctx.edit_failure_count, ctx.edit_backoff_until) == (1, 401.0)
        assert ctx.delivery.message_started_at == 100.0 and ctx.assistant_pending_chars == 7
        clock.now = 400.999
        await r._render_live(ctx, force=True)
        assert len(adapter.sent) == 1
        clock.now = 401.0
        await r._render_live(ctx, force=True)
        assert len(adapter.sent) == 2
        assert (ctx.edit_failure_count, ctx.edit_backoff_until) == (2, 403.0)
        assert ctx.delivery.message_started_at == 100.0 and ctx.assistant_pending_chars == 7

    asyncio.run(run())


def test_unknown_timestamp_and_recovery_activation(monkeypatch):
    async def run():
        r = renderer()
        monkeypatch.setattr(delivery.time, "monotonic", lambda: 250.0)
        for outcome in (Result(True, "m1"), Result(False, "m1", "message is not modified")):
            ctx = make_live_context(EdgeAdapter(edit=outcome))
            ctx.tool_lines.append("work")
            ctx.message_id = "m1"
            await r._render_live(ctx, force=True)
            assert ctx.adapter.sent == [] and ctx.delivery.message_started_at == 250.0

        adapter = SequentialAdapter()
        adapter.edit_outcome = Result(False, "m1", "message to edit not found")
        recovered = make_live_context(adapter)
        recovered.tool_lines.append("work")
        recovered.message_id = "m1"
        recovered.delivery.message_started_at = 100.0
        recovered.delivery.progress_message_ids = ["m1"]
        await r._render_live(recovered, force=True)
        assert (recovered.message_id, recovered.delivery.message_started_at) == ("m2", 250.0)
        assert recovered.delivery.progress_message_ids == ["m1", "m2"]
        assert (recovered.edit_recovery_sends, recovered.fallback_send_count) == (1, 1)
        assert (recovered.assistant_pending_chars, recovered.reasoning_pending_chars) == (0, 0)
        assert (
            recovered.edit_state,
            recovered.edit_failure_count,
            recovered.edit_backoff_until,
        ) == (
            "editable",
            0,
            0.0,
        )

        for replacement_id in (None, "", "m1"):
            malformed = make_live_context(EdgeAdapter(send=Result(True, replacement_id)))
            malformed.message_id = "m1"
            malformed.delivery.message_started_at = 100.0
            malformed.delivery.progress_message_ids = ["m1"]
            malformed.assistant_pending_chars = 5
            malformed.reasoning_pending_chars = 6
            malformed.edit_state = "recovering"
            malformed.edit_failure_count = 1
            malformed.edit_backoff_until = 275.0
            assert await r._send_live_message(malformed, "work", recovery=True) is False
            assert (malformed.message_id, malformed.delivery.message_started_at) == ("m1", 100.0)
            assert malformed.delivery.progress_message_ids == ["m1"]
            assert (malformed.edit_recovery_sends, malformed.fallback_send_count) == (0, 0)
            assert (malformed.assistant_pending_chars, malformed.reasoning_pending_chars) == (5, 6)
            assert (
                malformed.edit_state,
                malformed.edit_failure_count,
                malformed.edit_backoff_until,
            ) == ("recovering", 1, 275.0)

    asyncio.run(run())


def test_snapshot_delivery_does_not_consult_rollover(monkeypatch):
    async def run():
        r = renderer()
        ctx = make_live_context(EdgeAdapter(), strategy="snapshot")
        ctx.tool_lines.append("work")
        monkeypatch.setattr(
            r.delivery,
            "message_rollover_due",
            lambda *_args: (_ for _ in ()).throw(AssertionError("rollover consulted")),
            raising=False,
        )
        await r._render_snapshot(ctx, force=True)
        assert len(ctx.adapter.sent) == 1

    asyncio.run(run())
