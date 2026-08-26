"""End-to-end proof that the card resumes after a mid-turn adapter rebuild.

``tests/test_adapter_rebuild_recovery.py`` proves ``ctx.adapter`` resolves the
live object. That is the seam, not the symptom. The reported failure was
"the Telegram card is frozen while the agent keeps working", so the evidence
has to run through ``RendererDelivery.render_live(...)`` — the same path that
produced the ``telegram.py:167`` edits which stopped at 09:13:36 — and assert
on the content the **new** adapter actually recorded.
"""

from __future__ import annotations

import asyncio

from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.settings.loading import load_settings
from tests.support.rendering import Result, make_live_context

PLATFORM = "telegram"


class RecordingAdapter:
    """A live-ish adapter that records every edit it is asked to perform.

    ``dead`` mimics the rebuilt-away Telegram adapter: it still accepts calls
    and still records them (that is how we prove the edit went to the wrong
    object) but answers with a network-shaped failure, which
    ``_classify_edit_error`` maps to ``transient``.
    """

    def __init__(self, name: str):
        self.name = name
        self.edits: list[tuple[str, str, str]] = []
        self.sent: list[tuple[str, str, object]] = []
        self.dead = False

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        if self.dead:
            return Result(False, message_id, "Timed out")
        return Result(True, message_id)

    async def send(self, chat_id, content, metadata=None):
        self.sent.append((chat_id, content, metadata))
        return Result(True, "m1")

    @property
    def contents(self) -> list[str]:
        return [content for _chat, _mid, content in self.edits]


class FakeGateway:
    def __init__(self, adapters):
        self.adapters = adapters


def renderer() -> ProgressRenderer:
    return ProgressRenderer(load_settings({"progress_tail": {}}))


def make_established_context(adapter, *, gateway=None):
    """A context whose live message already exists, so the edit branch is taken."""

    ctx = make_live_context(adapter, platform=PLATFORM)
    ctx.delivery.message_id = "m1"
    ctx.delivery.can_edit = True
    ctx.tool.lines.append("work")
    if gateway is not None:
        ctx.attach_gateway(gateway)
    return ctx


def test_render_live_resumes_on_the_rebuilt_adapter():
    """After ``gateway.adapters[platform]`` is replaced, the card edits land on B."""

    async def run():
        old = RecordingAdapter("old")
        new = RecordingAdapter("new")
        adapters = {PLATFORM: old}
        delivery = renderer().delivery
        ctx = make_established_context(old, gateway=FakeGateway(adapters))

        await delivery.render_live(ctx, force=True)
        assert old.contents, "the first edit must reach the originally live adapter"
        assert new.edits == []
        edits_before_rebuild = len(old.edits)

        # Hermes core rebuilds the adapter mid-turn; the old object is stopped.
        old.dead = True
        adapters[PLATFORM] = new
        ctx.tool.lines.append("more work")

        await delivery.render_live(ctx, force=True, ignore_backoff=True)

        assert new.contents, "the card must resume on the rebuilt adapter"
        assert "more work" in new.contents[-1]
        assert len(old.edits) == edits_before_rebuild, "no edit may reach the dead adapter"
        assert ctx.delivery.edit_state == "editable"
        assert ctx.delivery.edit_backoff_until == 0.0
        assert ctx.delivery.edit_failure_count == 0

    asyncio.run(run())


def test_without_a_gateway_handle_the_card_stays_stuck_on_the_dead_adapter():
    """Negative control: this is the reported bug, pinned."""

    async def run():
        old = RecordingAdapter("old")
        new = RecordingAdapter("new")
        adapters = {PLATFORM: old}
        delivery = renderer().delivery
        ctx = make_established_context(old)  # no gateway attached

        await delivery.render_live(ctx, force=True)
        assert old.contents
        edits_before_rebuild = len(old.edits)

        old.dead = True
        adapters[PLATFORM] = new
        ctx.tool.lines.append("more work")

        await delivery.render_live(ctx, force=True, ignore_backoff=True)

        assert new.edits == [], "without the gateway handle the rebuild is invisible"
        assert len(old.edits) == edits_before_rebuild + 1, "the edit still hits the dead adapter"
        assert ctx.diagnostics.last_error == "Timed out"
        assert ctx.delivery.edit_state == "transient"
        assert ctx.delivery.edit_failure_count == 1

    asyncio.run(run())


def test_without_a_rebuild_delivery_behaviour_is_unchanged():
    """The fix is inert when nothing was rebuilt: same adapter, same state."""

    async def run():
        live = RecordingAdapter("live")
        untouched = RecordingAdapter("untouched")
        delivery = renderer().delivery
        ctx = make_established_context(live, gateway=FakeGateway({PLATFORM: live}))

        await delivery.render_live(ctx, force=True)
        ctx.tool.lines.append("more work")
        await delivery.render_live(ctx, force=True)

        assert len(live.edits) == 2
        assert untouched.edits == []
        assert live.sent == [], "the edit branch must be taken, never the send branch"
        assert [mid for _chat, mid, _content in live.edits] == ["m1", "m1"]
        assert ctx.delivery.message_id == "m1"
        assert ctx.delivery.progress_message_ids == ["m1"]
        assert ctx.delivery.can_edit is True
        assert ctx.delivery.edit_state == "editable"
        assert ctx.delivery.edit_backoff_until == 0.0
        assert ctx.delivery.edit_failure_count == 0
        assert ctx.delivery.edit_recovery_sends == 0
        assert ctx.delivery.fallback_send_count == 0
        assert ctx.delivery.delayed_flush_task is None
        assert ctx.diagnostics.last_error == ""

    asyncio.run(run())
