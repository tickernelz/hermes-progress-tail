import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import BackgroundJobEvent, ReasoningEvent, SessionContext, ToolEvent


class Result:
    def __init__(self, success=True, message_id=None, error=""):
        self.success = success
        self.message_id = message_id
        self.error = error


class EditableAdapter:
    name = "editable"

    def __init__(self):
        self.sent = []
        self.edits = []
        self.deleted = []
        self.next_id = 1
        self.delete_event = None

    async def send(self, chat_id, content, metadata=None):
        message_id = f"m{self.next_id}"
        self.next_id += 1
        self.sent.append((chat_id, content, metadata))
        return Result(True, message_id)

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return Result(True, message_id)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        if self.delete_event is not None:
            self.delete_event.set()
        return True


def make_ctx(
    adapter,
    *,
    session_id="s1",
    session_key="k1",
    platform="telegram",
    source_message_id=None,
):
    return SessionContext(
        session_id,
        session_key,
        platform,
        "chat",
        None,
        adapter,
        asyncio.get_running_loop(),
        "live_tail",
        timestamp=False,
        source_message_id=source_message_id,
    )


def make_renderer(config=None):
    return ProgressRenderer(
        load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False},
                    **(config or {}),
                }
            }
        )
    )


def test_finalize_keeps_completed_progress_bubble_visible_until_cleanup_delay():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "tool one"), force=True)
        await renderer.finalize(session_id="s1", success=True)

        assert adapter.sent == [("chat", "▰ 🧰 Tools\ntool one", None)]
        assert adapter.deleted == []
        assert ctx.message_id == "m1"
        assert ctx.progress_state == "finalized"
        assert list(ctx.tool_lines) == []

    asyncio.run(run())


def test_next_turn_after_final_answer_gets_new_progress_bubble():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "first turn"), force=True)
        await renderer.finalize(session_id="s1", success=True)

        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "second turn"), force=True)

        assert len(adapter.sent) == 2
        assert adapter.sent[0][1] == "▰ 🧰 Tools\nfirst turn"
        assert adapter.sent[1][1] == "▰ 🧰 Tools\nsecond turn"
        assert adapter.edits == [("chat", "m1", "▰ 🧰 Tools\nfirst turn")]
        assert next_ctx.message_id == "m2"

    asyncio.run(run())


def test_active_turn_interrupt_reuses_existing_progress_bubble():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "first event"), force=True)
        replacement_ctx = make_ctx(adapter)
        renderer.register_context(replacement_ctx)
        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "interrupt event"), force=True
        )

        assert len(adapter.sent) == 1
        assert adapter.edits[-1] == ("chat", "m1", "▰ 🧰 Tools\nfirst event\ninterrupt event")
        assert replacement_ctx.message_id == "m1"

    asyncio.run(run())


def test_new_source_message_does_not_reuse_stale_active_progress_bubble():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter, source_message_id="msg-1")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "first turn"), force=True)
        assert ctx.message_id == "m1"
        assert ctx.progress_state == "active"

        next_ctx = make_ctx(adapter, source_message_id="msg-2")
        renderer.register_context(next_ctx)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "second turn"), force=True)

        assert len(adapter.sent) == 2
        assert adapter.sent[0][1] == "▰ 🧰 Tools\nfirst turn"
        assert adapter.sent[1][1] == "▰ 🧰 Tools\nsecond turn"
        assert next_ctx.message_id == "m2"

    asyncio.run(run())


def test_late_events_after_finalize_do_not_edit_retired_bubble():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "old turn"), force=True)
        await renderer.finalize(session_id="s1", success=True)
        previous_edit_count = len(adapter.edits)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "late event"), force=True)

        assert len(adapter.sent) == 1
        assert len(adapter.edits) == previous_edit_count
        assert list(ctx.tool_lines) == []

    asyncio.run(run())


def test_finalize_keeps_background_job_bubble_active_but_new_turn_still_gets_new_bubble():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer(
            {
                "background_jobs": {
                    "head_lines": 1,
                    "tail_lines": 1,
                }
            }
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "normal tool"), force=True)
        await renderer.handle_event(
            BackgroundJobEvent(
                "s1",
                "k1",
                "telegram",
                "proc_1",
                event_type="started",
                command="pytest -q",
            ),
            force=True,
        )
        await renderer.finalize(session_id="s1", success=True)

        assert ctx.message_id == "m1"
        assert ctx.progress_state == "background_active"
        assert "normal tool" not in adapter.edits[-1][2]
        assert "proc_1" in adapter.edits[-1][2]

        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "new turn"), force=True)

        assert len(adapter.sent) == 2
        assert (
            adapter.sent[1][1]
            == "▰ 🖥 Background Jobs\n[1] 🔄 proc_1 · pytest -q · 0s\n\n▰ 🧰 Tools\nnew turn"
        )
        assert next_ctx.message_id == "m2"

    asyncio.run(run())


