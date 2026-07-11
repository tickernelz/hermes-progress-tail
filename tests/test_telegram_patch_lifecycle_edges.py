from __future__ import annotations

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hermes_progress_tail.hooks import telegram
from tests.support.telegram import SendResult


def run(awaitable):
    return asyncio.run(awaitable)


@pytest.fixture(autouse=True)
def isolate_patch_lifecycle():
    registries = (
        telegram._TELEGRAM_ORIGINALS,
        telegram._TELEGRAM_SEND_ORIGINALS,
        telegram._TELEGRAM_TOPIC_RECOVERY_ORIGINALS,
    )
    snapshots = [dict(item) for item in registries]
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


def test_markdown_code_spans_and_fences_are_untouched():
    content = "**__Title__** `**__inline__**`\n```\n***fenced***\n```\n***outside***"
    result = telegram.format_progress_tail_telegram_markdown(content, lambda value: value)
    assert result == "*__Title__* `**__inline__**`\n```\n***fenced***\n```\n*_outside_*"


def adapter_class(*, include_send=True):
    class Adapter:
        MAX_MESSAGE_LENGTH = 4096

        def __init__(self):
            self._bot = object()
            self.calls = []

        async def edit_message(
            self, chat_id, message_id, content, *, finalize=False, metadata=None
        ):
            self.calls.append(("edit", chat_id, message_id, content, finalize, metadata))
            return "original-edit"

    if include_send:

        async def send(self, chat_id, content, reply_to=None, metadata=None):
            self.calls.append(("send", chat_id, content, reply_to, metadata))
            return "original-send"

        Adapter.send = send
    return Adapter


def test_patched_edit_import_failure_delegates_unchanged(monkeypatch):
    cls = adapter_class()
    original = cls.edit_message
    assert telegram.install_telegram_format_monkeypatch(cls) is True
    monkeypatch.setattr(
        telegram, "_resolve_telegram_parse_mode", lambda: (_ for _ in ()).throw(ImportError())
    )
    monkeypatch.setitem(
        sys.modules, "gateway.platforms.base", SimpleNamespace(SendResult=SendResult, utf16_len=len)
    )
    instance = cls()
    metadata = {"x": 1}
    assert run(instance.edit_message("1", "2", "body", metadata=metadata)) == "original-edit"
    assert instance.calls == [("edit", "1", "2", "body", False, metadata)]
    assert telegram.uninstall_telegram_format_monkeypatch(cls) is True
    assert cls.edit_message is original


def test_patched_edit_length_coercion_failure_delegates(monkeypatch):
    cls = adapter_class()
    telegram.install_telegram_format_monkeypatch(cls)
    monkeypatch.setattr(
        telegram, "_resolve_telegram_parse_mode", lambda: SimpleNamespace(MARKDOWN_V2="md")
    )
    monkeypatch.setitem(
        sys.modules,
        "gateway.platforms.base",
        SimpleNamespace(
            SendResult=SendResult, utf16_len=lambda value: (_ for _ in ()).throw(ValueError())
        ),
    )
    instance = cls()
    assert run(instance.edit_message("1", "2", "body")) == "original-edit"
    assert instance.calls[0][:5] == ("edit", "1", "2", "body", False)


def test_patched_send_import_failure_delegates_with_legacy_metadata(monkeypatch):
    cls = adapter_class()
    telegram.install_telegram_format_monkeypatch(cls)
    monkeypatch.setitem(sys.modules, "gateway.platforms.base", None)
    instance = cls()
    assert run(instance.send("1", "body", reply_to="9", metadata={"x": 1})) == "original-send"
    assert instance.calls == [("send", "1", "body", "9", {"x": 1, "expect_edits": True})]


def test_format_install_absent_edit_edit_only_repeat_and_restore():
    class Missing:
        pass

    assert telegram.install_telegram_format_monkeypatch(Missing) is False
    assert telegram.uninstall_telegram_format_monkeypatch(Missing) is False

    cls = adapter_class(include_send=False)
    original = cls.edit_message
    assert "send" not in cls.__dict__
    assert telegram.install_telegram_format_monkeypatch(cls) is True
    patched = cls.edit_message
    assert telegram.install_telegram_format_monkeypatch(cls) is True
    assert cls.edit_message is patched
    assert telegram.uninstall_telegram_format_monkeypatch(cls) is True
    assert cls.edit_message is original and "send" not in cls.__dict__
    assert telegram._TELEGRAM_PATCH_MARKER not in cls.__dict__


def test_format_resolver_total_failure_and_implicit_uninstall(monkeypatch):
    original_import = telegram.importlib.import_module
    monkeypatch.setattr(
        telegram.importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError(name))
    )
    assert telegram.install_telegram_format_monkeypatch() is False
    assert telegram.uninstall_telegram_format_monkeypatch() is False

    cls = adapter_class()
    telegram.install_telegram_format_monkeypatch(cls)
    module = ModuleType("hermes_plugins.telegram_platform.adapter")
    module.TelegramAdapter = cls
    monkeypatch.setitem(sys.modules, "hermes_plugins.telegram_platform.adapter", module)
    monkeypatch.setattr(telegram.importlib, "import_module", original_import)
    assert telegram.uninstall_telegram_format_monkeypatch() is True


def gateway_modules(monkeypatch, runner):
    gateway = ModuleType("gateway")
    gateway.__path__ = []
    run_module = ModuleType("gateway.run")
    run_module.GatewayRunner = runner
    monkeypatch.setitem(sys.modules, "gateway", gateway)
    monkeypatch.setitem(sys.modules, "gateway.run", run_module)


def test_topic_implicit_install_non_dm_repeat_and_uninstall(monkeypatch):
    class Runner:
        def __init__(self):
            self.calls = []

        def _recover_telegram_topic_thread_id(self, source):
            self.calls.append(source)
            return "original"

    original = Runner._recover_telegram_topic_thread_id
    gateway_modules(monkeypatch, Runner)
    assert telegram.install_telegram_topic_recovery_monkeypatch() is True
    patched = Runner._recover_telegram_topic_thread_id
    assert telegram.install_telegram_topic_recovery_monkeypatch() is True
    assert Runner._recover_telegram_topic_thread_id is patched
    runner = Runner()
    source = SimpleNamespace(chat_type="group", platform="telegram", thread_id="22")
    assert runner._recover_telegram_topic_thread_id(source) == "original"
    assert runner.calls == [source]
    assert telegram.uninstall_telegram_topic_recovery_monkeypatch() is True
    assert Runner._recover_telegram_topic_thread_id is original
    assert telegram._TELEGRAM_TOPIC_RECOVERY_PATCH_MARKER not in Runner.__dict__


def test_topic_missing_api_and_absent_original():
    class Missing:
        pass

    assert telegram.install_telegram_topic_recovery_monkeypatch(Missing) is False
    assert telegram.uninstall_telegram_topic_recovery_monkeypatch(Missing) is False


def test_topic_implicit_import_failure(monkeypatch):
    monkeypatch.setitem(sys.modules, "gateway", None)
    monkeypatch.setitem(sys.modules, "gateway.run", None)
    assert telegram.install_telegram_topic_recovery_monkeypatch() is False
    assert telegram.uninstall_telegram_topic_recovery_monkeypatch() is False
