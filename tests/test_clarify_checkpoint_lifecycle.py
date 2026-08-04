import asyncio
from types import SimpleNamespace

from hermes_progress_tail.models.decision import (
    ClarifyCheckpoint,
    DecisionRecord,
    FreezeReservation,
)
from hermes_progress_tail.models.events import (
    AssistantEvent,
    DecisionEvent,
    ReasoningEvent,
    ToolEvent,
)
from hermes_progress_tail.rendering.lifecycle import RendererLifecycle
from hermes_progress_tail.rendering.renderer import ProgressRenderer
from hermes_progress_tail.settings.loading import load_settings
from tests.support.rendering import EditableAdapter, Result, make_live_context


class RecordingAdapter(EditableAdapter):
    def __init__(self):
        super().__init__()
        self.deleted = []

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        return True


def settings(**extra):
    return load_settings(
        {
            "progress_tail": {
                "tools": {"timestamp": False},
                "cleanup": {"auto_delete": True, "delete_on_success": True, "delay_seconds": 0},
                **extra,
            }
        }
    )


def context(adapter=None, *, source="source", loop=None):
    adapter = adapter or RecordingAdapter()
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        ctx = asyncio.run(_make_context(adapter))
    else:
        ctx = make_live_context(adapter, timestamp=False)
    ctx.session_id = "session"
    ctx.session_key = "stable-key"
    ctx.loop = loop
    ctx.routing.source_message_id = source
    return ctx


async def _make_context(adapter):
    return make_live_context(adapter, timestamp=False)


def frozen(ctx, message_id="checkpoint", *, editable=True):
    ctx.decision.active_checkpoint = ClarifyCheckpoint(
        1, message_id, "Continue?", ("yes",), "frozen", editable, "FROZEN"
    )
    ctx.decision.reconcile_protected_message_ids()


def pending(ctx, message_id="progress"):
    ctx.decision.pending_freeze = FreezeReservation(ctx.generation, message_id, 1.0, 0)
    ctx.decision.reconcile_protected_message_ids()


def test_renderer_composes_one_controller_and_lifecycle_with_settings_replacement():
    renderer = ProgressRenderer(settings())
    assert renderer.checkpoints.__class__.__name__ == "ClarifyCheckpointController"
    assert isinstance(renderer.lifecycle, RendererLifecycle)
    assert renderer.lifecycle.checkpoints is renderer.checkpoints
    replacement = settings(renderer={"density": "verbose"})
    renderer.replace_settings(replacement)
    assert renderer.checkpoints.delivery.settings is replacement
    assert renderer.lifecycle.settings is replacement


def test_decision_event_is_archive_only_and_reasoning_never_enters_archive():
    async def run():
        adapter = RecordingAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, loop=asyncio.get_running_loop())
        renderer.register_context(ctx)
        record = DecisionRecord("tool", "verified result", "tool:1", 40, 1.0)
        await renderer.handle_event(DecisionEvent("session", "stable-key", "discord", record))
        assert tuple(ctx.decision.records) == (record,)
        assert not ctx.tool.lines and not adapter.sent and not adapter.edits
        await renderer.handle_event(
            ReasoningEvent("session", "stable-key", "discord", "provider private payload"),
            force=True,
        )
        assert tuple(ctx.decision.records) == (record,)
        await renderer.handle_event(
            AssistantEvent("session", "stable-key", "discord", "normalized assistant fact"),
            force=True,
        )
        assert ctx.decision.records[-1].kind == "assistant"
        assert ctx.decision.records[-1].text == "normalized assistant fact"

    asyncio.run(run())


def test_waiting_events_reduce_and_archive_without_a_second_live_message():
    async def run():
        adapter = RecordingAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, loop=asyncio.get_running_loop())
        renderer.register_context(ctx)
        frozen(ctx, "checkpoint")
        await renderer.handle_event(
            ToolEvent("session", "stable-key", "discord", "ordinary tool"), force=True
        )
        await renderer.handle_event(
            AssistantEvent("session", "stable-key", "discord", "ordinary assistant"), force=True
        )
        assert list(ctx.tool.lines) == ["ordinary tool"]
        assert ctx.decision.records[-1].text == "ordinary assistant"
        assert not adapter.sent and not adapter.edits

    asyncio.run(run())


