from io import StringIO

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


ADVANCED_DEFAULTS = {
    "tools": {
        "enabled": True,
        "lines": 3,
        "preview_length": 120,
        "show_completed": True,
        "show_duration": True,
        "timestamp": True,
        "timestamp_format": "%H:%M",
    },
    "delegates": {
        "enabled": True,
        "max_delegates": 4,
        "lines_per_delegate": 2,
        "max_goal_chars": 48,
        "max_line_chars": 120,
        "show_model": False,
        "show_tool_count": True,
        "show_completion": True,
        "thinking": "off",
    },
    "todo": {
        "sticky": True,
        "hide_tool_line": True,
        "max_pending": 3,
        "max_completed": 3,
        "max_cancelled": 2,
        "max_item_chars": 40,
    },
    "reasoning": {
        "enabled": True,
        "max_lines": 3,
        "max_chars": 600,
        "min_update_chars": 300,
        "no_edit_strategy": "off",
    },
    "patch": {"detail": "smart", "preview_chars": 48, "max_files": 3},
    "renderer": {
        "strategy": "auto",
        "mode": "focused",
        "style": "emoji",
        "density": "normal",
        "edit_interval": 5.0,
        "stale_ttl_seconds": 900,
        "redact_secrets": True,
    },
    "no_edit": {
        "interval_seconds": 30,
        "min_new_events": 3,
        "final_summary": True,
        "max_snapshots_per_turn": 5,
    },
}


def test_advanced_overrides_apply_every_documented_default():
    answer_count = 40
    assert (
        interactive._advanced_install_overrides(stream(*([""] * answer_count))) == ADVANCED_DEFAULTS
    )


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
