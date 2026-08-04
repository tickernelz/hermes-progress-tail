import asyncio
from types import SimpleNamespace

from hermes_progress_tail.rendering.clarify_checkpoint import ClarifyCheckpointController
from hermes_progress_tail.rendering.delivery import RendererDelivery, edit_content, send_content
from hermes_progress_tail.settings.loading import load_settings
from tests.support.rendering import (
    EditableAdapter,
    NoEditAdapter,
    Result,
    SequenceEditAdapter,
    make_live_context,
)


class BlockingEditAdapter(EditableAdapter):
    def __init__(self, result=None):
        super().__init__()
        self.result = result or Result(True, "m0")
        self.started, self.release = asyncio.Event(), asyncio.Event()

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        self.started.set()
        await self.release.wait()
        return self.result


class BlockingSendAdapter(EditableAdapter):
    def __init__(self, result=None):
        super().__init__()
        self.result = result or Result(True, "checkpoint")
        self.started, self.release = asyncio.Event(), asyncio.Event()

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        self.started.set()
        await self.release.wait()
        return self.result


class ResultsAdapter(EditableAdapter):
    def __init__(self, *, edit_results=(), send_results=()):
        super().__init__()
        self.edit_results, self.send_results = list(edit_results), list(send_results)

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return self.edit_results.pop(0) if self.edit_results else Result(True, message_id)

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        return (
            self.send_results.pop(0)
            if self.send_results
            else await super().send(chat_id, content, metadata)
        )


class BareResultAdapter:
    async def send(self, chat_id, content, metadata=None):
        return True

    async def edit_message(self, chat_id, message_id, content):
        return SimpleNamespace(success=True, message_id="replacement-event")


def controller():
    settings = load_settings({"progress_tail": {"tools": {"timestamp": False}}})
    return ClarifyCheckpointController(RendererDelivery(settings, lambda ctx: "live"))


def args():
    return {"question": "Choose scope?", "choices": ["default", "all"]}


def prepare(ctx, message_id="m0"):
    ctx.delivery.message_id = message_id
    ctx.delivery.message_started_at = 8.0
    ctx.delivery.progress_message_ids[:] = [message_id] if message_id else []
    ctx.tool.lines.append("old tool")
    ctx.assistant.latest_text = "old assistant"


def test_checkpoint_adapter_wrappers_normalize_result_shape_without_claiming_delivery_state():
    async def run():
        ctx = make_live_context(BareResultAdapter())
        prepare(ctx, "ordinary")
        sent = await send_content(ctx, "checkpoint")
        edited = await edit_content(ctx, "ordinary", "checkpoint")
        assert sent.success and sent.message_id is None and sent.error == ""
        assert edited.success and edited.message_id == "replacement-event" and edited.error == ""
        assert ctx.delivery.message_id == "ordinary" and ctx.delivery.message_started_at == 8.0
        assert ctx.delivery.progress_message_ids == ["ordinary"]

    asyncio.run(run())


def test_edit_reservation_fences_before_await_and_success_always_owns_original_target():
    async def run():
        for result in (
            Result(True, "replacement-event"),
            Result(True, None),
            Result(False, "m0", "not modified"),
        ):
            adapter, checkpoints = BlockingEditAdapter(result), controller()
            ctx = make_live_context(adapter)
            prepare(ctx)
            delayed, deleted = (
                asyncio.create_task(asyncio.sleep(10)),
                asyncio.create_task(asyncio.sleep(10)),
            )
            ctx.delivery.delayed_flush_task, ctx.delivery.delete_task = delayed, deleted
            task = asyncio.create_task(
                checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
            )
            await adapter.started.wait()
            reservation = ctx.decision.pending_freeze
            assert checkpoints.is_waiting(ctx) and reservation.message_id == "m0"
            assert ctx.decision.cleanup_epoch == 1 and delayed.cancelling() and deleted.cancelling()
            assert ctx.delivery.message_id is None and ctx.delivery.message_started_at == 0.0
            assert (
                ctx.delivery.progress_message_ids == []
                and ctx.decision.protected_message_ids == {"m0"}
            )
            assert ctx.decision.sequence == 0 and ctx.decision.active_checkpoint is None
            adapter.release.set()
            assert await task
            checkpoint = ctx.decision.active_checkpoint
            assert checkpoint.message_id == "m0" and checkpoint.editable
            assert (
                ctx.delivery.progress_message_ids == []
                and ctx.decision.protected_message_ids == {"m0"}
            )
            assert (
                ctx.decision.sequence == 1 and not ctx.tool.lines and not ctx.assistant.latest_text
            )
            assert await checkpoints.resolve_locked(ctx, "resolved", "default")
            assert adapter.edits[-1][1] == "m0"

    asyncio.run(run())


