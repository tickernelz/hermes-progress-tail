import asyncio

from hermes_progress_tail.monkeypatches import (
    install_compression_lifecycle_monkeypatch,
    uninstall_compression_lifecycle_monkeypatch,
)
from hermes_progress_tail.plugin import _on_post_llm_call
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.state import SessionContext, ToolEvent
from tests.support.rendering import Result


class EditableAdapter:
    name = "editable"

    def __init__(self):
        self.sent = []
        self.edits = []
        self.deleted = []
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
        return True


def make_renderer(config=None):
    return ProgressRenderer(
        load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False},
                    "assistant": {"min_update_chars": 1},
                    "cleanup": {"auto_delete": True, "delay_seconds": 60},
                    **(config or {}),
                }
            }
        )
    )


def make_ctx(adapter, *, session_id="old-session", session_key="stable-key"):
    return SessionContext(
        session_id,
        session_key,
        "telegram",
        "chat",
        None,
        adapter,
        asyncio.get_running_loop(),
        "live_tail",
        timestamp=False,
    )


def test_renderer_migrates_active_context_across_compression_session_rotation():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("old-session", "stable-key", "telegram", "before compression"),
            force=True,
        )
        assert ctx.message_id == "m1"
        ctx.compaction_count = 2
        started_at = ctx.delivery.message_started_at
        delivery_state = ctx.delivery
        history = ctx.delivery.progress_message_ids

        migrated = renderer.migrate_context("old-session", "new-session", session_key="stable-key")
        assert migrated is True
        assert renderer.find_context("old-session", "") is None
        assert renderer.find_context("new-session", "") is ctx
        assert renderer.find_context("", "stable-key") is ctx
        assert ctx.session_id == "new-session"
        assert ctx.message_id == "m1"
        assert ctx.delivery is delivery_state
        assert ctx.delivery.progress_message_ids is history
        assert ctx.delivery.message_started_at == started_at
        assert ctx.compaction_count == 2

        await renderer.handle_event(
            ToolEvent("new-session", "stable-key", "telegram", "after compression"),
            force=True,
        )

        assert len(adapter.sent) == 1
        assert adapter.edits[-1] == (
            "chat",
            "m1",
            "▰ 🧰 Tools\nbefore compression\nafter compression",
        )

    asyncio.run(run())


def test_renderer_migration_cancels_pending_delete_for_reactivated_progress():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer({"cleanup": {"auto_delete": True, "delay_seconds": 60}})
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(ToolEvent("old-session", "stable-key", "telegram", "work"))
        await renderer.finalize(session_id="old-session", success=True)
        assert ctx.delete_task is not None
        assert not ctx.delete_task.done()
        ctx.compaction_count = 2

        renderer.migrate_context("old-session", "new-session", session_key="stable-key")

        assert ctx.progress_state == "active"
        assert ctx.delete_task is None or ctx.delete_task.cancelled()
        assert ctx.message_id == "m1"
        assert ctx.compaction_count == 2

    asyncio.run(run())


def test_compression_lifecycle_monkeypatch_migrates_progress_context_and_reports_completion(
    monkeypatch,
):
    async def run():
        import hermes_progress_tail.plugin as plugin

        adapter = EditableAdapter()
        renderer = make_renderer()
        monkeypatch.setattr(plugin, "_renderer", renderer)
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        await renderer.handle_event(
            ToolEvent("old-session", "stable-key", "telegram", "before compression"),
            force=True,
        )

        class Compressor:
            compression_count = 1
            last_prompt_tokens = 52738

            def get_status(self):
                return {"last_prompt_tokens": self.last_prompt_tokens}

        class FakeAgent:
            session_id = "old-session"
            gateway_session_key = "stable-key"
            platform = "telegram"
            context_compressor = Compressor()

            def _compress_context(self, messages, system_message, **kwargs):
                self.session_id = "new-session"
                return ([{"role": "user", "content": "compressed"}], "system")

        install_compression_lifecycle_monkeypatch(FakeAgent)
        try:
            agent = FakeAgent()
            result = agent._compress_context(
                [{"role": "user", "content": "one"}] * 357,
                "system",
                approx_tokens=180740,
            )
        finally:
            uninstall_compression_lifecycle_monkeypatch(FakeAgent)

        await asyncio.sleep(0.01)

        assert result[0] == [{"role": "user", "content": "compressed"}]
        assert renderer.find_context("new-session", "stable-key") is ctx
        assert renderer.find_context("old-session", "") is None
        assert ctx.compaction_count == 1
        latest = adapter.edits[-1][2]
        assert "Context compacted" in latest
        assert "357 → 1 messages" in latest
        assert "181k → 53k tokens" in latest

    asyncio.run(run())


