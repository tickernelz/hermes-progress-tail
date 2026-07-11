from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from hermes_progress_tail.hooks import monkeypatches as mp
from hermes_progress_tail.hooks.contracts import current_hook_callbacks
from hermes_progress_tail.hooks.install_report import PatchFailureCategory

ROWS = (
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


def _callable_target(spec):
    return SimpleNamespace(**{member: (lambda *a, **k: None) for member in spec.members})


def _run(specs):
    old = mp._CAPABILITY_SPECS
    try:
        mp._CAPABILITY_SPECS = tuple(specs)
        return mp.install_monkeypatches_report(current_hook_callbacks())
    finally:
        mp._CAPABILITY_SPECS = old


def _specs(results):
    output = []
    for spec, result in zip(mp._CAPABILITY_SPECS, results, strict=True):

        def resolver(s=spec):
            return _callable_target(s)

        if isinstance(result, BaseException):

            def installer(*args, exc=result, **kwargs):
                raise exc
        else:

            def installer(*args, value=result, **kwargs):
                return value

        output.append(replace(spec, resolver=resolver, installer=installer))
    return output


def test_literal_capability_names_labels_and_all_installed_report():
    report = _run(_specs([True] * 11))
    assert [(s.name, s.target) for s in report.statuses] == list(ROWS)
    assert [s.installed for s in report.statuses] == [True] * 11
    assert report.any_installed is True
    assert report.degraded is False


def test_partial_and_none_installed_reports_have_all_literal_rows():
    partial = _run(_specs([True] + [False] * 10))
    none = _run(_specs([False] * 11))
    assert [(s.name, s.target) for s in partial.statuses] == list(ROWS)
    assert [s.installed for s in partial.statuses] == [True] + [False] * 10
    assert (partial.any_installed, partial.degraded) == (True, True)
    assert [(s.name, s.target) for s in none.statuses] == list(ROWS)
    assert (none.any_installed, none.degraded) == (False, True)


@pytest.mark.parametrize("index,row", tuple(enumerate(ROWS)), ids=[r[0] for r in ROWS])
def test_resolver_exception_is_import_unavailable_for_every_leaf(index, row):
    specs = list(_specs([True] * 11))

    def unavailable():
        raise ImportError("host unavailable")

    specs[index] = replace(specs[index], resolver=unavailable)
    status = _run(specs).statuses[index]
    assert (status.name, status.target, status.installed, status.failure_category) == (
        row[0],
        row[1],
        False,
        PatchFailureCategory.IMPORT_UNAVAILABLE,
    )


@pytest.mark.parametrize("index,row", tuple(enumerate(ROWS)), ids=[r[0] for r in ROWS])
def test_missing_callable_is_target_api_missing_for_every_leaf(index, row):
    specs = list(_specs([True] * 11))
    specs[index] = replace(specs[index], resolver=lambda: SimpleNamespace())
    status = _run(specs).statuses[index]
    assert (status.name, status.target, status.failure_category) == (
        row[0],
        row[1],
        PatchFailureCategory.TARGET_API_MISSING,
    )


@pytest.mark.parametrize(
    "result", [False, RuntimeError("mutation failed")], ids=["literal-false", "exception"]
)
@pytest.mark.parametrize("index,row", tuple(enumerate(ROWS)), ids=[r[0] for r in ROWS])
def test_mutation_failure_is_install_failed_for_every_leaf(index, row, result):
    values = [True] * 11
    values[index] = result
    status = _run(_specs(values)).statuses[index]
    assert (status.name, status.target, status.failure_category) == (
        row[0],
        row[1],
        PatchFailureCategory.INSTALL_FAILED,
    )


def test_callbacks_are_forwarded_only_to_callback_consuming_rows():
    callbacks = current_hook_callbacks()
    calls = []
    specs = []
    for spec in mp._CAPABILITY_SPECS:

        def installer(*args, _name=spec.name, **kwargs):
            calls.append((_name, args[0], kwargs.get("callbacks")))
            return True

        specs.append(
            replace(spec, resolver=lambda s=spec: _callable_target(s), installer=installer)
        )
    _run(specs)
    expected = {
        "telegram_topic_recovery",
        "command_menu",
        "gateway_display",
        "process_notifications",
    }
    assert [(name, cb is callbacks) for name, _, cb in calls] == [
        (name, name not in expected) for name, _ in ROWS
    ]