def test_targetless_reservation_keeps_prior_ordinary_identity_through_blocking_send():
    async def run():
        adapter, checkpoints = BlockingSendAdapter(), controller()
        ctx = make_live_context(adapter, strategy="snapshot")
        prepare(ctx, "ordinary")
        task = asyncio.create_task(
            checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
        )
        await adapter.started.wait()
        assert checkpoints.is_waiting(ctx) and ctx.decision.pending_freeze.message_id == ""
        assert ctx.decision.cleanup_epoch == 1 and ctx.delivery.message_id == "ordinary"
        assert ctx.delivery.message_started_at == 8.0 and ctx.delivery.progress_message_ids == [
            "ordinary"
        ]
        assert ctx.decision.protected_message_ids == set() and ctx.decision.sequence == 0
        adapter.release.set()
        assert await task
        checkpoint = ctx.decision.active_checkpoint
        assert checkpoint.message_id == "checkpoint" and checkpoint.editable
        assert ctx.delivery.message_id is None and ctx.delivery.message_started_at == 0.0
        assert ctx.delivery.progress_message_ids == ["ordinary"]
        assert ctx.decision.protected_message_ids == {"checkpoint"}

    asyncio.run(run())


def test_targetless_no_message_and_noneditable_active_send_one_snapshot_without_claiming_old_target():
    async def run():
        for message_id, can_edit in (("", True), ("ordinary", False)):
            adapter, checkpoints = EditableAdapter(), controller()
            ctx = make_live_context(adapter)
            prepare(ctx, message_id)
            ctx.delivery.can_edit = can_edit
            assert await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
            assert len(adapter.sent) == 1 and not adapter.edits
            assert ctx.decision.active_checkpoint.message_id == "m1"
            assert ctx.delivery.message_id is None and ctx.delivery.message_started_at == 0.0
            assert ctx.delivery.progress_message_ids == (["ordinary"] if message_id else [])

    asyncio.run(run())


def test_targetless_send_rejects_empty_or_reachable_id_without_detaching_it():
    async def run():
        for fresh_id in (None, "ordinary", "historical"):
            adapter, checkpoints = (
                ResultsAdapter(send_results=[Result(True, fresh_id)]),
                controller(),
            )
            ctx = make_live_context(adapter, strategy="snapshot")
            prepare(ctx, "ordinary")
            ctx.delivery.progress_message_ids.append("historical")
            assert not await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
            assert len(adapter.sent) == 1 and ctx.decision.active_checkpoint is None
            assert ctx.decision.pending_freeze is None and ctx.decision.sequence == 0
            assert ctx.delivery.message_id == "ordinary" and ctx.delivery.message_started_at == 8.0
            assert ctx.delivery.progress_message_ids == ["ordinary", "historical"]
            assert ctx.decision.protected_message_ids == set()

    asyncio.run(run())


def test_fallback_requires_new_send_id_and_message_lost_failure_never_restores_stale_target():
    async def run():
        cases = (
            (
                Result(False, "m0", "edit not supported"),
                Result(False, None, "send failed"),
                "m0",
                ["m0"],
            ),
            (
                Result(False, "m0", "message to edit not found"),
                Result(False, None, "send failed"),
                None,
                [],
            ),
            (Result(False, "m0", "edit not supported"), Result(True, "m0"), "m0", ["m0"]),
            (Result(False, "m0", "message to edit not found"), Result(True, "m0"), None, []),
        )
        for edit_result, send_result, expected_id, expected_history in cases:
            adapter, checkpoints = (
                ResultsAdapter(edit_results=[edit_result], send_results=[send_result]),
                controller(),
            )
            ctx = make_live_context(adapter)
            prepare(ctx)
            assert not await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
            assert len(adapter.edits) == 1 and len(adapter.sent) == 1
            assert ctx.decision.sequence == 0 and ctx.decision.active_checkpoint is None
            assert (
                ctx.delivery.message_id == expected_id
                and ctx.delivery.progress_message_ids == expected_history
            )
            assert ctx.decision.pending_freeze is None and not ctx.decision.protected_message_ids

    asyncio.run(run())


