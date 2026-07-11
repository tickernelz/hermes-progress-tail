from io import StringIO

import pytest

from hermes_progress_tail.cli import interactive


def stream(*answers: str) -> StringIO:
    return StringIO("".join(f"{answer}\n" for answer in answers))


def test_prompt_strips_input_and_reports_eof(capsys):
    assert interactive._prompt(stream("  value  "), "Question: ") == "value"
    assert capsys.readouterr().out == "Question: "
    with pytest.raises(EOFError, match="interactive input ended unexpectedly"):
        interactive._prompt(StringIO(), "Again: ")


@pytest.mark.parametrize(
    ("answer", "default", "expected"),
    [
        ("", True, True),
        ("", False, False),
        ("YES", False, True),
        ("on", False, True),
        ("no", True, False),
    ],
)
def test_confirm_defaults_and_recognized_answers(answer, default, expected):
    assert interactive._confirm("Continue", default, stream(answer)) is expected


@pytest.mark.parametrize(
    ("helper", "answer", "kwargs", "expected"),
    [
        (interactive._prompt_int, "", {"default": 3}, 3),
        (interactive._prompt_int, "7", {"default": 3}, 7),
        (interactive._prompt_float, "", {"default": 5.0}, 5.0),
        (interactive._prompt_float, "2.5", {"default": 5.0}, 2.5),
    ],
)
def test_numeric_prompts_accept_defaults_and_values(helper, answer, kwargs, expected):
    assert helper("Count", input_stream=stream(answer), **kwargs) == expected


@pytest.mark.parametrize(
    ("helper", "answer", "kwargs", "message"),
    [
        (interactive._prompt_int, "wat", {"default": 3}, "invalid integer for 'Count': wat"),
        (interactive._prompt_int, "2", {"default": 3, "min_value": 3}, "'Count' must be >= 3"),
        (interactive._prompt_float, "wat", {"default": 5.0}, "invalid number for 'Count': wat"),
        (interactive._prompt_float, "0", {"default": 5.0}, "'Count' must be > 0"),
    ],
)
def test_numeric_prompts_reject_invalid_or_out_of_range(helper, answer, kwargs, message):
    with pytest.raises(ValueError) as exc_info:
        helper("Count", input_stream=stream(answer), **kwargs)
    assert str(exc_info.value) == message


def test_choice_normalizes_values_and_rejects_unknown():
    choices = ("emoji", "plain")
    assert interactive._prompt_choice("Style", choices, "emoji", stream("")) == "emoji"
    assert interactive._prompt_choice("Style", choices, "emoji", stream(" PLAIN ")) == "plain"
    with pytest.raises(ValueError) as exc_info:
        interactive._prompt_choice("Style", choices, "emoji", stream("loud"))
    assert str(exc_info.value) == "invalid choice for 'Style': loud. Expected one of: emoji, plain"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("", "default"),
        ("d", "default"),
        ("s", "simple"),
        ("a", "advance"),
        ("adv", "advance"),
        ("advanced", "advance"),
    ],
)
def test_setup_mode_defaults_and_aliases(answer, expected):
    assert interactive._prompt_setup_mode(stream(answer)) == expected


def test_setup_mode_rejects_unknown_value():
    with pytest.raises(ValueError) as exc_info:
        interactive._prompt_setup_mode(stream("expert"))
    assert str(exc_info.value) == (
        "invalid choice for 'Setup mode': expert. Expected one of: default, simple, advance, advanced"
    )


def test_profile_selection_without_discovered_profiles_uses_default(tmp_path, capsys):
    assert interactive._select_profiles_interactive(tmp_path, stream()) == (["default"], False)
    assert capsys.readouterr().out == "No Hermes profiles found; installing to default only.\n"


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("", (None, True)),
        ("ALL", (None, True)),
        ("0,2", (["default", "work"], False)),
        ("1,base,main,custom", (["personal", "default", "default", "custom"], False)),
        (",,", (["default"], False)),
        ("-1,+1,1.0", (["-1", "+1", "1.0"], False)),
        ("1,,work,", (["personal", "work"], False)),
    ],
)
def test_profile_selection_variants(monkeypatch, tmp_path, answer, expected):
    monkeypatch.setattr(interactive, "_discover_profile_names", lambda _home: ["personal", "work"])
    assert interactive._select_profiles_interactive(tmp_path, stream(answer)) == expected


def test_profile_selection_rejects_out_of_range_index(monkeypatch, tmp_path):
    monkeypatch.setattr(interactive, "_discover_profile_names", lambda _home: ["work"])
    with pytest.raises(ValueError, match="invalid profile selection index: 2"):
        interactive._select_profiles_interactive(tmp_path, stream("2"), action="uninstall")
