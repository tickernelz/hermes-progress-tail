import importlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_progress_tail.hooks.contracts import current_hook_callbacks
from hermes_progress_tail.hooks.install_report import (
    PatchFailureCategory,
    PatchInstallReport,
    PatchStatus,
)
from hermes_progress_tail.runtime import commands, plugin


class RegistrationContext:
    def __init__(self):
        self.hooks = []
        self.commands = []

    def register_hook(self, name, callback):
        self.hooks.append((name, callback))

    def register_command(self, name, callback, **kwargs):
        self.commands.append((name, callback, kwargs))


def test_commands_consume_configured_runtime_renderer_and_version(monkeypatch):
    expected_renderer = SimpleNamespace(
        settings=SimpleNamespace(background_jobs=SimpleNamespace(enabled=True)), sessions={}
    )
    wrong_renderer = SimpleNamespace(
        settings=SimpleNamespace(background_jobs=SimpleNamespace(enabled=False)), sessions={}
    )
    runtime = SimpleNamespace(get_renderer=lambda: expected_renderer)
    # Record both module globals with monkeypatch before exercising the public configurator,
    # so its process-level slot mutation is restored between tests.
    monkeypatch.setattr(commands, "_COMMAND_RUNTIME", commands._COMMAND_RUNTIME)
    monkeypatch.setattr(commands, "_COMMAND_VERSION", commands._COMMAND_VERSION)
    commands.configure_command_runtime(runtime, version="1.2.3-port")
    monkeypatch.setattr(plugin, "_get_renderer", lambda: wrong_renderer)
    monkeypatch.setattr(plugin, "VERSION", "0.0.0-composition-root")
    monkeypatch.setattr(commands, "_run_update_install", lambda *args, **kwargs: ["safe"])

    jobs = commands._command("jobs")
    update = commands._update_command("--dry-run --force --ref v9.9.9")

    assert jobs.splitlines()[0] == "background_jobs=enabled"
    assert "v1.2.3-port → v9.9.9" in update


def test_copied_plugin_registration_owns_independent_runtime_ports_and_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source_package = Path(__file__).parents[1] / "hermes_progress_tail"
    plugins = tmp_path / "hermes_plugins"
    copied_package = plugins / "b4_copy"
    plugins.mkdir()
    (plugins / "__init__.py").write_text("")
    shutil.copytree(source_package, copied_package)
    monkeypatch.syspath_prepend(str(tmp_path))
    copied_plugin = importlib.import_module("hermes_plugins.b4_copy.runtime.plugin")
    copied_commands = importlib.import_module("hermes_plugins.b4_copy.runtime.commands")
    copied_contracts = importlib.import_module("hermes_plugins.b4_copy.hooks.contracts")
    source_runtime = plugin._runtime
    copied_runtime = copied_plugin._runtime
    source_report = source_runtime.patch_report
    copied_report = PatchInstallReport((PatchStatus("copy", True, "copy-target"),))
    monkeypatch.setattr(
        copied_plugin, "install_monkeypatches_report", lambda callbacks: copied_report
    )
    monkeypatch.setattr(copied_plugin, "_load_runtime_config", lambda: {})
    context = RegistrationContext()

    try:
        copied_plugin.register(context)
        copied_plugin.register(context)
        copied_renderer = copied_runtime.get_renderer()
        copied_runtime.assistant_capture["status"] = "copied"

        assert copied_plugin._runtime is copied_runtime
        assert copied_runtime is not source_runtime
        assert copied_renderer is not source_runtime.renderer
        assert copied_runtime.assistant_capture is copied_plugin._ASSISTANT_CAPTURE
        assert copied_runtime.assistant_capture is not source_runtime.assistant_capture
        assert (
            copied_contracts.current_hook_callbacks().reasoning_enabled.__self__ is copied_runtime
        )
        assert current_hook_callbacks().reasoning_enabled.__self__ is source_runtime
        assert copied_commands._COMMAND_RUNTIME is copied_runtime
        assert commands._COMMAND_RUNTIME is source_runtime
        assert copied_runtime.patch_report is copied_report
        assert source_runtime.patch_report is source_report
        assert source_runtime.assistant_capture["status"] != "copied"
        assert len(context.hooks) == 12
        assert len(context.commands) == 12
    finally:
        for name in tuple(sys.modules):
            if name == "hermes_plugins" or name.startswith("hermes_plugins.b4_copy"):
                sys.modules.pop(name, None)


_CAPABILITIES = (
    "agent_callbacks",
    "adapter_context",
    "delegate_progress",
    "telegram_format",
    "telegram_topic_recovery",
    "command_menu",
    "gateway_interrupt",
    "gateway_display",
    "process_notifications",
    "compression_status",
    "compression_lifecycle",
)


@pytest.mark.parametrize("failed", [False, True], ids=["complete", "partial-fail-open"])
def test_register_stores_exact_complete_or_failed_report_and_continues(monkeypatch, failed):
    statuses = [PatchStatus(name, True, f"target:{name}") for name in _CAPABILITIES]
    if failed:
        statuses[3] = PatchStatus(
            "telegram_format",
            False,
            "target:telegram_format",
            PatchFailureCategory.INSTALL_FAILED,
            "expected test failure",
        )
    report = PatchInstallReport(tuple(statuses))
    monkeypatch.setattr(plugin, "install_monkeypatches_report", lambda callbacks: report)
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: {})
    context = RegistrationContext()

    result = plugin.register(context)

    assert result is None
    assert plugin._runtime.patch_report is report
    assert plugin._runtime.patch_report.statuses == tuple(statuses)
    assert plugin._runtime.patch_report.degraded is failed
    assert [name for name, _ in context.hooks] == [
        "pre_gateway_dispatch",
        "pre_tool_call",
        "post_tool_call",
        "post_llm_call",
        "on_session_reset",
        "on_session_finalize",
    ]
    assert [name for name, _, _ in context.commands] == [
        "progresstail",
        "progresstail-update",
        "progresstail-doctor",
        "progresstail-jobs",
        "progresstail-cleanup",
        "progresstail-demo",
    ]