def test_fallback_rejects_historical_send_id_with_existing_ownership_unchanged():
    async def run():
        cases = (
            ("edit not supported", "m0", ["m0", "historical"]),
            ("message to edit not found", None, ["historical"]),
        )
        for error, expected_id, expected_history in cases:
            adapter, checkpoints = (
                ResultsAdapter(
                    edit_results=[Result(False, "m0", error)],
                    send_results=[Result(True, "historical")],
                ),
                controller(),
            )
            ctx = make_live_context(adapter)
            prepare(ctx)
            ctx.delivery.progress_message_ids.append("historical")
            assert not await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
            assert len(adapter.edits) == 1 and len(adapter.sent) == 1
            assert ctx.decision.pending_freeze is None and ctx.decision.active_checkpoint is None
            assert ctx.decision.sequence == 0 and ctx.decision.protected_message_ids == set()
            assert ctx.delivery.message_id == expected_id and ctx.delivery.message_started_at == (
                8.0 if expected_id else 0.0
            )
            assert ctx.delivery.progress_message_ids == expected_history

    asyncio.run(run())


def test_successful_fallback_owns_only_fresh_checkpoint_and_handles_lost_target():
    async def run():
        cases = (
            ("edit not supported", "fresh-unsupported", False, ["older", "m0", "historical"]),
            ("message to edit not found", "fresh-lost", True, ["older", "historical"]),
        )
        for error, checkpoint_id, editable, expected_history in cases:
            adapter, checkpoints = (
                ResultsAdapter(
                    edit_results=[Result(False, "m0", error)],
                    send_results=[Result(True, checkpoint_id)],
                ),
                controller(),
            )
            ctx = make_live_context(adapter)
            prepare(ctx)
            ctx.delivery.progress_message_ids[:] = ["older", "m0", "historical"]
            assert await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
            checkpoint = ctx.decision.active_checkpoint
            assert checkpoint is not None and checkpoint.message_id == checkpoint_id
            assert checkpoint.editable is editable and checkpoint.status == "frozen"
            assert ctx.decision.pending_freeze is None and ctx.decision.sequence == 1
            assert ctx.decision.protected_message_ids == {checkpoint_id}
            assert ctx.delivery.message_id is None and ctx.delivery.message_started_at == 0.0
            assert ctx.delivery.progress_message_ids == expected_history

    asyncio.run(run())


def test_non_fallback_failures_roll_back_and_second_message_lost_does_not_send():
    async def run():
        for error in ("flood_control:5", "forbidden", "too long", "temporary network failure"):
            adapter, checkpoints = SequenceEditAdapter([error]), controller()
            ctx = make_live_context(adapter)
            prepare(ctx)
            assert not await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
            assert not adapter.sent and ctx.delivery.message_id == "m0"
            assert ctx.delivery.message_started_at == 8.0 and ctx.delivery.progress_message_ids == [
                "m0"
            ]
        adapter, checkpoints = SequenceEditAdapter(["message to edit not found"]), controller()
        ctx = make_live_context(adapter)
        ctx.delivery.edit_recovery_sends = 1
        prepare(ctx)
        assert not await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
        assert (
            not adapter.sent
            and ctx.delivery.message_id is None
            and ctx.delivery.progress_message_ids == []
        )

    asyncio.run(run())


def test_late_edit_result_remains_fenced_until_pending_cleanup_prunes_original_target():
    async def run():
        adapter, checkpoints = BlockingEditAdapter(Result(True, "replacement-event")), controller()
        ctx = make_live_context(adapter)
        prepare(ctx)
        cancelled = False
        task = asyncio.create_task(
            checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: cancelled)
        )
        await adapter.started.wait()
        cancelled = True
        adapter.release.set()
        assert not await task and ctx.decision.pending_freeze is not None
        assert ctx.delivery.message_id is None and ctx.delivery.progress_message_ids == []
        assert ctx.decision.protected_message_ids == {"m0"} and ctx.decision.sequence == 0
        assert await checkpoints.resolve_locked(ctx, "timed_out")
        assert ctx.decision.cleanup_epoch == 2 and ctx.decision.pending_freeze is None
        assert not ctx.decision.protected_message_ids and ctx.delivery.message_id is None
        await controller().delivery.render_live(ctx, force=True)
        assert adapter.sent and ctx.delivery.message_id == "m1"

    asyncio.run(run())