def test_compression_lifecycle_uses_rough_after_tokens_when_real_usage_is_pending(
    monkeypatch,
):
    async def run():
        import hermes_progress_tail.plugin as plugin

        adapter = EditableAdapter()
        renderer = make_renderer()
        monkeypatch.setattr(plugin, "_renderer", renderer)
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        await renderer.handle_event(
            ToolEvent("old-session", "stable-key", "telegram", "before compression"),
            force=True,
        )

        class Compressor:
            compression_count = 1
            last_prompt_tokens = -1
            last_compression_rough_tokens = 79468
            awaiting_real_usage_after_compression = True

            def get_status(self):
                return {"last_prompt_tokens": self.last_prompt_tokens}

        class FakeAgent:
            session_id = "old-session"
            gateway_session_key = "stable-key"
            platform = "telegram"
            context_compressor = Compressor()

            def _compress_context(self, messages, system_message, **kwargs):
                self.session_id = "new-session"
                return ([{"role": "user", "content": "compressed"}] * 72, "system")

        install_compression_lifecycle_monkeypatch(FakeAgent)
        try:
            agent = FakeAgent()
            agent._compress_context(
                [{"role": "user", "content": "one"}] * 270,
                "system",
                approx_tokens=241771,
            )
        finally:
            uninstall_compression_lifecycle_monkeypatch(FakeAgent)

        await asyncio.sleep(0.01)

        latest = adapter.edits[-1][2]
        assert "Context compacted" in latest
        assert "270 → 72 messages" in latest
        assert "rough 242k → 79k tokens" in latest
        assert "-1" not in latest

    asyncio.run(run())


def test_compression_lifecycle_omits_stale_rough_tokens_when_not_awaiting_real_usage(
    monkeypatch,
):
    async def run():
        import hermes_progress_tail.plugin as plugin

        adapter = EditableAdapter()
        renderer = make_renderer()
        monkeypatch.setattr(plugin, "_renderer", renderer)
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        await renderer.handle_event(
            ToolEvent("old-session", "stable-key", "telegram", "before compression"),
            force=True,
        )

        class Compressor:
            compression_count = 1
            last_prompt_tokens = -1
            last_compression_rough_tokens = 79468
            awaiting_real_usage_after_compression = False

            def get_status(self):
                return {"last_prompt_tokens": self.last_prompt_tokens}

        class FakeAgent:
            session_id = "old-session"
            gateway_session_key = "stable-key"
            platform = "telegram"
            context_compressor = Compressor()

            def _compress_context(self, messages, system_message, **kwargs):
                self.session_id = "new-session"
                return ([{"role": "user", "content": "compressed"}] * 72, "system")

        install_compression_lifecycle_monkeypatch(FakeAgent)
        try:
            agent = FakeAgent()
            agent._compress_context(
                [{"role": "user", "content": "one"}] * 270,
                "system",
                approx_tokens=241771,
            )
        finally:
            uninstall_compression_lifecycle_monkeypatch(FakeAgent)

        await asyncio.sleep(0.01)

        latest = adapter.edits[-1][2]
        assert "Context compacted" in latest
        assert "270 → 72 messages" in latest
        assert "rough 242k → 79k tokens" not in latest
        assert "-1" not in latest

    asyncio.run(run())


def test_compression_lifecycle_omits_non_positive_after_tokens():
    import hermes_progress_tail.plugin as plugin

    text = plugin._compression_lifecycle_completed_text(
        {
            "before_count": 270,
            "after_count": 72,
            "before_tokens": 241771,
            "after_tokens": -1,
        }
    )

    assert text == "Context compacted · 270 → 72 messages"
    assert "-1" not in text


def test_post_llm_finalize_finds_compression_migrated_context_by_session_key(monkeypatch):
    async def run():
        import hermes_progress_tail.plugin as plugin

        adapter = EditableAdapter()
        renderer = make_renderer({"cleanup": {"auto_delete": False}})
        monkeypatch.setattr(plugin, "_renderer", renderer)
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)
        await renderer.handle_event(ToolEvent("old-session", "stable-key", "telegram", "work"))
        renderer.migrate_context("old-session", "new-session", session_key="stable-key")

        agent = type(
            "Agent",
            (),
            {"session_id": "new-session", "gateway_session_key": "stable-key"},
        )()
        _on_post_llm_call(session_id="new-session", platform="telegram", agent=agent)
        await asyncio.sleep(0.01)

        assert ctx.progress_state == "finalized"
        assert ctx.session_id == "new-session"

    asyncio.run(run())
