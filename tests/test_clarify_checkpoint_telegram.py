import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from hermes_progress_tail.hooks.telegram_formatting import _escape_telegram_mdv2
from hermes_progress_tail.models.decision import DecisionRecord
from hermes_progress_tail.models.events import ToolEvent
from hermes_progress_tail.monkeypatches import (
    install_telegram_format_monkeypatch,
    uninstall_telegram_format_monkeypatch,
)
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.runtime.clarify import run_clarify_freeze_barrier
from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.state import SessionContext


class Result:
    def __init__(self, success=True, message_id=None, error="", retryable=None):
        self.success = success
        self.message_id = message_id
        self.error = error
        self.retryable = retryable


class GatewayBot:
    def __init__(self):
        self.api_calls = []
        self.markdown_edits = []
        self.rich_error = None

    async def do_api_request(self, method, *, api_kwargs):
        self.api_calls.append((method, api_kwargs))
        if self.rich_error is not None:
            raise self.rich_error
        return {"result": {"message_id": 999}}

    async def edit_message_text(self, **kwargs):
        self.markdown_edits.append(kwargs)
        return {"ok": True}


class PatchedTelegramAdapter:
    name = "telegram"
    MAX_MESSAGE_LENGTH = 4096

    def __init__(self):
        self._bot = GatewayBot()
        self.native_edits = []
        self.native_sends = []
        self.next_id = 900

    def format_message(self, content):
        return _escape_telegram_mdv2(content)[: self.MAX_MESSAGE_LENGTH]

    async def edit_message(self, chat_id, message_id, content, *, finalize=False, metadata=None):
        self.native_edits.append((chat_id, message_id, content, finalize, metadata))
        return Result(True, message_id)

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        self.native_sends.append((chat_id, content, reply_to, metadata))
        self.next_id += 1
        return Result(True, str(self.next_id))


class NoEditAdapter:
    name = "telegram"

    def __init__(self, order):
        self.order = order
        self.sent = []

    async def send(self, chat_id, content, metadata=None):
        self.order.append("snapshot")
        self.sent.append((chat_id, content, metadata))
        return Result(True, "snapshot-1")


class LoopThread:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()


def settings():
    return load_settings({"progress_tail": {"tools": {"timestamp": False}}})


def context(adapter, loop):
    ctx = SessionContext("session", "key", "telegram", "123", None, adapter, loop, "live_tail")
    ctx.delivery.message_id = "41"
    ctx.delivery.message_started_at = 1.0
    ctx.delivery.can_edit = True
    ctx.delivery.progress_message_ids.append("41")
    return ctx


def add_decision_records(renderer, ctx):
    records = (
        DecisionRecord(
            "assistant",
            "Keep **bold** snake_case with `inline_code` in the decision record.",
            "assistant:1",
            20,
            1.0,
        ),
        DecisionRecord("plan", "**Inspect node_modules heading**", "plan:1", 30, 2.0),
        DecisionRecord(
            "tool", "Verified `python -m pytest -q` with **bold evidence**.", "tool:1", 40, 3.0
        ),
    )
    for record in records:
        renderer.checkpoints.observe_record(ctx, record)


@pytest.fixture
def patched_gateway(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "gateway.platforms.base",
        SimpleNamespace(SendResult=Result, utf16_len=len),
    )
    monkeypatch.setitem(
        sys.modules,
        "gateway.platforms.telegram",
        SimpleNamespace(ParseMode=SimpleNamespace(MARKDOWN_V2="MarkdownV2")),
    )
    uninstall_telegram_format_monkeypatch(PatchedTelegramAdapter)
    assert install_telegram_format_monkeypatch(PatchedTelegramAdapter)
    try:
        yield
    finally:
        uninstall_telegram_format_monkeypatch(PatchedTelegramAdapter)


def test_editable_freeze_reaches_rich_adapter_with_checkpoint_hierarchy_and_one_resolution(
    patched_gateway,
):
    async def run():
        adapter = PatchedTelegramAdapter()
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, asyncio.get_running_loop())
        renderer.register_context(ctx)
        add_decision_records(renderer, ctx)

        assert await renderer.freeze_clarify(
            ctx,
            {"question": "Keep `node_modules` and snake_case?", "choices": ["**Yes**", "No"]},
            lambda: False,
        )
        assert [call[0] for call in adapter._bot.api_calls] == ["editMessageText"]
        rich = adapter._bot.api_calls[0][1]["rich_message"]["markdown"]
        for heading in ("## Decision", "## Context so far", "## Plan", "## Relevant evidence"):
            assert heading in rich
        assert "snake_case" in rich and "`inline_code`" in rich and "**bold**" in rich
        assert "node_modules" in rich and "- Inspect node_modules heading" in rich
        assert not adapter.native_edits and not adapter.native_sends

        assert await renderer.resolve_clarify(ctx, "resolved", "yes")
        assert not await renderer.resolve_clarify(ctx, "resolved", "yes again")
        assert [call[0] for call in adapter._bot.api_calls] == ["editMessageText"] * 2
        assert all(call[1]["message_id"] == 41 for call in adapter._bot.api_calls)
        resolution = adapter._bot.api_calls[-1][1]["rich_message"]["markdown"]
        assert "RESOLVED · yes" in resolution

        call_count = len(adapter._bot.api_calls)
        markdown_edit_count = len(adapter._bot.markdown_edits)
        native_edit_count = len(adapter.native_edits)
        await renderer.handle_event(
            ToolEvent("session", "key", "telegram", "✅ terminal: later work · done"), force=True
        )
        assert adapter.native_sends == []
        assert len(adapter._bot.markdown_edits) == markdown_edit_count
        assert len(adapter.native_edits) == native_edit_count
        later_calls = adapter._bot.api_calls[call_count:]
        assert [call[0] for call in later_calls] == ["sendRichMessage"]
        assert ctx.delivery.message_id == "999" and ctx.delivery.message_id != "41"

    asyncio.run(run())