def test_late_targetless_send_never_claims_result_and_pending_cleanup_detaches_old_identity():
    async def run():
        adapter, checkpoints = BlockingSendAdapter(Result(True, "late-checkpoint")), controller()
        ctx = make_live_context(adapter, strategy="snapshot")
        prepare(ctx, "ordinary")
        cancelled = False
        task = asyncio.create_task(
            checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: cancelled)
        )
        await adapter.started.wait()
        assert ctx.delivery.message_id == "ordinary" and ctx.delivery.progress_message_ids == [
            "ordinary"
        ]
        cancelled = True
        adapter.release.set()
        assert not await task and ctx.decision.pending_freeze is not None
        assert await checkpoints.resolve_locked(ctx, "timed_out")
        assert ctx.decision.pending_freeze is None and ctx.delivery.message_id is None
        assert (
            ctx.delivery.progress_message_ids == ["ordinary"]
            and not ctx.decision.protected_message_ids
        )
        ctx.routing.strategy = "live_tail"
        adapter.result = Result(True, "fresh-progress")
        await controller().delivery.render_live(ctx, force=True)
        assert ctx.delivery.message_id == "fresh-progress"
        assert "late-checkpoint" not in ctx.delivery.progress_message_ids

    asyncio.run(run())


def test_lone_pending_timeout_and_duplicate_freeze_do_not_duplicate_network_or_checkpoint_state():
    async def run():
        adapter, checkpoints = BlockingSendAdapter(), controller()
        ctx = make_live_context(adapter, strategy="snapshot")
        task = asyncio.create_task(
            checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
        )
        await adapter.started.wait()
        assert not await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
        assert len(adapter.sent) == 1 and await checkpoints.resolve_locked(ctx, "timed_out")
        adapter.release.set()
        assert not await task and ctx.decision.active_checkpoint is None
        assert ctx.decision.sequence == 0 and not ctx.decision.protected_message_ids

    asyncio.run(run())


def test_resolution_has_no_fallback_and_terminal_non_answers_never_create_plan_records():
    async def run():
        for status in ("timed_out", "prompt_failed", "interrupted"):
            adapter, checkpoints = EditableAdapter(), controller()
            ctx = make_live_context(adapter)
            prepare(ctx)
            assert await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
            assert await checkpoints.resolve_locked(ctx, status, "invented")
            assert ctx.decision.active_checkpoint.answer == "" and not ctx.decision.records
        adapter, checkpoints = (
            ResultsAdapter(edit_results=[Result(True, "m0"), Result(False, "m0", "unsupported")]),
            controller(),
        )
        ctx = make_live_context(adapter)
        prepare(ctx)
        assert await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
        assert not await checkpoints.resolve_locked(ctx, "resolved", "default")
        assert len(adapter.sent) == 0 and len(adapter.edits) == 2

    asyncio.run(run())


def test_success_preserves_todo_background_and_protection_is_bounded_across_replacement_and_reset():
    async def run():
        adapter, checkpoints = EditableAdapter(), controller()
        ctx = make_live_context(adapter, strategy="snapshot")
        ctx.tool.todo_items = ("keep-todo",)
        ctx.background.jobs["job"] = object()
        assert await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
        assert ctx.tool.todo_items == ("keep-todo",) and ctx.background.jobs == {
            "job": ctx.background.jobs["job"]
        }
        assert ctx.decision.protected_message_ids == {"m1"}
        assert await checkpoints.resolve_locked(ctx, "resolved", "default")
        assert await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
        assert ctx.decision.sequence == 2 and ctx.decision.protected_message_ids == {"m2"}
        assert ctx.decision.active_checkpoint.message_id == "m2"
        ctx.decision.reset_turn()
        assert not ctx.decision.protected_message_ids

    asyncio.run(run())


def test_strategy_off_invalid_args_and_no_edit_snapshot_fail_open_or_stay_terminal_in_memory():
    async def run():
        adapter, checkpoints = EditableAdapter(), controller()
        ctx = make_live_context(adapter, strategy="off")
        assert not await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
        assert not await checkpoints.freeze_locked(
            ctx, {"question": " ", "choices": []}, is_cancelled=lambda: False
        )
        assert not adapter.sent and not adapter.edits
        no_edit, checkpoints = NoEditAdapter(), controller()
        ctx = make_live_context(no_edit, strategy="snapshot")
        assert await checkpoints.freeze_locked(ctx, args(), is_cancelled=lambda: False)
        assert not ctx.decision.active_checkpoint.editable
        assert await checkpoints.resolve_locked(ctx, "resolved", "default")
        assert len(no_edit.sent) == 1

    asyncio.run(run())
