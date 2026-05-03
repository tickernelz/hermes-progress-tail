# Release notes

## v0.1.1

- Add GitHub Actions CI for Ruff, tests, compile checks, shell syntax, and whitespace checks.
- Pin README curl install/uninstall examples and script defaults to `v0.1.1` for stable installs.
- Merge newly introduced default config keys into existing `progress_tail` configs during install without overwriting user values.
- Improve `/progresstail status` with version, tools, reasoning, renderer, sessions, monkeypatch, and warning details.
- Add `renderer.style: plain` for no-emoji progress rendering.
- Keep sticky todo visible while hiding the duplicate `todo` tool line by default.

## v0.1.0

- Initial standalone Hermes gateway plugin for compact tool and reasoning progress tails.
- Add curl-friendly install/uninstall scripts.
- Add sticky todo progress and compact `[HH:MM]` timestamps.
- Warn when plugin reasoning and Hermes built-in `display.show_reasoning` are enabled together.
- Add Ruff/pre-commit config and regression tests.
