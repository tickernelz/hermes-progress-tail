from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

DecisionKind = Literal["assistant", "tool", "plan", "warning"]
CheckpointStatus = Literal["frozen", "resolved", "timed_out", "interrupted", "prompt_failed"]


@dataclass(frozen=True)
class DecisionRecord:
    kind: DecisionKind
    text: str
    identity: str
    priority: int
    created_at: float


@dataclass
class ClarifyCheckpoint:
    sequence: int
    message_id: str
    question: str
    choices: tuple[str, ...]
    status: CheckpointStatus
    editable: bool
    content: str
    answer: str = ""


@dataclass
class FreezeReservation:
    generation: int
    message_id: str
    message_started_at: float
    history_index: int | None


@dataclass
class DecisionState:
    records: deque[DecisionRecord] = field(default_factory=lambda: deque(maxlen=24))
    active_checkpoint: ClarifyCheckpoint | None = None
    pending_freeze: FreezeReservation | None = None
    protected_message_ids: set[str] = field(default_factory=set)
    cleanup_epoch: int = 0
    sequence: int = 0

    def clear_records_for_new_segment(self) -> None:
        """Discard only the archive captured by a successful checkpoint freeze."""
        self.records.clear()

    def reconcile_protected_message_ids(self) -> None:
        """Retain only non-empty checkpoint IDs still owned by this decision state."""
        ids = set()
        if self.pending_freeze and self.pending_freeze.message_id:
            ids.add(self.pending_freeze.message_id)
        if self.active_checkpoint and self.active_checkpoint.message_id:
            ids.add(self.active_checkpoint.message_id)
        self.protected_message_ids = ids

    def reset_turn(self) -> None:
        """Clear decision state after callers have fenced cleanup and detached delivery IDs."""
        self.records.clear()
        self.pending_freeze = None
        self.active_checkpoint = None
        self.protected_message_ids.clear()
        self.sequence = 0


def decision_is_waiting(state: DecisionState) -> bool:
    """Return whether checkpoint delivery suppresses ordinary live progress."""
    return state.pending_freeze is not None or (
        state.active_checkpoint is not None and state.active_checkpoint.status == "frozen"
    )
