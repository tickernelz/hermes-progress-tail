import sys
from types import ModuleType

import pytest

from hermes_progress_tail.runtime import config_runtime as runtime


def test_load_config_hermes_home_mapping_and_shapes(monkeypatch, tmp_path):
    constants = ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: tmp_path
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    path = tmp_path / "config.yaml"
    path.write_text("progress_tail:\n  enabled: true\n", encoding="utf-8")
    assert runtime._load_runtime_config() == {"progress_tail": {"enabled": True}}
    for text in ("", "- item\n", "3\n"):
        path.write_text(text, encoding="utf-8")
        assert runtime._load_runtime_config() == {}
    path.unlink()
    assert runtime._load_runtime_config() == {}


def test_load_config_home_fallback_and_failure(monkeypatch, tmp_path, caplog):
    constants = ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: (_ for _ in ()).throw(RuntimeError())
    monkeypatch.setitem(sys.modules, "hermes_constants", constants)
    monkeypatch.setattr(runtime.Path, "home", classmethod(lambda cls: tmp_path))
    path = tmp_path / ".hermes" / "config.yaml"
    path.parent.mkdir()
    path.write_text("bad: [", encoding="utf-8")
    with caplog.at_level("DEBUG"):
        assert runtime._load_runtime_config() == {}
    assert "config load failed" in caplog.text
    monkeypatch.setattr(
        runtime.Path, "read_text", lambda *a, **k: (_ for _ in ()).throw(OSError("read"))
    )
    assert runtime._load_runtime_config() == {}


def test_runtime_settings_are_freshly_delegated(monkeypatch):
    configs = iter(({"progress_tail": {}}, {"progress_tail": {"enabled": False}}))
    seen = []
    monkeypatch.setattr(runtime, "_load_runtime_config", lambda: next(configs))
    monkeypatch.setattr(runtime, "load_settings", lambda config: seen.append(config) or object())
    first = runtime._load_runtime_settings()
    second = runtime._load_runtime_settings()
    assert first is not second
    assert seen == [{"progress_tail": {}}, {"progress_tail": {"enabled": False}}]


def test_runtime_settings_delegate_real_nested_conversion(monkeypatch):
    platform_override = {"strategy": "snapshot", "tools_enabled": False}
    monkeypatch.setattr(
        runtime,
        "_load_runtime_config",
        lambda: {
            "progress_tail": {
                "tools": {"preview_length": 42, "lines": "invalid"},
                "platforms": {"telegram": platform_override},
            }
        },
    )

    settings = runtime._load_runtime_settings()

    assert settings.tools.preview_length == 42
    assert settings.tools.lines == 3
    assert settings.platforms == {"telegram": platform_override}


@pytest.mark.parametrize(
    "config,expected",
    [
        ({}, True),
        ({"progress_tail": []}, True),
        ({"progress_tail": {"enabled": False}}, False),
        ({"progress_tail": {"enabled": 0}}, True),
    ],
)
def test_progress_tail_enabled(config, expected):
    assert runtime._progress_tail_enabled(config) is expected


@pytest.mark.parametrize(
    "config,name,default,expected",
    [
        ({}, "x", False, False),
        ({"progress_tail": {"enabled": False}}, "x", True, False),
        ({"progress_tail": {"x": []}}, "x", True, True),
        ({"progress_tail": {"x": {"enabled": False}}}, "x", True, False),
        ({"progress_tail": {"x": {"enabled": 0}}}, "x", False, True),
    ],
)
def test_feature_enabled(config, name, default, expected):
    assert runtime._feature_enabled(config, name, default) is expected


def test_assistant_and_builtin_conflicts():
    assert runtime._assistant_tail_enabled({}) is True
    assert (
        runtime._builtin_interim_conflict({"display": {"interim_assistant_messages": True}}) is True
    )
    assert (
        runtime._builtin_interim_conflict({"display": {"interim_assistant_messages": False}})
        is False
    )
    assert runtime._builtin_interim_conflict({"display": []}) is False
    assert runtime._builtin_reasoning_conflict({"display": {"show_reasoning": True}}) is True
    assert runtime._builtin_reasoning_conflict({"display": {"show_reasoning": 1}}) is False
    disabled = {
        "display": {"show_reasoning": True},
        "progress_tail": {"reasoning": {"enabled": False}},
    }
    assert runtime._builtin_reasoning_conflict(disabled) is False


@pytest.mark.parametrize(
    "config,expected",
    [
        ({"progress_tail": {"enabled": False}}, False),
        ({}, True),
        ({"agent": []}, True),
        ({"agent": {}}, True),
        ({"agent": {"gateway_notify_interval": 1}}, True),
        ({"agent": {"gateway_notify_interval": "0"}}, False),
        ({"agent": {"gateway_notify_interval": -1}}, False),
        ({"agent": {"gateway_notify_interval": None}}, True),
        ({"agent": {"gateway_notify_interval": "bad"}}, True),
    ],
)
def test_core_notifier_conflict(config, expected):
    assert runtime._core_notifier_conflict(config) is expected


def test_warning_contracts():
    interim = runtime._interim_conflict_warning()
    reasoning = runtime._reasoning_conflict_warning()
    notifier = runtime._core_notifier_conflict_warning()
    assert "display.interim_assistant_messages=false" in interim
    assert "display.show_reasoning=false" in reasoning
    assert "agent.gateway_notify_interval=0" in notifier
    assert all(value.startswith("warning:") for value in (interim, reasoning, notifier))
