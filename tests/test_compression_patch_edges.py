import sys
import types

import pytest

from hermes_progress_tail.hooks import compression


@pytest.fixture(autouse=True)
def restore_compression_globals():
    status = compression._COMPRESSION_STATUS_ORIGINALS.copy()
    lifecycle = compression._COMPRESSION_LIFECYCLE_ORIGINALS.copy()
    old_run_agent = sys.modules.get("run_agent")
    yield
    for registry, snapshot, marker, method in (
        (
            compression._COMPRESSION_STATUS_ORIGINALS,
            status,
            compression._COMPRESSION_STATUS_PATCH_MARKER,
            "_emit_status",
        ),
        (
            compression._COMPRESSION_LIFECYCLE_ORIGINALS,
            lifecycle,
            compression._COMPRESSION_LIFECYCLE_PATCH_MARKER,
            "_compress_context",
        ),
    ):
        for cls, original in list(registry.items()):
            if cls not in snapshot:
                setattr(cls, method, original)
                if hasattr(cls, marker):
                    delattr(cls, marker)
        registry.clear()
        registry.update(snapshot)
    if old_run_agent is None:
        sys.modules.pop("run_agent", None)
    else:
        sys.modules["run_agent"] = old_run_agent


def test_default_import_install_repeat_missing_and_restore(monkeypatch):
    class Agent:
        def _emit_status(self, text):
            return "original:" + text

        def _compress_context(self, messages, system_message):
            return messages

    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=Agent))
    assert compression.install_compression_status_monkeypatch()
    assert compression.install_compression_status_monkeypatch()
    assert compression.install_compression_lifecycle_monkeypatch()
    assert compression.install_compression_lifecycle_monkeypatch()
    assert compression.uninstall_compression_status_monkeypatch()
    assert compression.uninstall_compression_lifecycle_monkeypatch()
    assert not hasattr(Agent, compression._COMPRESSION_STATUS_PATCH_MARKER)
    assert not hasattr(Agent, compression._COMPRESSION_LIFECYCLE_PATCH_MARKER)
    assert not compression.uninstall_compression_status_monkeypatch(Agent)
    assert not compression.install_compression_status_monkeypatch(type("Missing", (), {}))
    assert not compression.install_compression_lifecycle_monkeypatch(type("Missing", (), {}))


def test_default_import_absence(monkeypatch):
    monkeypatch.delitem(sys.modules, "run_agent", raising=False)
    real_import = __import__

    def blocked(name, *args, **kwargs):
        if name == "run_agent":
            raise ImportError("absent")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    assert not compression.install_compression_status_monkeypatch()
    assert not compression.install_compression_lifecycle_monkeypatch()
    assert not compression.uninstall_compression_status_monkeypatch()
    assert not compression.uninstall_compression_lifecycle_monkeypatch()


@pytest.mark.parametrize("callback_result", [False, RuntimeError("capture")])
def test_status_false_or_exception_delegates(monkeypatch, callback_result):
    import hermes_progress_tail.runtime.plugin as plugin

    calls = []

    class Agent:
        def _emit_status(self, text, flag=None):
            calls.append((text, flag))
            return "native"

    def callback(*_):
        if isinstance(callback_result, Exception):
            raise callback_result
        return callback_result

    monkeypatch.setattr(plugin, "on_compression_status_from_agent", callback)
    assert compression.install_compression_status_monkeypatch(Agent)
    assert Agent()._emit_status("Compacting context", flag=1) == "native"
    assert calls == [("Compacting context", 1)]
    assert compression.uninstall_compression_status_monkeypatch(Agent)


def test_lifecycle_callback_exceptions_and_native_failure(monkeypatch):
    import hermes_progress_tail.runtime.plugin as plugin

    phases = []

    def callback(_agent, **data):
        phases.append(data["phase"])
        raise RuntimeError("callback")

    monkeypatch.setattr(plugin, "on_compression_lifecycle_from_agent", callback)

    class Agent:
        session_id = "old"

        def _compress_context(self, messages, system_message):
            raise ValueError("native")

    assert compression.install_compression_lifecycle_monkeypatch(Agent)
    with pytest.raises(ValueError, match="native"):
        Agent()._compress_context([], "system")
    assert phases == ["started", "failed"]
    assert compression.uninstall_compression_lifecycle_monkeypatch(Agent)


def test_lifecycle_normalizes_malformed_status_and_counts(monkeypatch):
    import hermes_progress_tail.runtime.plugin as plugin

    events = []
    monkeypatch.setattr(
        plugin, "on_compression_lifecycle_from_agent", lambda _agent, **data: events.append(data)
    )

    class Compressor:
        compression_count = 3
        last_prompt_tokens = -1
        awaiting_real_usage_after_compression = True
        last_compression_rough_tokens = "bad"

        def get_status(self):
            return "malformed"

    class Agent:
        session_id = "old"
        context_compressor = Compressor()

        def _compress_context(self, messages, system_message, **kwargs):
            self.session_id = "new"
            return object()

    assert compression.install_compression_lifecycle_monkeypatch(Agent)
    result = Agent()._compress_context(iter([1]), "system", approx_tokens="bad")
    assert result is not None
    completed = events[-1]
    assert completed["phase"] == "completed"
    assert completed["before_count"] == completed["after_count"] == 0
    assert completed["after_tokens"] == -1
    assert completed["after_tokens_kind"] == ""
    assert compression.uninstall_compression_lifecycle_monkeypatch(Agent)
