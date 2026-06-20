from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from .profiles import _discover_profile_names, _parse_profile_list


def _prompt(input_stream: Any, prompt: str) -> str:
    print(prompt, end="", flush=True)
    line = input_stream.readline()
    if line == "":
        raise EOFError("interactive input ended unexpectedly")
    return line.strip()


def _confirm(prompt: str, default: bool = True, input_stream: Any = sys.stdin) -> bool:
    suffix = "Y/n" if default else "y/N"
    answer = _prompt(input_stream, f"{prompt} [{suffix}]: ").lower()
    if not answer:
        return default
    return answer in {"y", "yes", "1", "true", "on"}


def _prompt_int(
    prompt: str, default: int, input_stream: Any = sys.stdin, *, min_value: int = 1
) -> int:
    answer = _prompt(input_stream, f"{prompt} [{default}]: ")
    if not answer:
        return default
    try:
        value = int(answer)
    except ValueError as exc:
        raise ValueError(f"invalid integer for {prompt!r}: {answer}") from exc
    if value < min_value:
        raise ValueError(f"{prompt!r} must be >= {min_value}")
    return value


def _prompt_float(
    prompt: str, default: float, input_stream: Any = sys.stdin, *, min_value: float = 0.0
) -> float:
    answer = _prompt(input_stream, f"{prompt} [{default:g}]: ")
    if not answer:
        return default
    try:
        value = float(answer)
    except ValueError as exc:
        raise ValueError(f"invalid number for {prompt!r}: {answer}") from exc
    if value <= min_value:
        raise ValueError(f"{prompt!r} must be > {min_value:g}")
    return value


def _prompt_choice(
    prompt: str, choices: tuple[str, ...], default: str, input_stream: Any = sys.stdin
) -> str:
    answer = _prompt(input_stream, f"{prompt} ({'|'.join(choices)}) [{default}]: ").strip().lower()
    if not answer:
        return default
    if answer not in choices:
        raise ValueError(
            f"invalid choice for {prompt!r}: {answer}. Expected one of: {', '.join(choices)}"
        )
    return answer


def _prompt_setup_mode(input_stream: Any = sys.stdin) -> str:
    answer = (
        _prompt(
            input_stream,
            "Setup mode (default|simple|advance/advanced) [default]: ",
        )
        .strip()
        .lower()
    )
    if not answer:
        return "default"
    aliases = {
        "d": "default",
        "s": "simple",
        "a": "advance",
        "adv": "advance",
        "advanced": "advance",
    }
    answer = aliases.get(answer, answer)
    if answer not in {"default", "simple", "advance"}:
        raise ValueError(
            "invalid choice for 'Setup mode': "
            f"{answer}. Expected one of: default, simple, advance, advanced"
        )
    return answer


def _select_profiles_interactive(
    hermes_home: Path, input_stream: Any = sys.stdin, *, action: str = "install"
) -> tuple[list[str] | None, bool]:
    discovered = _discover_profile_names(hermes_home)
    if not discovered:
        print("No Hermes profiles found; installing to default only.")
        return ["default"], False
    print("Available targets:")
    print("  0) default")
    for idx, name in enumerate(discovered, start=1):
        print(f"  {idx}) {name}")
    print("  a) all")
    raw = _prompt(
        input_stream,
        f"{action.title()} target profiles (comma-separated numbers/names, default: all): ",
    )
    if not raw or raw.lower() in {"a", "all"}:
        return None, True
    selected: list[str] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        if token.isdigit():
            idx = int(token)
            if idx == 0:
                selected.append("default")
            elif 1 <= idx <= len(discovered):
                selected.append(discovered[idx - 1])
            else:
                raise ValueError(f"invalid profile selection index: {token}")
        else:
            selected.append("default" if token in {"base", "main"} else token)
    return selected or ["default"], False


def _simple_install_overrides(input_stream: Any = sys.stdin) -> dict[str, Any]:
    print("\nSimple setup")
    return {
        "tools": {"enabled": _confirm("Enable tool progress tail", True, input_stream)},
        "delegates": {
            "enabled": _confirm("Enable delegate_task/subagent progress", True, input_stream)
        },
        "todo": {"sticky": _confirm("Enable sticky todo section", True, input_stream)},
        "reasoning": {"enabled": _confirm("Enable reasoning/thinking tail", True, input_stream)},
        "renderer": {
            "style": _prompt_choice("Renderer style", ("emoji", "plain"), "emoji", input_stream),
            "density": _prompt_choice(
                "Renderer density",
                ("compact", "normal", "verbose", "debug"),
                "verbose",
                input_stream,
            ),
        },
    }


