from types import SimpleNamespace

from hermes_progress_tail.monkeypatches import (
    install_telegram_topic_recovery_monkeypatch,
    uninstall_telegram_topic_recovery_monkeypatch,
)


class FakeGatewayRunner:
    _TELEGRAM_GENERAL_TOPIC_IDS = frozenset({"", "1"})

    def __init__(self, recovered="77447"):
        self.recovered = recovered
        self.calls = []

    def _recover_telegram_topic_thread_id(self, source):
        self.calls.append(source)
        return self.recovered


def source(thread_id):
    return SimpleNamespace(
        platform="telegram",
        chat_type="dm",
        chat_id="191060132",
        user_id="191060132",
        thread_id=thread_id,
    )


def test_topic_recovery_monkeypatch_preserves_concrete_unknown_topic_thread():
    uninstall_telegram_topic_recovery_monkeypatch(FakeGatewayRunner)
    assert install_telegram_topic_recovery_monkeypatch(FakeGatewayRunner) is True
    gateway = FakeGatewayRunner(recovered="77447")

    recovered = gateway._recover_telegram_topic_thread_id(source("77445"))

    assert recovered is None
    assert len(gateway.calls) == 0
    uninstall_telegram_topic_recovery_monkeypatch(FakeGatewayRunner)


def test_topic_recovery_monkeypatch_allows_lobby_or_missing_thread_recovery():
    uninstall_telegram_topic_recovery_monkeypatch(FakeGatewayRunner)
    assert install_telegram_topic_recovery_monkeypatch(FakeGatewayRunner) is True
    gateway = FakeGatewayRunner(recovered="77447")

    assert gateway._recover_telegram_topic_thread_id(source("")) == "77447"
    assert gateway._recover_telegram_topic_thread_id(source("1")) == "77447"
    assert len(gateway.calls) == 2
    uninstall_telegram_topic_recovery_monkeypatch(FakeGatewayRunner)


def test_topic_recovery_monkeypatch_is_idempotent_and_uninstall_restores_original():
    uninstall_telegram_topic_recovery_monkeypatch(FakeGatewayRunner)
    original = FakeGatewayRunner._recover_telegram_topic_thread_id

    assert install_telegram_topic_recovery_monkeypatch(FakeGatewayRunner) is True
    first_patch = FakeGatewayRunner._recover_telegram_topic_thread_id
    assert install_telegram_topic_recovery_monkeypatch(FakeGatewayRunner) is True
    assert FakeGatewayRunner._recover_telegram_topic_thread_id is first_patch
    assert uninstall_telegram_topic_recovery_monkeypatch(FakeGatewayRunner) is True
    assert FakeGatewayRunner._recover_telegram_topic_thread_id is original


def test_topic_recovery_monkeypatch_leaves_non_telegram_sources_unchanged():
    uninstall_telegram_topic_recovery_monkeypatch(FakeGatewayRunner)
    assert install_telegram_topic_recovery_monkeypatch(FakeGatewayRunner) is True
    gateway = FakeGatewayRunner(recovered="77447")
    non_telegram = SimpleNamespace(
        platform="discord",
        chat_type="dm",
        chat_id="c1",
        user_id="u1",
        thread_id="thread-a",
    )

    assert gateway._recover_telegram_topic_thread_id(non_telegram) == "77447"
    assert gateway.calls == [non_telegram]
    uninstall_telegram_topic_recovery_monkeypatch(FakeGatewayRunner)