def test_delayed_flush_and_delegate_cleanup_recheck_waiting_under_lock():
    async def run():
        adapter = RecordingAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, loop=asyncio.get_running_loop())
        renderer.register_context(ctx)
        ctx.tool.lines.append("buffered")
        frozen(ctx)
        renderer.delivery.schedule_delayed_live_flush(ctx, 0)
        await asyncio.sleep(0.1)
        assert not adapter.sent and not adapter.edits

        branch = SimpleNamespace(cleanup_task=None)
        ctx.delegate.branches["done"] = branch
        renderer.delegate_renderer.prune_completed = lambda _ctx: ctx.delegate.branches.pop("done")
        renderer._schedule_delegate_cleanup(ctx, "done", branch)
        await asyncio.sleep(renderer.settings.delegates.completed_ttl_seconds + 0.1)
        assert "done" not in ctx.delegate.branches
        assert not adapter.sent and not adapter.edits

    asyncio.run(run())


def test_resolution_releases_checkpoint_and_next_event_sends_new_bubble():
    async def run():
        adapter = RecordingAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, loop=asyncio.get_running_loop())
        renderer.register_context(ctx)
        frozen(ctx, "checkpoint")
        assert await renderer.resolve_clarify(ctx, "resolved", "yes")
        assert len(adapter.edits) == 1
        assert adapter.edits[0][0:2] == ("chat", "checkpoint")
        assert adapter.edits[0][2].endswith("RESOLVED · yes")
        await renderer.handle_event(
            ToolEvent("session", "stable-key", "discord", "next tool"), force=True
        )
        assert len(adapter.sent) == 1
        assert ctx.delivery.message_id != "checkpoint"
        assert [entry[1] for entry in adapter.edits] == ["checkpoint"]

    asyncio.run(run())


def test_auto_delete_rechecks_epoch_and_checkpoint_protection_under_lock():
    async def run():
        adapter = RecordingAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, loop=asyncio.get_running_loop())
        ctx.delivery.message_id = "progress"
        ctx.delivery.progress_message_ids.append("progress")
        renderer.register_context(ctx)
        renderer.delivery.schedule_auto_delete(ctx, success=True)
        pending(ctx, "progress")
        ctx.decision.cleanup_epoch += 1
        await asyncio.sleep(0.1)
        assert adapter.deleted == []
        assert ctx.decision.protected_message_ids == {"progress"}

    asyncio.run(run())


def test_new_source_retires_checkpoint_state_without_adapter_call_and_same_source_reuses_it():
    adapter = RecordingAdapter()
    renderer = ProgressRenderer(settings())
    old = context(adapter, source="old")
    old.delivery.message_id = "checkpoint"
    old.delivery.progress_message_ids.append("checkpoint")
    frozen(old, "checkpoint")
    renderer.register_context(old)

    same = context(adapter, source="old")
    renderer.register_context(same)
    assert same.decision is old.decision

    new = context(adapter, source="new")
    renderer.register_context(new)
    assert new.decision is not same.decision
    assert not same.decision.records
    assert same.decision.active_checkpoint is None
    assert same.decision.pending_freeze is None
    assert not same.decision.protected_message_ids
    assert same.decision.cleanup_epoch > 0
    assert not adapter.sent and not adapter.edits


def test_registry_purge_prefers_stable_key_over_reused_stale_id_without_adapter_call():
    adapter = RecordingAdapter()
    renderer = ProgressRenderer(settings())
    migrated = context(adapter)
    frozen(migrated)
    renderer.register_context(migrated)
    assert renderer.migrate_context("session", "new-session", "stable-key")
    unrelated = context(adapter, source="other")
    unrelated.session_id = "session"
    unrelated.session_key = "other-key"
    renderer.register_context(unrelated)

    renderer.purge(session_id="session", session_key="stable-key")
    assert renderer.find_context("", "stable-key") is None
    assert renderer.find_context("session", "other-key") is unrelated
    assert not adapter.sent and not adapter.edits


