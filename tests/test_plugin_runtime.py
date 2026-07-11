from types import SimpleNamespace

from hermes_progress_tail.hooks.contracts import current_hook_callbacks
from hermes_progress_tail.hooks.install_report import PatchInstallReport, PatchStatus
from hermes_progress_tail.runtime import plugin
from hermes_progress_tail.runtime.container import PluginRuntime
from hermes_progress_tail.settings.config import load_settings


def test_runtime_lazily_creates_one_renderer_and_propagates_settings_identity():
    first = load_settings({"progress_tail": {"tools": {"lines": 2}}})
    second = load_settings({"progress_tail": {"tools": {"lines": 7}}})
    settings = iter((first, second))
    runtime = PluginRuntime(settings_loader=lambda: next(settings))

    renderer = runtime.get_renderer()
    assert runtime.get_renderer() is renderer
    assert renderer.settings is second
    assert renderer.delegate_renderer.settings is second


def test_runtime_capture_identity_and_report_replacement():
    runtime = PluginRuntime()
    assert runtime.assistant_capture == {
        "status": "never",
        "session_id": "",
        "session_key_present": False,
        "text_preview": "",
        "already_streamed": False,
        "updated_at": 0.0,
    }
    report = PatchInstallReport((PatchStatus("agent_callbacks", True, "agent"),))
    runtime.set_patch_report(report)
    assert runtime.patch_report is report


def test_callbacks_bind_events_and_forward_keywords(monkeypatch):
    seen = {}

    def reasoning(agent, text, **kwargs):
        seen.update(agent=agent, text=text, kwargs=kwargs)

    monkeypatch.setattr(
        "hermes_progress_tail.runtime.agent_events.on_reasoning_delta_from_agent", reasoning
    )
    callbacks = PluginRuntime().callbacks()
    agent = object()
    callbacks.on_reasoning_delta(agent, "delta", source="inline")
    assert seen == {"agent": agent, "text": "delta", "kwargs": {"source": "inline"}}


def test_reasoning_and_telegram_narrow_callbacks():
    settings = load_settings({})
    renderer = SimpleNamespace(
        settings=settings,
        find_context=lambda sid, key: SimpleNamespace(reasoning_enabled=True),
        replace_settings=lambda value: None,
    )
    runtime = PluginRuntime(
        settings_loader=lambda: settings, renderer_factory=lambda value: renderer
    )
    agent = SimpleNamespace(session_id="sid", gateway_session_key="key")
    assert runtime.reasoning_enabled(agent) is True
    assert runtime.telegram_settings() is settings.telegram


def test_plugin_facades_share_runtime_state(monkeypatch):
    assert plugin._ASSISTANT_CAPTURE is plugin._runtime.assistant_capture
    replacement = SimpleNamespace(replace_settings=lambda settings: None)
    monkeypatch.setattr(plugin, "_renderer", replacement)
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings({}))
    assert plugin._get_renderer() is replacement
    assert plugin._runtime.renderer is replacement
    assert current_hook_callbacks().reasoning_enabled.__self__ is plugin._runtime


def test_register_retains_runtime_and_records_partial_report(monkeypatch):
    runtime = plugin._runtime
    report = PatchInstallReport((PatchStatus("agent_callbacks", True, "agent"),))
    monkeypatch.setattr(plugin, "install_monkeypatches_report", lambda callbacks: report)
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: {})
    ctx = SimpleNamespace(
        register_hook=lambda *args: None, register_command=lambda *args, **kwargs: None
    )
    plugin.register(ctx)
    plugin.register(ctx)
    assert plugin._runtime is runtime
    assert runtime.patch_report is report
