from io import StringIO
from types import SimpleNamespace

import pytest

from hermes_progress_tail.cli import interactive


def stream(*answers: str) -> StringIO:
    return StringIO("".join(f"{answer}\n" for answer in answers))


def test_simple_overrides_forward_each_core_answer_exactly():
    overrides = interactive._simple_install_overrides(
        stream("no", "yes", "0", "on", "plain", "debug")
    )

    assert overrides == {
        "tools": {"enabled": False},
        "delegates": {"enabled": True},
        "todo": {"sticky": False},
        "reasoning": {"enabled": True},
        "renderer": {"style": "plain", "density": "debug"},
    }


ADVANCED_ANSWERS = {
    "Enable tool progress tail": "no",
    "Latest tool lines to keep": "7",
    "Tool preview max characters": "24",
    "Show completion status by replacing running tool lines": "no",
    "Show tool duration on completed/failed lines": "no",
    "Show compact timestamps on tool lines": "no",
    "Enable delegate_task/subagent progress": "no",
    "Maximum visible delegates": "6",
    "Timeline lines per delegate": "4",
    "Delegate title max characters": "12",
    "Delegate line max characters": "24",
    "Show delegate model names": "yes",
    "Show delegate tool count": "no",
    "Show delegate completion summary": "no",
    "Delegate thinking display": "summary",
    "Enable sticky todo section": "no",
    "Hide duplicate todo tool line": "no",
    "Maximum pending todo items shown": "8",
    "Maximum completed todo items shown": "9",
    "Maximum cancelled todo items shown": "10",
    "Todo item max characters": "10",
    "Enable reasoning/thinking tail": "no",
    "Reasoning max lines": "11",
    "Reasoning max characters": "80",
    "Reasoning minimum new characters before edit": "12",
    "Reasoning behavior on no-edit platforms": "snapshot",
    "Patch detail mode": "stats",
    "Patch preview max characters": "10",
    "Maximum patch files in summary": "13",
    "Renderer update strategy": "live_tail",
    "Renderer layout mode": "sectioned",
    "Renderer style": "plain",
    "Renderer density": "debug",
    "Minimum seconds between live edits": "0.25",
    "Stale session TTL seconds": "14",
    "Redact common secrets before rendering": "no",
    "Snapshot interval seconds": "15",
    "Minimum new events before snapshot": "16",
    "Send final snapshot summary": "no",
    "Maximum snapshots per turn": "17",
}


def test_advanced_overrides_route_named_nondefault_answers(monkeypatch):
    prompted = []

    def keyed_prompt(_stream, rendered):
        name = rendered.split(" [", 1)[0].split(" (", 1)[0]
        prompted.append(name)
        return ADVANCED_ANSWERS[name]

    monkeypatch.setattr(interactive, "_prompt", keyed_prompt)

    assert interactive._advanced_install_overrides(SimpleNamespace()) == {
        "tools": {
            "enabled": False,
            "lines": 7,
            "preview_length": 24,
            "show_completed": False,
            "show_duration": False,
            "timestamp": False,
            "timestamp_format": "%H:%M",
        },
        "delegates": {
            "enabled": False,
            "max_delegates": 6,
            "lines_per_delegate": 4,
            "max_goal_chars": 12,
            "max_line_chars": 24,
            "show_model": True,
            "show_tool_count": False,
            "show_completion": False,
            "thinking": "summary",
        },
        "todo": {
            "sticky": False,
            "hide_tool_line": False,
            "max_pending": 8,
            "max_completed": 9,
            "max_cancelled": 10,
            "max_item_chars": 10,
        },
        "reasoning": {
            "enabled": False,
            "max_lines": 11,
            "max_chars": 80,
            "min_update_chars": 12,
            "no_edit_strategy": "snapshot",
        },
        "patch": {"detail": "stats", "preview_chars": 10, "max_files": 13},
        "renderer": {
            "strategy": "live_tail",
            "mode": "sectioned",
            "style": "plain",
            "density": "debug",
            "edit_interval": 0.25,
            "stale_ttl_seconds": 14,
            "redact_secrets": False,
        },
        "no_edit": {
            "interval_seconds": 15,
            "min_new_events": 16,
            "final_summary": False,
            "max_snapshots_per_turn": 17,
        },
    }
    assert len(prompted) == len(set(prompted))
    assert set(prompted) == set(ADVANCED_ANSWERS)


@pytest.mark.parametrize(
    ("mode", "expected_overrides", "force_default"),
    [
        ("", {}, True),
        ("simple", {"kind": "simple"}, False),
        ("advanced", {"kind": "advanced"}, False),
    ],
)
def test_install_option_mode_routing(
    monkeypatch, tmp_path, mode, expected_overrides, force_default
):
    monkeypatch.setattr(
        interactive,
        "_select_profiles_interactive",
        lambda _home, _input: (["work"], False),
    )
    monkeypatch.setattr(interactive, "_simple_install_overrides", lambda _input: {"kind": "simple"})
    monkeypatch.setattr(
        interactive, "_advanced_install_overrides", lambda _input: {"kind": "advanced"}
    )

    result = interactive._interactive_install_options(tmp_path, stream(mode))

    assert result == (["work"], False, True, expected_overrides, force_default)