def test_stale_background_finalize_does_not_hide_new_foreground_progress():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        bg_ctx = make_ctx(adapter)
        renderer.register_context(bg_ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "bg-review tool"), force=True)
        fg_ctx = make_ctx(adapter)
        renderer.register_context(fg_ctx)
        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "foreground tool"), force=True
        )
        await renderer.finalize(session_id="s1", generation=bg_ctx.generation, success=True)
        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "foreground second tool"), force=True
        )

        assert fg_ctx.progress_state == "active"
        assert len(adapter.sent) == 1
        assert adapter.edits[-1] == (
            "chat",
            "m1",
            "▰ 🧰 Tools\nbg-review tool\nforeground tool\nforeground second tool",
        )
        assert renderer.sessions["s1"].generation > bg_ctx.generation

    asyncio.run(run())


def test_stale_generation_finalize_does_not_purge_replaced_context_after_lock_wait():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        old_ctx = make_ctx(adapter)
        renderer.register_context(old_ctx)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "old turn"), force=True)

        await old_ctx.lock.acquire()
        finalize_task = asyncio.create_task(
            renderer.finalize(session_id="s1", generation=old_ctx.generation, purge=True)
        )
        await asyncio.sleep(0)
        new_ctx = make_ctx(adapter)
        renderer.register_context(new_ctx)
        old_ctx.lock.release()
        await finalize_task
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "new turn"), force=True)

        assert renderer.sessions["s1"] is new_ctx
        assert new_ctx.progress_state == "active"
        assert "new turn" in adapter.edits[-1][2]

    asyncio.run(run())


def test_current_generation_finalize_still_finishes_active_context():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "foreground tool"), force=True
        )
        await renderer.finalize(session_id="s1", generation=ctx.generation, success=True)

        assert ctx.progress_state == "finalized"
        assert ctx.message_id == "m1"

    asyncio.run(run())


def test_config_ignores_removed_finalization_section():
    settings = load_settings(
        {
            "progress_tail": {
                "finalization": {
                    "policy": "delete",
                    "delete_on_success": True,
                    "delay_seconds": 0,
                }
            }
        }
    )

    assert not hasattr(settings, "finalization")


def test_reasoning_after_finalize_starts_fresh_bubble():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer({"reasoning": {"min_update_chars": 1}})
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ReasoningEvent("s1", "k1", "telegram", "old thought"), force=True
        )
        await renderer.finalize(session_id="s1", success=True)

        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await renderer.handle_event(
            ReasoningEvent("s1", "k1", "telegram", "new thought"), force=True
        )

        assert len(adapter.sent) == 2
        assert adapter.sent[0][1] == "▰ 💭 Reasoning\nold thought"
        assert adapter.sent[1][1] == "▰ 💭 Reasoning\nnew thought"
        assert next_ctx.message_id == "m2"

    asyncio.run(run())


def test_finalize_auto_deletes_completed_progress_bubble_when_enabled():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer({"cleanup": {"auto_delete": True, "delay_seconds": 1}})
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "tool one"), force=True)
        await renderer.finalize(session_id="s1", success=True)
        assert adapter.deleted == []

        await asyncio.sleep(0)
        assert adapter.deleted == []

        await asyncio.sleep(1.01)
        assert adapter.deleted == [("chat", "m1")]
        assert ctx.message_id is None
        assert ctx.progress_state == "deleted"

    asyncio.run(run())


def test_finalize_purge_keeps_auto_delete_task_alive():
    async def run():
        adapter = EditableAdapter()
        adapter.delete_event = asyncio.Event()
        renderer = make_renderer({"cleanup": {"auto_delete": True, "delay_seconds": 1}})
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "tool one"), force=True)
        await renderer.finalize(session_id="s1", success=True, purge=True)

        assert renderer.find_context("s1") is None
        await asyncio.wait_for(adapter.delete_event.wait(), timeout=1.2)
        assert adapter.deleted == [("chat", "m1")]

    asyncio.run(run())


def test_finalize_does_not_auto_delete_failure_by_default():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer({"cleanup": {"auto_delete": True, "delay_seconds": 1}})
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "tool one"), force=True)
        await renderer.finalize(session_id="s1", success=False)
        await asyncio.sleep(1.01)

        assert adapter.deleted == []
        assert ctx.message_id == "m1"
        assert ctx.progress_state == "finalized"

    asyncio.run(run())
