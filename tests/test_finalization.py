import asyncio

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import BackgroundJobEvent, SessionContext, ToolEvent


class Result:
    def __init__(self, success=True, message_id=None, error=""):
        self.success = success
        self.message_id = message_id
        self.error = error


class DeletableAdapter:
    name = "deletable"

    def __init__(self, *, delete_success=True):
        self.sent = []
        self.edits = []
        self.deleted = []
        self.delete_success = delete_success
        self.next_id = 1

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
        return self.delete_success


class DelayedDeleteAdapter(DeletableAdapter):
    def __init__(self, *, delete_success=True, release: asyncio.Event | None = None):
        super().__init__(delete_success=delete_success)
        self.release = release

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        if self.release is not None:
            await self.release.wait()
        return self.delete_success


def make_ctx(adapter, *, session_id="s1", session_key="k1", platform="telegram"):
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
        code_fence="off",
    )


def make_renderer(config=None):
    return ProgressRenderer(
        load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False},
                    "renderer": {"code_fence": "off"},
                    **(config or {}),
                }
            }
        )
    )


def test_live_tail_progress_message_is_deleted_on_successful_finalize():
    async def run():
        adapter = DeletableAdapter()
        renderer = make_renderer({"finalization": {"policy": "delete", "delay_seconds": 0}})
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "tool one"), force=True)
        assert ctx.message_id == "m1"

        await renderer.finalize(session_id="s1", success=True)

        assert adapter.deleted == [("chat", "m1")]
        assert ctx.message_id is None
        assert ctx.stale_message_id == ""
        assert ctx.progress_state == "deleted"
        assert list(ctx.tool_lines) == []

    asyncio.run(run())


def test_next_turn_does_not_reuse_deleted_progress_message():
    async def run():
        adapter = DeletableAdapter()
        renderer = make_renderer({"finalization": {"policy": "delete", "delay_seconds": 0}})
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "first turn"), force=True)
        await renderer.finalize(session_id="s1", success=True)

        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "second turn"), force=True)

        assert len(adapter.sent) == 2
        assert adapter.sent[1][1].endswith("second turn")
        assert adapter.edits == []
        assert next_ctx.message_id == "m2"

    asyncio.run(run())


def test_finalize_keeps_progress_message_when_background_job_is_running():
    async def run():
        adapter = DeletableAdapter()
        renderer = make_renderer({"finalization": {"policy": "delete", "delay_seconds": 0}})
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

        assert adapter.deleted == []
        assert ctx.message_id == "m1"
        assert ctx.progress_state == "active"
        assert list(ctx.tool_lines) == []
        assert "normal tool" not in adapter.edits[-1][2]
        assert "proc_1" in adapter.edits[-1][2]

    asyncio.run(run())


def test_delete_failure_keeps_stale_id_for_next_turn_cleanup_without_reuse():
    async def run():
        adapter = DeletableAdapter(delete_success=False)
        renderer = make_renderer({"finalization": {"policy": "delete", "delay_seconds": 0}})
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "old turn"), force=True)
        await renderer.finalize(session_id="s1", success=True)

        assert adapter.deleted == [("chat", "m1")]
        assert ctx.message_id is None
        assert ctx.stale_message_id == "m1"
        assert ctx.progress_state == "finalized"
        assert "delete" in ctx.delete_failed_reason.lower()

        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await asyncio.sleep(0)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "new turn"), force=True)

        assert adapter.deleted == [("chat", "m1"), ("chat", "m1")]
        assert len(adapter.sent) == 2
        assert adapter.edits == []
        assert next_ctx.message_id == "m2"

    asyncio.run(run())


def test_successful_stale_cleanup_clears_current_context_stale_id():
    async def run():
        adapter = DeletableAdapter()
        renderer = make_renderer({"finalization": {"policy": "delete", "delay_seconds": 0}})
        old_ctx = make_ctx(adapter)
        old_ctx.progress_state = "finalized"
        old_ctx.stale_message_id = "m-old"
        renderer.sessions["s1"] = old_ctx
        renderer.session_keys["k1"] = "s1"

        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await asyncio.sleep(0)

        assert adapter.deleted == [("chat", "m-old")]
        assert next_ctx.stale_message_id == ""
        assert next_ctx.delete_failed_reason == ""

    asyncio.run(run())