def _advanced_install_overrides(input_stream: Any = sys.stdin) -> dict[str, Any]:
    print("\nTool progress")
    tools = {
        "enabled": _confirm("Enable tool progress tail", True, input_stream),
        "lines": _prompt_int("Latest tool lines to keep", 3, input_stream),
        "preview_length": _prompt_int(
            "Tool preview max characters", 120, input_stream, min_value=24
        ),
        "show_completed": _confirm(
            "Show completion status by replacing running tool lines", True, input_stream
        ),
        "show_duration": _confirm(
            "Show tool duration on completed/failed lines", True, input_stream
        ),
        "timestamp": _confirm("Show compact timestamps on tool lines", True, input_stream),
        "timestamp_format": "%H:%M",
    }

    print("\nDelegate/subagent progress")
    delegates = {
        "enabled": _confirm("Enable delegate_task/subagent progress", True, input_stream),
        "max_delegates": _prompt_int("Maximum visible delegates", 4, input_stream),
        "lines_per_delegate": _prompt_int("Timeline lines per delegate", 2, input_stream),
        "max_goal_chars": _prompt_int(
            "Delegate title max characters", 48, input_stream, min_value=12
        ),
        "max_line_chars": _prompt_int(
            "Delegate line max characters", 120, input_stream, min_value=24
        ),
        "show_model": _confirm("Show delegate model names", False, input_stream),
        "show_tool_count": _confirm("Show delegate tool count", True, input_stream),
        "show_completion": _confirm("Show delegate completion summary", True, input_stream),
        "thinking": _prompt_choice(
            "Delegate thinking display", ("off", "summary"), "off", input_stream
        ),
    }

    print("\nSticky Todo section")
    todo = {
        "sticky": _confirm("Enable sticky todo section", True, input_stream),
        "hide_tool_line": _confirm("Hide duplicate todo tool line", True, input_stream),
        "max_pending": _prompt_int("Maximum pending todo items shown", 3, input_stream),
        "max_completed": _prompt_int("Maximum completed todo items shown", 3, input_stream),
        "max_cancelled": _prompt_int("Maximum cancelled todo items shown", 2, input_stream),
        "max_item_chars": _prompt_int("Todo item max characters", 40, input_stream, min_value=10),
    }

    print("\nReasoning/thinking tail")
    reasoning = {
        "enabled": _confirm("Enable reasoning/thinking tail", True, input_stream),
        "max_lines": _prompt_int("Reasoning max lines", 3, input_stream),
        "max_chars": _prompt_int("Reasoning max characters", 600, input_stream, min_value=80),
        "min_update_chars": _prompt_int(
            "Reasoning minimum new characters before edit", 80, input_stream
        ),
        "no_edit_strategy": _prompt_choice(
            "Reasoning behavior on no-edit platforms",
            ("auto", "live_tail", "snapshot", "summary_only", "off"),
            "off",
            input_stream,
        ),
    }

    print("\nPatch formatter")
    patch = {
        "detail": _prompt_choice(
            "Patch detail mode", ("off", "path", "smart", "stats"), "smart", input_stream
        ),
        "preview_chars": _prompt_int(
            "Patch preview max characters", 48, input_stream, min_value=10
        ),
        "max_files": _prompt_int("Maximum patch files in summary", 3, input_stream),
    }

    print("\nRenderer")
    renderer = {
        "strategy": _prompt_choice(
            "Renderer update strategy",
            ("auto", "live_tail", "snapshot", "summary_only", "off"),
            "auto",
            input_stream,
        ),
        "mode": _prompt_choice(
            "Renderer layout mode", ("focused", "sectioned"), "focused", input_stream
        ),
        "style": _prompt_choice("Renderer style", ("emoji", "plain"), "emoji", input_stream),
        "density": _prompt_choice(
            "Renderer density", ("compact", "normal", "verbose", "debug"), "verbose", input_stream
        ),
        "edit_interval": _prompt_float("Minimum seconds between live edits", 1.5, input_stream),
        "stale_ttl_seconds": _prompt_int("Stale session TTL seconds", 900, input_stream),
        "redact_secrets": _confirm("Redact common secrets before rendering", True, input_stream),
    }

    print("\nNo-edit platform snapshots")
    no_edit = {
        "interval_seconds": _prompt_int("Snapshot interval seconds", 30, input_stream),
        "min_new_events": _prompt_int("Minimum new events before snapshot", 3, input_stream),
        "final_summary": _confirm("Send final snapshot summary", True, input_stream),
        "max_snapshots_per_turn": _prompt_int("Maximum snapshots per turn", 5, input_stream),
    }

    return {
        "tools": tools,
        "delegates": delegates,
        "todo": todo,
        "reasoning": reasoning,
        "patch": patch,
        "renderer": renderer,
        "no_edit": no_edit,
    }