def test_formatted_rich_and_markdownv2_checkpoint_payloads_fit_the_telegram_budget(patched_gateway):
    async def run():
        punctuation = "_*[]()~`>#+-=|{}.!" * 50
        args = {"question": punctuation, "choices": [punctuation] * 4}

        rich_adapter = PatchedTelegramAdapter()
        rich_renderer = ProgressRenderer(settings())
        rich_ctx = context(rich_adapter, asyncio.get_running_loop())
        rich_renderer.register_context(rich_ctx)
        add_decision_records(rich_renderer, rich_ctx)
        assert await rich_renderer.freeze_clarify(rich_ctx, args, lambda: False)
        rich = rich_adapter._bot.api_calls[0][1]["rich_message"]["markdown"]
        assert len(rich) <= rich_adapter.MAX_MESSAGE_LENGTH

        fallback_adapter = PatchedTelegramAdapter()
        fallback_adapter._bot.rich_error = NotImplementedError("rich endpoint unsupported")
        fallback_renderer = ProgressRenderer(settings())
        fallback_ctx = context(fallback_adapter, asyncio.get_running_loop())
        fallback_renderer.register_context(fallback_ctx)
        add_decision_records(fallback_renderer, fallback_ctx)
        assert await fallback_renderer.freeze_clarify(fallback_ctx, args, lambda: False)
        assert [call[0] for call in fallback_adapter._bot.api_calls] == ["editMessageText"]
        assert len(fallback_adapter._bot.markdown_edits) == 1
        assert (
            len(fallback_adapter._bot.markdown_edits[0]["text"])
            <= fallback_adapter.MAX_MESSAGE_LENGTH
        )
        assert not fallback_adapter.native_edits and not fallback_adapter.native_sends

    asyncio.run(run())


@pytest.mark.parametrize(
    "error",
    [RuntimeError("temporary network failure"), RuntimeError("too many requests retry after 1")],
)
def test_transient_and_flood_checkpoint_errors_fail_open_without_any_retry(patched_gateway, error):
    with LoopThread() as worker:
        adapter = PatchedTelegramAdapter()
        adapter._bot.rich_error = error
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, worker.loop)
        renderer.register_context(ctx)

        result = run_clarify_freeze_barrier(
            ctx,
            lambda guard: renderer.freeze_clarify(
                ctx, {"question": "Continue?", "choices": []}, guard.is_cancelled
            ),
        )
        native_path = ["native-prompt"]

        assert [call[0] for call in adapter._bot.api_calls] == ["editMessageText"]
        assert (
            adapter._bot.markdown_edits == []
            and adapter.native_edits == []
            and adapter.native_sends == []
        )
        assert not result and native_path == ["native-prompt"]
        assert ctx.decision.active_checkpoint is None and ctx.decision.pending_freeze is None
        assert ctx.decision.protected_message_ids == set() and ctx.decision.sequence == 0
        assert ctx.delivery.message_id == "41" and ctx.delivery.progress_message_ids == ["41"]


def test_worker_thread_no_edit_snapshot_precedes_native_prompt_and_same_loop_is_best_effort():
    with LoopThread() as worker:
        ordered = []
        adapter = NoEditAdapter(ordered)
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, worker.loop)
        ctx.delivery.message_id = None
        ctx.delivery.can_edit = False
        renderer.register_context(ctx)
        assert run_clarify_freeze_barrier(
            ctx,
            lambda guard: renderer.freeze_clarify(
                ctx, {"question": "Continue?", "choices": []}, guard.is_cancelled
            ),
        )
        ordered.append("native-prompt")
        assert ordered == ["snapshot", "native-prompt"]
        assert len(adapter.sent) == 1 and ctx.decision.active_checkpoint.editable is False

    async def same_loop():
        ordered = []
        adapter = NoEditAdapter(ordered)
        renderer = ProgressRenderer(settings())
        ctx = context(adapter, asyncio.get_running_loop())
        ctx.delivery.message_id = None
        ctx.delivery.can_edit = False
        renderer.register_context(ctx)
        assert run_clarify_freeze_barrier(
            ctx,
            lambda guard: renderer.freeze_clarify(
                ctx, {"question": "Continue?", "choices": []}, guard.is_cancelled
            ),
        )
        ordered.append("native-prompt")
        await asyncio.sleep(0)
        assert ordered.count("snapshot") == 1 and ordered.count("native-prompt") == 1

    asyncio.run(same_loop())