def test_stale_cleanup_success_does_not_clear_fresh_message_id():
    async def run():
        release = asyncio.Event()
        adapter = DelayedDeleteAdapter(delete_success=True, release=release)
        renderer = make_renderer({"finalization": {"policy": "delete", "delay_seconds": 0}})
        old_ctx = make_ctx(adapter)
        old_ctx.progress_state = "finalized"
        old_ctx.stale_message_id = "m-old"
        renderer.sessions["s1"] = old_ctx
        renderer.session_keys["k1"] = "s1"

        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await asyncio.sleep(0)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "new turn"), force=True)
        assert next_ctx.message_id == "m1"

        release.set()
        await asyncio.sleep(0)

        assert adapter.deleted == [("chat", "m-old")]
        assert next_ctx.message_id == "m1"
        assert next_ctx.stale_message_id == ""
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "second event"), force=True)
        assert len(adapter.sent) == 1
        assert adapter.edits[-1][1] == "m1"

    asyncio.run(run())


def test_stale_cleanup_failure_does_not_clear_fresh_message_id():
    async def run():
        release = asyncio.Event()
        adapter = DelayedDeleteAdapter(delete_success=False, release=release)
        renderer = make_renderer({"finalization": {"policy": "delete", "delay_seconds": 0}})
        old_ctx = make_ctx(adapter)
        old_ctx.progress_state = "finalized"
        old_ctx.stale_message_id = "m-old"
        renderer.sessions["s1"] = old_ctx
        renderer.session_keys["k1"] = "s1"

        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await asyncio.sleep(0)
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "new turn"), force=True)
        assert next_ctx.message_id == "m1"

        release.set()
        await asyncio.sleep(0)

        assert adapter.deleted == [("chat", "m-old")]
        assert next_ctx.message_id == "m1"
        assert next_ctx.stale_message_id == "m-old"
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "second event"), force=True)
        assert len(adapter.sent) == 1
        assert adapter.edits[-1][1] == "m1"

    asyncio.run(run())


def test_delayed_delete_failure_after_next_context_registration_keeps_stale_id_current():
    async def run():
        adapter = DelayedDeleteAdapter(delete_success=False)
        renderer = make_renderer({"finalization": {"policy": "delete", "delay_seconds": 0.01}})
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "old turn"), force=True)
        finalize_task = asyncio.create_task(renderer.finalize(session_id="s1", success=True))
        await asyncio.sleep(0)
        next_ctx = make_ctx(adapter)
        renderer.register_context(next_ctx)
        await finalize_task

        assert adapter.deleted == [("chat", "m1")]
        assert next_ctx.stale_message_id == "m1"
        assert next_ctx.message_id is None
        assert "delete" in next_ctx.delete_failed_reason.lower()

    asyncio.run(run())


def test_zero_finalization_delay_is_preserved():
    settings = load_settings({"progress_tail": {"finalization": {"delay_seconds": 0}}})

    assert settings.finalization.delay_seconds == 0


def test_keep_policy_does_not_edit_finalized_bubble_without_new_context():
    async def run():
        adapter = DeletableAdapter()
        renderer = make_renderer({"finalization": {"policy": "keep", "delay_seconds": 0}})
        ctx = make_ctx(adapter, platform="discord")
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "old turn"), force=True)
        await renderer.finalize(session_id="s1", success=True)
        previous_edit_count = len(adapter.edits)
        await renderer.handle_event(ToolEvent("s1", "k1", "discord", "late event"), force=True)

        assert len(adapter.sent) == 1
        assert len(adapter.edits) == previous_edit_count
        assert list(ctx.tool_lines) == []

    asyncio.run(run())
