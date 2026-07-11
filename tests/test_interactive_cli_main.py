from types import SimpleNamespace

import pytest

from hermes_progress_tail.cli import installer, interactive


def result(*messages: str):
    return SimpleNamespace(messages=list(messages))


def test_main_install_forwards_noninteractive_options_exactly(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(
        installer,
        "install_many",
        lambda *args, **kwargs: calls.append((args, kwargs)) or result("installed"),
    )
    monkeypatch.setattr(
        installer,
        "TELEGRAM_FLOOD_SAFE_CONFIG",
        {"renderer": {"strategy": "snapshot"}, "no_edit": {"interval_seconds": 60}},
    )
    home = tmp_path / "home"
    source = tmp_path / "source"

    status = interactive.main(
        [
            "install",
            "--hermes-home",
            str(home),
            "--source-dir",
            str(source),
            "--profile",
            "work,personal",
            "--native-gateway-suppress",
            "--dry-run",
            "--telegram-flood-safe",
            "--enable-tools",
            "off",
            "--enable-delegates",
            "on",
            "--enable-todo",
            "off",
            "--enable-reasoning",
            "on",
            "--renderer-style",
            "plain",
            "--renderer-density",
            "compact",
        ]
    )

    assert status == 0
    assert calls == [
        (
            (home, source),
            {
                "profiles": ["work", "personal"],
                "all_profiles": False,
                "set_display_off": True,
                "dry_run": True,
                "feature_overrides": {
                    "renderer": {"strategy": "snapshot", "style": "plain", "density": "compact"},
                    "no_edit": {"interval_seconds": 60},
                    "tools": {"enabled": False},
                    "delegates": {"enabled": True},
                    "reasoning": {"enabled": True},
                    "todo": {"sticky": False},
                },
                "force_default_config": False,
            },
        )
    ]
    assert capsys.readouterr().out == "installed\n"


def test_main_uninstall_forwards_exact_options(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        installer,
        "uninstall_many",
        lambda *args, **kwargs: calls.append((args, kwargs)) or result(),
    )
    home = tmp_path / "home"

    assert (
        interactive.main(["uninstall", "--hermes-home", str(home), "--all-profiles", "--dry-run"])
        == 0
    )
    assert calls == [((home,), {"profiles": None, "all_profiles": True, "dry_run": True})]


def test_interactive_install_uses_prompt_file_and_closes_it(monkeypatch, tmp_path):
    prompt_path = tmp_path / "answers.txt"
    prompt_path.write_text("all\ndefault\n", encoding="utf-8")
    observed = {}

    def options(home, prompt_stream):
        observed["home"] = home
        observed["stream"] = prompt_stream
        return ["work"], False, True, {"tools": {"enabled": False}}, True

    calls = []
    monkeypatch.setattr(interactive, "_interactive_install_options", options)
    monkeypatch.setattr(
        installer, "install_many", lambda *args, **kwargs: calls.append((args, kwargs)) or result()
    )
    home = tmp_path / "home"

    assert (
        interactive.main(
            [
                "install",
                "--hermes-home",
                str(home),
                "--prompt-input",
                str(prompt_path),
                "--interactive",
            ]
        )
        == 0
    )
    assert observed["home"] == home.resolve()
    assert observed["stream"].closed
    assert calls[0][1] == {
        "profiles": ["work"],
        "all_profiles": False,
        "set_display_off": True,
        "dry_run": False,
        "feature_overrides": {"tools": {"enabled": False}},
        "force_default_config": True,
    }


def test_interactive_uninstall_routes_profile_selection(monkeypatch, tmp_path):
    selected = []
    monkeypatch.setattr(
        interactive,
        "_select_profiles_interactive",
        lambda home, prompt_stream, *, action: (
            selected.append((home, prompt_stream, action)) or (["work"], False)
        ),
    )
    calls = []
    monkeypatch.setattr(
        installer,
        "uninstall_many",
        lambda *args, **kwargs: calls.append((args, kwargs)) or result(),
    )
    home = tmp_path / "home"

    assert interactive.main(["uninstall", "--hermes-home", str(home), "--interactive"]) == 0
    assert selected[0][0] == home.resolve()
    assert selected[0][2] == "uninstall"
    assert calls == [((home,), {"profiles": ["work"], "all_profiles": False, "dry_run": False})]


def test_missing_prompt_file_returns_two(tmp_path, capsys):
    missing = tmp_path / "missing.txt"
    assert interactive.main(["install", "--interactive", "--prompt-input", str(missing)]) == 2
    assert capsys.readouterr().err.startswith(f"error: cannot open prompt input {missing}:")


@pytest.mark.parametrize("error", [EOFError("ended"), ValueError("malformed")])
def test_interactive_prompt_errors_close_file_and_return_two(monkeypatch, tmp_path, capsys, error):
    prompt_path = tmp_path / "answers.txt"
    prompt_path.write_text("answer\n", encoding="utf-8")
    observed = {}

    def fail(_home, prompt_stream):
        observed["stream"] = prompt_stream
        raise error

    monkeypatch.setattr(interactive, "_interactive_install_options", fail)

    assert interactive.main(["install", "--interactive", "--prompt-input", str(prompt_path)]) == 2
    assert observed["stream"].closed
    assert capsys.readouterr().err == f"error: {error}\n"


def test_installer_value_error_returns_two(monkeypatch, capsys):
    monkeypatch.setattr(
        installer,
        "install_many",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad config")),
    )

    assert interactive.main(["install"]) == 2
    assert capsys.readouterr().err == "error: bad config\n"


@pytest.mark.parametrize(
    ("action", "installer_name"),
    [("install", "install_many"), ("uninstall", "uninstall_many")],
)
def test_installer_value_error_closes_prompt_file(
    monkeypatch, tmp_path, capsys, action, installer_name
):
    prompt_path = tmp_path / "answers.txt"
    prompt_path.write_text("answer\n", encoding="utf-8")
    observed = {}

    def select(_home, prompt_stream, *, action):
        observed["stream"] = prompt_stream
        return ["work"], False

    def install_options(_home, prompt_stream):
        observed["stream"] = prompt_stream
        return [], False, False, {}, False

    monkeypatch.setattr(interactive, "_select_profiles_interactive", select)
    monkeypatch.setattr(interactive, "_interactive_install_options", install_options)
    monkeypatch.setattr(
        installer,
        installer_name,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad config")),
    )

    status = interactive.main([action, "--interactive", "--prompt-input", str(prompt_path)])

    assert status == 2
    assert capsys.readouterr().err == "error: bad config\n"
    assert observed["stream"].closed