def _interactive_install_options(
    hermes_home: Path, input_stream: Any = sys.stdin
) -> tuple[list[str] | None, bool, bool, dict[str, Any], bool]:
    print("hermes-progress-tail interactive installer")
    print("Press Enter to accept the recommended default shown in brackets.")
    profiles, all_profiles = _select_profiles_interactive(hermes_home, input_stream)
    print("\nSetup mode")
    print("  default: reset/apply recommended defaults without extra questions")
    print("  simple: ask only the core UX choices")
    print("  advance: ask every public config option")
    setup_mode = _prompt_setup_mode(input_stream)
    set_display_off = True
    force_default_config = setup_mode == "default"
    if setup_mode == "default":
        print("Applying recommended defaults.")
        overrides: dict[str, Any] = {}
    elif setup_mode == "simple":
        overrides = _simple_install_overrides(input_stream)
    else:
        overrides = _advanced_install_overrides(input_stream)
    return profiles, all_profiles, set_display_off, overrides, force_default_config


def main(argv: list[str] | None = None) -> int:
    from .installer import _default_source_dir, install_many, uninstall_many

    parser = argparse.ArgumentParser(prog="hermes_progress_tail")
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--hermes-home", default=os.getenv("HERMES_HOME", "~/.hermes"))
    parser.add_argument("--source-dir", default=str(_default_source_dir()))
    parser.add_argument(
        "--native-gateway-suppress",
        "--set-display-off",
        dest="native_gateway_suppress",
        action="store_true",
        help=(
            "Enable plugin-local gateway native display suppression. "
            "--set-display-off is a backwards-compatible alias."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--profile", action="append", default=[])
    parser.add_argument("--all-profiles", action="store_true")
    parser.add_argument("--interactive", "-i", action="store_true")
    parser.add_argument("--prompt-input", default="")
    parser.add_argument("--enable-tools", choices=["on", "off"])
    parser.add_argument("--enable-delegates", choices=["on", "off"])
    parser.add_argument("--enable-todo", choices=["on", "off"])
    parser.add_argument("--enable-reasoning", choices=["on", "off"])
    parser.add_argument("--renderer-style", choices=["emoji", "plain"])
    parser.add_argument("--renderer-density", choices=["compact", "normal", "verbose", "debug"])
    args = parser.parse_args(argv)
    hermes_home = Path(args.hermes_home)
    profiles = _parse_profile_list(args.profile)
    all_profiles = args.all_profiles
    set_display_off = args.native_gateway_suppress
    feature_overrides: dict[str, Any] = {}
    force_default_config = False
    prompt_stream = sys.stdin
    prompt_file = None
    if args.prompt_input:
        try:
            prompt_file = Path(args.prompt_input).open(encoding="utf-8")  # noqa: SIM115
            prompt_stream = prompt_file
        except OSError as exc:
            print(f"error: cannot open prompt input {args.prompt_input}: {exc}", file=sys.stderr)
            return 2
    try:
        if args.interactive and args.action == "install":
            profiles, all_profiles, set_display_off, feature_overrides, force_default_config = (
                _interactive_install_options(hermes_home.expanduser().resolve(), prompt_stream)
            )
        elif args.interactive and args.action == "uninstall":
            profiles, all_profiles = _select_profiles_interactive(
                hermes_home.expanduser().resolve(), prompt_stream, action="uninstall"
            )
    except (EOFError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        if prompt_file is not None:
            prompt_file.close()
        return 2
    if not (args.interactive and args.action in {"install", "uninstall"}):
        toggles = {
            "tools": args.enable_tools,
            "delegates": args.enable_delegates,
            "reasoning": args.enable_reasoning,
        }
        for section, value in toggles.items():
            if value:
                feature_overrides.setdefault(section, {})["enabled"] = value == "on"
        if args.enable_todo:
            feature_overrides.setdefault("todo", {})["sticky"] = args.enable_todo == "on"
        if args.renderer_style:
            feature_overrides.setdefault("renderer", {})["style"] = args.renderer_style
        if args.renderer_density:
            feature_overrides.setdefault("renderer", {})["density"] = args.renderer_density
    try:
        if args.action == "install":
            result = install_many(
                hermes_home,
                Path(args.source_dir),
                profiles=profiles,
                all_profiles=all_profiles,
                set_display_off=set_display_off,
                dry_run=args.dry_run,
                feature_overrides=feature_overrides,
                force_default_config=force_default_config,
            )
        else:
            result = uninstall_many(
                hermes_home,
                profiles=profiles,
                all_profiles=all_profiles,
                dry_run=args.dry_run,
            )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for msg in result.messages:
        print(msg)
    if prompt_file is not None:
        prompt_file.close()
    return 0
