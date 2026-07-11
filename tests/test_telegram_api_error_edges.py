from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hermes_progress_tail.hooks import telegram
from tests.support.telegram import SendResult


@pytest.fixture(autouse=True)
def isolate_telegram_state(monkeypatch):
    registries = (
        telegram._TELEGRAM_ORIGINALS,
        telegram._TELEGRAM_SEND_ORIGINALS,
        telegram._TELEGRAM_TOPIC_RECOVERY_ORIGINALS,
    )
    snapshots = [dict(registry) for registry in registries]
    yield
    for registry in registries:
        for cls, original in list(registry.items()):
            if registry is telegram._TELEGRAM_ORIGINALS:
                cls.edit_message = original
                marker = telegram._TELEGRAM_PATCH_MARKER
            elif registry is telegram._TELEGRAM_SEND_ORIGINALS:
                cls.send = original
                continue
            else:
                cls._recover_telegram_topic_thread_id = original
                marker = telegram._TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER
            if marker in cls.__dict__:
                delattr(cls, marker)
    for registry, snapshot in zip(registries, snapshots, strict=True):
        registry.clear()
        registry.update(snapshot)


def run(awaitable):
    return asyncio.run(awaitable)


def test_settings_failure_default_and_adapter_overrides(monkeypatch):
    from dataclasses import replace

    from hermes_progress_tail.hooks.contracts import current_hook_callbacks

    callbacks = replace(
        current_hook_callbacks(), telegram_settings=lambda: (_ for _ in ()).throw(RuntimeError())
    )
    assert telegram._runtime_telegram_settings(callbacks) is None
    adapter = SimpleNamespace(_hermes_progress_tail_rich_disabled=True)
    adapter._hermes_progress_tail_rich_messages = True
    assert telegram._telegram_rich_enabled(adapter) is True
    adapter._hermes_progress_tail_rich_messages = False
    assert telegram._telegram_rich_enabled(adapter) is False
    del adapter._hermes_progress_tail_rich_messages
    del adapter._hermes_progress_tail_rich_disabled
    assert telegram._telegram_rich_enabled(adapter) is True


@pytest.mark.parametrize(
    ("exc", "capability", "flood"),
    [
        (SimpleNamespace(error_code=404), True, False),
        (RuntimeError("endpoint not found"), True, False),
        (NotImplementedError(), True, False),
        (SimpleNamespace(error_code=429), False, True),
        (RuntimeError("Too many requests"), False, True),
        (RuntimeError("retry after 9"), False, True),
    ],
)
def test_error_classifiers(exc, capability, flood):
    if not isinstance(exc, Exception):

        class Error(Exception):
            pass

        wrapped = Error()
        wrapped.error_code = exc.error_code
        exc = wrapped
    assert telegram._telegram_rich_capability_error(exc) is capability
    assert telegram._is_flood_control(exc) is flood


@pytest.mark.parametrize(
    ("retry_after", "text", "expected"),
    [(7, "flood", 7.0), (999, "flood", 600.0), (None, "retry in 12", 12.0), (None, "flood", 300.0)],
)
def test_flood_seconds_and_deadline(monkeypatch, retry_after, text, expected):
    class Error(Exception):
        pass

    exc = Error(text)
    if retry_after is not None:
        exc.retry_after = retry_after
    assert telegram._flood_control_seconds(exc) == expected
    adapter = SimpleNamespace()
    monkeypatch.setattr(telegram.time, "monotonic", lambda: 100.0)
    telegram._latch_rich_flood_off(adapter, exc)
    assert adapter._hermes_progress_tail_rich_disabled is True
    assert adapter._hermes_progress_tail_rich_flood_until == 100.0 + expected
    string_adapter = SimpleNamespace()
    telegram._latch_rich_flood_off_str(string_adapter, text)
    string_expected = min(float(text.split()[-1]), 600.0) if "retry" in text else 300.0
    assert string_adapter._hermes_progress_tail_rich_flood_until == 100.0 + string_expected


def test_rich_edit_disabled_not_modified_and_lost():
    adapter = SimpleNamespace(_hermes_progress_tail_rich_messages=False)
    assert run(telegram._try_edit_rich_message(adapter, "1", "2", "x", SendResult)) is None

    async def raises(message):
        raise RuntimeError(message)

    for message, success, retryable in [
        ("Message is not modified", True, None),
        ("Message to edit not found", False, False),
    ]:
        bot = SimpleNamespace(do_api_request=AsyncMock(side_effect=RuntimeError(message)))
        adapter = SimpleNamespace(_bot=bot)
        result = run(telegram._try_edit_rich_message(adapter, "1", "2", "x", SendResult))
        assert result.success is success
        assert result.message_id == "2"
        assert result.retryable is retryable
        if not success:
            assert result.error == f"message_lost: {message}"


def test_core_rich_send_decline_failure_and_exception(monkeypatch):
    async def disabled(*args):
        return None

    adapter = SimpleNamespace(_try_send_rich=disabled, _rich_send_disabled=True)
    assert run(telegram._try_send_rich_message(adapter, "1", "x", SendResult)) is None
    assert adapter._hermes_progress_tail_rich_disabled is True

    failed = SendResult(False, error="ordinary failure", retryable=False)
    adapter = SimpleNamespace(_try_send_rich=AsyncMock(return_value=failed))
    assert run(telegram._try_send_rich_message(adapter, "1", "x", SendResult)) is failed

    adapter = SimpleNamespace(_try_send_rich=AsyncMock(side_effect=RuntimeError("gateway down")))
    result = run(telegram._try_send_rich_message(adapter, "1", "x", SendResult))
    assert result.success is False and result.retryable is True
    assert result.error == "gateway down"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"message_id": 5}, "5"),
        ({"result": {"message_id": 6}}, "6"),
        (SimpleNamespace(message_id=7), "7"),
        ({}, None),
    ],
)
def test_bot_rich_send_id_shapes_and_thread(response, expected):
    request = AsyncMock(return_value=response)
    adapter = SimpleNamespace(_bot=SimpleNamespace(do_api_request=request))
    result = run(
        telegram._try_send_rich_message(
            adapter, "10", "hello", SendResult, metadata={"thread_id": "12"}
        )
    )
    assert result.success is True and result.message_id == expected
    assert request.await_args.args == ("sendRichMessage",)
    payload = request.await_args.kwargs["api_kwargs"]
    assert payload["chat_id"] == 10 and payload["message_thread_id"] == 12


def test_bot_unsupported_and_send_exception_outcomes():
    assert (
        run(telegram._try_send_rich_message(SimpleNamespace(_bot=object()), "1", "x", SendResult))
        is None
    )

    adapter = SimpleNamespace()
    assert (
        telegram._send_rich_exception_result(adapter, AttributeError("missing"), SendResult) is None
    )
    assert adapter._hermes_progress_tail_rich_disabled is True
    assert adapter._rich_send_disabled is True

    adapter = SimpleNamespace()
    assert (
        telegram._send_rich_exception_result(
            adapter, RuntimeError("Bad Request: can't parse"), SendResult
        )
        is None
    )
    assert not hasattr(adapter, "_hermes_progress_tail_rich_disabled")

    result = telegram._send_rich_exception_result(adapter, RuntimeError("offline"), SendResult)
    assert result.success is False and result.retryable is True and result.error == "offline"


def test_legacy_reply_forwarding():
    assert telegram._original_send_kwargs("77", {"key": "value"}) == {
        "reply_to": "77",
        "metadata": {"key": "value", "expect_edits": True},
    }
