import asyncio

import pytest

from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.rendering import delivery
from hermes_progress_tail.settings.loading import load_settings
from tests.support.rendering import EditableAdapter, make_live_context


def renderer():
    settings = load_settings(
        {"progress_tail": {"cleanup": {"auto_delete": True, "delay_seconds": 0}}}
    )
    return ProgressRenderer(settings)


class DeleteAdapter(EditableAdapter):
    def __init__(self, delete):
        super().__init__()
        self.delete = delete
        self.deleted = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        outcome = self.delete[message_id] if isinstance(self.delete, dict) else self.delete
        if callable(outcome):
            outcome = await outcome(message_id)
        return outcome


async def schedule(ctx, monkeypatch):
    real_sleep = asyncio.sleep

    async def yielding(_delay):
        await real_sleep(0)

    monkeypatch.setattr(delivery.asyncio, "sleep", yielding)
    renderer()._schedule_auto_delete(ctx, success=True)
    return ctx.delete_task


def test_cleanup_all_success_clears_exact_delivery_state(monkeypatch):
    async def run():
        ctx = make_live_context(DeleteAdapter(True))
        ctx.message_id = "m3"
        ctx.delivery.progress_message_ids = ["m1", "m2", "m3"]

        task = await schedule(ctx, monkeypatch)
        await task

        assert ctx.adapter.deleted == [(ctx.chat_id, id_) for id_ in ("m1", "m2", "m3")]
        assert ctx.delivery.progress_message_ids == []
        assert (ctx.message_id, ctx.can_edit, ctx.progress_state) == (None, False, "deleted")

    asyncio.run(run())


def test_cleanup_partial_failure_retains_only_failed_id_and_clears_active(monkeypatch):
    async def run():
        ctx = make_live_context(DeleteAdapter({"m1": True, "m2": False, "m3": True}))
        ctx.message_id = "m3"
        ctx.delivery.progress_message_ids = ["m1", "m2", "m3"]

        task = await schedule(ctx, monkeypatch)
        await task

        assert ctx.adapter.deleted == [(ctx.chat_id, id_) for id_ in ("m1", "m2", "m3")]
        assert ctx.delivery.progress_message_ids == ["m2"]
        assert (ctx.message_id, ctx.can_edit, ctx.progress_state) == (None, False, "deleted")

    asyncio.run(run())


def test_cleanup_stops_without_mutating_new_generation_after_await(monkeypatch):
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def blocked(_message_id):
            started.set()
            await release.wait()
            return True

        ctx = make_live_context(DeleteAdapter({"m1": blocked, "m2": True, "m3": True}))
        ctx.message_id = "m3"
        ctx.delivery.progress_message_ids = ["m1", "m2", "m3"]
        task = await schedule(ctx, monkeypatch)
        await started.wait()

        ctx.generation += 1
        newer_delivery = type(ctx.delivery)()
        newer_delivery.message_id = "new"
        newer_delivery.progress_message_ids = ["new-history"]
        newer_delivery.can_edit = True
        newer_delivery.progress_state = "new-active"
        ctx.delivery = newer_delivery
        release.set()
        await task

        assert ctx.adapter.deleted == [(ctx.chat_id, "m1")]
        assert ctx.delivery is newer_delivery
        assert ctx.delivery.progress_message_ids == ["new-history"]
        assert (ctx.message_id, ctx.can_edit, ctx.progress_state) == ("new", True, "new-active")

    asyncio.run(run())


def test_cleanup_cancellation_retains_failed_and_unattempted_ids(monkeypatch):
    async def run():
        started = asyncio.Event()

        async def blocked(_message_id):
            started.set()
            await asyncio.Event().wait()

        ctx = make_live_context(DeleteAdapter({"m1": True, "m2": blocked, "m3": True}))
        ctx.message_id = "m3"
        ctx.delivery.progress_message_ids = ["m1", "m2", "m3"]
        task = await schedule(ctx, monkeypatch)
        await started.wait()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert ctx.adapter.deleted == [(ctx.chat_id, "m1"), (ctx.chat_id, "m2")]
        assert ctx.delivery.progress_message_ids == ["m2", "m3"]
        assert (ctx.message_id, ctx.can_edit, ctx.progress_state) == ("m3", True, "active")

    asyncio.run(run())
