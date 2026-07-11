from types import SimpleNamespace

from hermes_progress_tail.config import load_settings
from hermes_progress_tail.hooks.install_report import (
    PatchFailureCategory,
    PatchInstallReport,
    PatchStatus,
)
from hermes_progress_tail.runtime import commands

EXPECTED = (
    ("agent_callbacks", "run_agent.AIAgent.__init__+_fire_reasoning_delta"),
    (
        "adapter_context",
        "gateway.platforms.base.BasePlatformAdapter.set_message_handler+handle_message",
    ),
    ("delegate_progress", "tools.delegate_tool._build_child_progress_callback"),
    ("telegram_format", "TelegramAdapter.edit_message"),
    ("telegram_topic_recovery", "gateway.run.GatewayRunner._recover_telegram_topic_thread_id"),
    ("command_menu", "hermes_cli.commands.telegram_menu_commands"),
    ("gateway_interrupt", "gateway.run.GatewayRunner._interrupt_and_clear_session"),
    ("gateway_display", "gateway.display_config.resolve_display_setting"),
    ("process_notifications", "tools.process_registry.format_process_notification"),
    ("compression_status", "run_agent.AIAgent._emit_status"),
    ("compression_lifecycle", "run_agent.AIAgent._compress_context"),
)


def _runtime(report):
    renderer = SimpleNamespace(
        settings=load_settings({"progress_tail": {"tools": {"timestamp": False}}}), sessions={}
    )
    return SimpleNamespace(
        get_renderer=lambda: renderer,
        assistant_capture={},
        patch_report=report,
        load_runtime_config=lambda: {},
    )


def _output(monkeypatch, report, command="status"):
    monkeypatch.setattr(commands, "_COMMAND_RUNTIME", _runtime(report))
    monkeypatch.setattr(commands, "_COMMAND_VERSION", "9.8.7")
    monkeypatch.setattr(commands, "_latest_release_info", lambda: None)
    return commands._command(command)


def test_status_reports_exact_healthy_and_degraded_counts(monkeypatch):
    healthy = PatchInstallReport(
        tuple(PatchStatus(name, True, target) for name, target in EXPECTED)
    )
    assert "hooks=healthy installed=11/11" in _output(monkeypatch, healthy)

    partial = PatchInstallReport(
        (PatchStatus(EXPECTED[0][0], True, EXPECTED[0][1]), PatchStatus("future_hook", True, "x"))
    )
    output = _output(monkeypatch, partial)
    assert "hooks=degraded installed=1/11" in output
    assert "future_hook" not in output


def test_empty_doctor_synthesizes_all_absent_rows_in_fixed_order(monkeypatch):
    output = _output(monkeypatch, PatchInstallReport(), "doctor")
    assert "hooks=degraded installed=0/11" in output
    rows = [line for line in output.splitlines() if line.startswith("hook ")]
    assert rows == [
        f"hook {name}: target_api_missing target={target} reason=status absent from patch report"
        for name, target in EXPECTED
    ]


def test_doctor_only_lists_expected_failures_and_safely_bounds_reason(monkeypatch):
    secret = "token-super-secret"
    report = PatchInstallReport(
        (
            PatchStatus(EXPECTED[0][0], True, EXPECTED[0][1]),
            PatchStatus(
                EXPECTED[1][0],
                False,
                EXPECTED[1][1],
                PatchFailureCategory.INSTALL_FAILED,
                f"Traceback password={secret} " + "x" * 500,
            ),
            PatchStatus(
                "future_hook",
                False,
                "unknown.target",
                PatchFailureCategory.IMPORT_UNAVAILABLE,
                "bad",
            ),
        )
    )
    output = _output(monkeypatch, report, "doctor")
    rows = [line for line in output.splitlines() if line.startswith("hook ")]
    assert rows[0].startswith(
        "hook adapter_context: install_failed target=gateway.platforms.base.BasePlatformAdapter"
    )
    assert secret not in output
    assert "Traceback" not in output
    assert "future_hook" not in output
    assert len(rows[0].split(" reason=", 1)[1]) <= 240
    assert [row.split(":", 1)[0][5:] for row in rows] == [name for name, _ in EXPECTED[1:]]


def test_direct_commands_have_fail_open_runtime_port_and_config_loader():
    assert commands._COMMAND_RUNTIME.get_renderer() is not None
    assert isinstance(commands._COMMAND_RUNTIME.patch_report, PatchInstallReport)
    assert isinstance(commands._COMMAND_RUNTIME.load_runtime_config(), dict)
