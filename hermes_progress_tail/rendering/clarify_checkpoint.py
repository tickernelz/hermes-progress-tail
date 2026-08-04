from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..gateway.compat import adapter_supports_edit
from ..models.decision import (
    CheckpointStatus,
    ClarifyCheckpoint,
    DecisionRecord,
    FreezeReservation,
    decision_is_waiting,
)
from ..models.state import SessionContext
from ..utils.redaction import redact_text
from .decision_archive import DecisionArchive
from .delivery import edit_content, send_content

_MAX_RECORDS = 12
_SOURCE_BUDGET = 2800
_ANSWER_BUDGET = 240
_TERMINAL_RESERVE = 300
_QUESTION_BUDGET = 620
_CHOICE_BUDGET = 320
_ELLIPSIS = "…"


@dataclass(frozen=True)
class NormalizedClarify:
    question: str
    choices: tuple[str, ...]


def _preview(value: Any, limit: int) -> str:
    text = " ".join(redact_text(str(value)).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + _ELLIPSIS


def _truncate_source(value: str, limit: int) -> str:
    """Keep the leading checkpoint context; terminal composition owns its suffix."""
    if len(value) <= limit:
        return value
    if limit <= 0:
        return ""
    if limit == 1:
        return _ELLIPSIS
    return value[: limit - 1].rstrip() + _ELLIPSIS


def _flatten_choice(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("label", "description", "text", "title"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return _preview(candidate, _CHOICE_BUDGET)
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(part for item in value if (part := _flatten_choice(item))).strip()
    if isinstance(value, (str, int, float, bool)):
        return _preview(value, _CHOICE_BUDGET)
    return ""


def canonicalize_clarify_args(args: Mapping[str, Any] | Any) -> NormalizedClarify | None:
    if not isinstance(args, Mapping) or not isinstance(args.get("question"), str):
        return None
    question = _preview(args["question"], _QUESTION_BUDGET)
    choices = [] if "choices" not in args else args.get("choices")
    if not question or not isinstance(choices, list):
        return None
    flattened = tuple(part for item in choices if (part := _flatten_choice(item)))
    return NormalizedClarify(question, flattened[:4])


def _records(ctx: Any, budget: int) -> tuple[DecisionRecord, ...]:
    candidates = sorted(
        ctx.decision.records, key=lambda item: (item.priority, item.created_at), reverse=True
    )
    selected, used = [], 0
    for item in candidates:
        text = _preview(item.text, 360)
        if len(selected) == _MAX_RECORDS or used + len(text) > budget:
            continue
        selected.append(
            DecisionRecord(item.kind, text, item.identity, item.priority, item.created_at)
        )
        used += len(text)
    return tuple(sorted(selected, key=lambda item: item.created_at))


def _sections(records: tuple[DecisionRecord, ...]) -> list[str]:
    groups = (
        ("Context so far", {"assistant", "warning"}),
        ("Plan", {"plan"}),
        ("Relevant evidence", {"tool"}),
    )
    return [
        title + "\n" + "\n".join("• " + item.text for item in records if item.kind in kinds)
        for title, kinds in groups
        if any(item.kind in kinds for item in records)
    ]


def _fit_checkpoint(ctx: Any, normalized: NormalizedClarify, sequence: int) -> str:
    label = _preview(getattr(ctx.routing, "agent_label", "") or "Agent", 80)
    choice_lines = [f"• {index}. {choice}" for index, choice in enumerate(normalized.choices, 1)]
    fixed = [
        f"{label} needs your input",
        f"Checkpoint #{sequence} · this progress message is now read-only",
        "FROZEN · waiting for your answer",
        "Decision\n" + normalized.question,
    ]
    if choice_lines:
        fixed.append("Choices\n" + "\n".join(choice_lines))
    base = "\n\n".join(fixed)
    records = _records(ctx, max(0, _SOURCE_BUDGET - _TERMINAL_RESERVE - len(base) - 6))
    return "\n\n".join([base, *_sections(records)])


def compose_checkpoint(ctx: Any, args: Mapping[str, Any] | Any, sequence: int) -> str | None:
    normalized = canonicalize_clarify_args(args)
    if normalized is None:
        return None
    content = redact_text(_fit_checkpoint(ctx, normalized, sequence))
    return _truncate_source(content, _SOURCE_BUDGET)


def compose_resolution(content: str, status: str, answer: str = "") -> str:
    lines = {
        "resolved": "RESOLVED · " + (_preview(answer, _ANSWER_BUDGET) or "no answer received"),
        "timed_out": "TIMED OUT · no answer received",
        "interrupted": "INTERRUPTED · task stopped while waiting",
        "prompt_failed": "PROMPT FAILED · clarify prompt was not delivered",
    }
    terminal = redact_text(lines.get(status, lines["prompt_failed"]))
    frozen = redact_text(
        content.replace("FROZEN · waiting for your answer", "", 1).replace("\n\n\n\n", "\n\n")
    ).rstrip()
    separator = "\n\n" if frozen else ""
    source_limit = max(0, _SOURCE_BUDGET - len(separator) - len(terminal))
    frozen = _truncate_source(frozen, source_limit)
    resolved = redact_text(frozen + separator + terminal)
    while len(resolved) > _SOURCE_BUDGET and frozen:
        frozen = _truncate_source(frozen, max(0, len(frozen) - (len(resolved) - _SOURCE_BUDGET)))
        separator = "\n\n" if frozen else ""
        resolved = redact_text(frozen + separator + terminal)
    return resolved


class ClarifyCheckpointController:
    """Own the reversible freeze fence and one-shot checkpoint lifecycle."""

    def __init__(self, delivery: Any):
        self.delivery = delivery
        self.archive = DecisionArchive()

    def observe_progress(self, ctx: SessionContext, event: Any) -> None:
        if getattr(event, "kind", "") == "assistant":
            self.archive.observe_assistant(ctx.decision, event.text, event.created_at)

    def observe_record(self, ctx: SessionContext, record: DecisionRecord) -> None:
        self.archive.upsert(ctx.decision, record)

    def is_waiting(self, ctx: SessionContext) -> bool:
        return decision_is_waiting(ctx.decision)

    def _cancel_work(self, ctx: SessionContext) -> None:
        ctx.decision.cleanup_epoch += 1
        self.delivery.cancel_delayed_flush(ctx)
        self.delivery.cancel_delete(ctx)

    @staticmethod
    def _remove_history(ctx: SessionContext, message_id: str) -> int | None:
        try:
            index = ctx.delivery.progress_message_ids.index(message_id)
        except ValueError:
            return None
        ctx.delivery.progress_message_ids.pop(index)
        return index

    @staticmethod
    def _reachable_message_ids(ctx: SessionContext) -> set[str]:
        """Snapshot all current owners before a checkpoint delivery attempt."""
        ids = {
            ctx.delivery.message_id,
            *(ctx.delivery.progress_message_ids),
            *(ctx.decision.protected_message_ids),
        }
        if ctx.decision.pending_freeze is not None:
            ids.add(ctx.decision.pending_freeze.message_id)
        if ctx.decision.active_checkpoint is not None:
            ids.add(ctx.decision.active_checkpoint.message_id)
        return {str(message_id) for message_id in ids if message_id}

    def _reserve(self, ctx: SessionContext, *, editable_target: bool) -> FreezeReservation:
        message_id = str(ctx.delivery.message_id or "") if editable_target else ""
        try:
            history_index = ctx.delivery.progress_message_ids.index(message_id)
        except ValueError:
            history_index = None
        self._cancel_work(ctx)
        reservation = FreezeReservation(
            ctx.generation,
            message_id,
            ctx.delivery.message_started_at,
            self._remove_history(ctx, message_id) if editable_target else history_index,
        )
        ctx.decision.pending_freeze = reservation
        if editable_target:
            ctx.delivery.message_id = None
            ctx.delivery.message_started_at = 0.0
        ctx.decision.reconcile_protected_message_ids()
        return reservation

    @staticmethod
    def _owned(ctx: SessionContext, reservation: FreezeReservation) -> bool:
        return (
            ctx.generation == reservation.generation and ctx.decision.pending_freeze is reservation
        )

    def _rollback(self, ctx: SessionContext, reservation: FreezeReservation, *, lost: bool) -> None:
        if not self._owned(ctx, reservation):
            return
        if reservation.message_id and not lost:
            ctx.delivery.message_id = reservation.message_id
            ctx.delivery.message_started_at = reservation.message_started_at
            if reservation.message_id not in ctx.delivery.progress_message_ids:
                index = reservation.history_index
                ctx.delivery.progress_message_ids.insert(
                    len(ctx.delivery.progress_message_ids) if index is None else index,
                    reservation.message_id,
                )
        ctx.decision.pending_freeze = None
        ctx.decision.reconcile_protected_message_ids()

    @staticmethod
    def _clear_live_segment(ctx: SessionContext) -> None:
        ctx.tool.lines.clear()
        ctx.tool.active_lines.clear()
        ctx.assistant.lines.clear()
        ctx.assistant.latest_text = ""
        ctx.assistant.pending_chars = 0
        ctx.reasoning.text = ""
        ctx.reasoning.pending_chars = 0
        ctx.delegate.branches.clear()
        ctx.delegate.order.clear()

    def _commit(
        self,
        ctx: SessionContext,
        reservation: FreezeReservation,
        normalized: NormalizedClarify,
        content: str,
        message_id: str,
        editable: bool,
        *,
        retain_old_target: bool = False,
    ) -> bool:
        if not self._owned(ctx, reservation):
            return False
        if retain_old_target and reservation.message_id:
            index = reservation.history_index
            history = ctx.delivery.progress_message_ids
            if reservation.message_id not in history:
                history.insert(len(history) if index is None else index, reservation.message_id)
        ctx.decision.sequence += 1
        ctx.decision.active_checkpoint = ClarifyCheckpoint(
            ctx.decision.sequence,
            message_id,
            normalized.question,
            normalized.choices,
            "frozen",
            editable,
            content,
        )
        ctx.decision.pending_freeze = None
        ctx.decision.reconcile_protected_message_ids()
        ctx.delivery.message_id = None
        ctx.delivery.message_started_at = 0.0
        ctx.decision.clear_records_for_new_segment()
        self._clear_live_segment(ctx)
        return True

    async def freeze_locked(
        self, ctx: SessionContext, args: Mapping[str, Any], *, is_cancelled: Callable[[], bool]
    ) -> bool:
        normalized = canonicalize_clarify_args(args)
        if (
            normalized is None
            or ctx.delivery.disabled
            or ctx.routing.strategy == "off"
            or self.is_waiting(ctx)
            or is_cancelled()
        ):
            return False
        content = compose_checkpoint(ctx, args, ctx.decision.sequence + 1)
        if content is None:
            return False
        target = str(ctx.delivery.message_id or "")
        editable_target = bool(
            target and ctx.delivery.can_edit and ctx.routing.strategy == "live_tail"
        )
        reachable_ids = self._reachable_message_ids(ctx)
        reservation = self._reserve(ctx, editable_target=editable_target)
        result = await (
            edit_content(ctx, target, content) if editable_target else send_content(ctx, content)
        )
        if is_cancelled() or not self._owned(ctx, reservation):
            return False
        success = bool(getattr(result, "success", False))
        new_id = str(getattr(result, "message_id", "") or "")
        error = str(getattr(result, "error", "") or "checkpoint delivery failed")
        kind = self.delivery.classify_edit_error(error)
        initial_kind = kind
        if editable_target and kind == "noop_success":
            success = True
        used_fallback = False
        if (
            editable_target
            and not success
            and (
                kind == "unsupported"
                or (kind == "message_lost" and ctx.delivery.edit_recovery_sends == 0)
            )
            and not is_cancelled()
        ):
            used_fallback = True
            result = await send_content(ctx, content)
            if is_cancelled() or not self._owned(ctx, reservation):
                return False
            success = bool(getattr(result, "success", False))
            new_id = str(getattr(result, "message_id", "") or "")
        valid = success
        if editable_target:
            valid = valid and (not used_fallback or bool(new_id and new_id not in reachable_ids))
        else:
            valid = valid and bool(new_id and new_id not in reachable_ids)
        if not valid:
            self._rollback(
                ctx, reservation, lost=editable_target and initial_kind == "message_lost"
            )
            return False
        checkpoint_id = target if editable_target and not used_fallback else new_id
        editable = editable_target and not used_fallback
        if not editable:
            editable = adapter_supports_edit(ctx.adapter) and initial_kind != "unsupported"
        return self._commit(
            ctx,
            reservation,
            normalized,
            content,
            checkpoint_id,
            editable,
            retain_old_target=used_fallback and initial_kind != "message_lost",
        )

    async def resolve_locked(
        self, ctx: SessionContext, status: CheckpointStatus, answer: str = ""
    ) -> bool:
        checkpoint = ctx.decision.active_checkpoint
        if checkpoint is None:
            if ctx.decision.pending_freeze is None:
                return False
            self._cancel_work(ctx)
            ctx.decision.pending_freeze = None
            ctx.delivery.message_id = None
            ctx.delivery.message_started_at = 0.0
            ctx.decision.reconcile_protected_message_ids()
            return True
        if checkpoint.status != "frozen":
            return False
        checkpoint.status = status
        checkpoint.answer = _preview(answer, _ANSWER_BUDGET) if status == "resolved" else ""
        if checkpoint.answer:
            self.archive.upsert(
                ctx.decision,
                DecisionRecord(
                    "plan",
                    _preview(f"{checkpoint.question}: {checkpoint.answer}", 360),
                    f"checkpoint:{checkpoint.sequence}",
                    20,
                    time.monotonic(),
                ),
            )
        if checkpoint.editable and checkpoint.message_id:
            result = await edit_content(
                ctx,
                checkpoint.message_id,
                compose_resolution(checkpoint.content, status, checkpoint.answer),
            )
            return bool(getattr(result, "success", False)) or (
                self.delivery.classify_edit_error(getattr(result, "error", "")) == "noop_success"
            )
        return True

    async def interrupt_locked(self, ctx: SessionContext) -> bool:
        return await self.resolve_locked(ctx, "interrupted")