def test_pending_and_frozen_finalize_only_interrupts_once_and_suppresses_ordinary_flush():
    async def run():
        adapter = RecordingAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, loop=asyncio.get_running_loop())
        ctx.delivery.message_id = "checkpoint"
        ctx.delivery.progress_message_ids.append("checkpoint")
        ctx.tool.lines.append("ordinary")
        frozen(ctx, "checkpoint")
        renderer.register_context(ctx)
        await asyncio.gather(
            renderer.finalize("session", "stable-key", success=False),
            renderer.resolve_clarify(ctx, "resolved", "yes"),
        )
        assert len(adapter.edits) == 1
        assert not adapter.sent
        assert ctx.decision.active_checkpoint is None

        second = context(adapter, source="second", loop=asyncio.get_running_loop())
        pending(second, "pending")
        second.delivery.message_id = "pending"
        second.delivery.progress_message_ids.append("pending")
        renderer.register_context(second)
        before = len(adapter.edits)
        await renderer.finalize("session", "stable-key", success=False)
        assert len(adapter.edits) == before
        assert second.decision.pending_freeze is None
        assert "pending" not in second.delivery.progress_message_ids
        assert not second.decision.protected_message_ids

    asyncio.run(run())


def test_disabled_frozen_finalize_interrupts_and_retires_checkpoint_state():
    async def run():
        adapter = RecordingAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, loop=asyncio.get_running_loop())
        ctx.delivery.message_id = "checkpoint"
        ctx.delivery.progress_message_ids.append("checkpoint")
        ctx.delivery.disabled = True
        frozen(ctx, "checkpoint")
        renderer.register_context(ctx)
        cleanup_epoch = ctx.decision.cleanup_epoch

        await renderer.finalize("session", "stable-key", success=False)

        assert len(adapter.edits) == 1
        assert adapter.edits[0][0:2] == ("chat", "checkpoint")
        assert adapter.edits[0][2].endswith("INTERRUPTED · task stopped while waiting")
        assert ctx.decision.active_checkpoint is None
        assert not ctx.decision.protected_message_ids
        assert ctx.decision.cleanup_epoch > cleanup_epoch
        assert ctx.delivery.message_id is None
        assert "checkpoint" not in ctx.delivery.progress_message_ids

    asyncio.run(run())


def test_reset_turn_lifecycle_fences_and_clears_decision_state():
    async def run():
        adapter = RecordingAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, loop=asyncio.get_running_loop())
        ctx.delivery.message_id = "checkpoint"
        ctx.delivery.progress_message_ids.append("checkpoint")
        ctx.decision.records.append(DecisionRecord("assistant", "fact", "a", 1, 1))
        frozen(ctx, "checkpoint")
        renderer.register_context(ctx)
        await renderer.finalize("session", "stable-key", success=False)
        assert not ctx.decision.records
        assert ctx.decision.active_checkpoint is None
        assert ctx.decision.pending_freeze is None
        assert ctx.decision.cleanup_epoch > 0
        assert not ctx.decision.protected_message_ids
        assert "checkpoint" not in ctx.delivery.progress_message_ids

    asyncio.run(run())


def test_cancellation_resistant_freeze_loses_registry_ownership_without_commit_or_fallback():
    class BlockingAdapter(RecordingAdapter):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def edit_message(self, chat_id, message_id, content):
            self.started.set()
            await self.release.wait()
            return Result(True, message_id)

    async def run():
        adapter = BlockingAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, loop=asyncio.get_running_loop())
        ctx.delivery.message_id = "progress"
        ctx.delivery.progress_message_ids.append("progress")
        renderer.register_context(ctx)
        operation = asyncio.create_task(
            renderer.freeze_clarify(ctx, {"question": "go?", "choices": []}, lambda: False)
        )
        await adapter.started.wait()
        renderer.purge(session_id="session", session_key="stable-key")
        adapter.release.set()
        assert not await operation
        assert ctx.decision.sequence == 0
        assert ctx.decision.active_checkpoint is None
        assert ctx.decision.pending_freeze is None
        assert not ctx.decision.protected_message_ids
        assert "progress" not in ctx.delivery.progress_message_ids
        assert not adapter.sent

    asyncio.run(run())
