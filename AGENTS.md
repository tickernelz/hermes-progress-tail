# AGENTS.md

## Project

`hermes-progress-tail` is a Hermes Agent plugin that renders compact progress updates for gateway platforms. Keep fixes plugin-local unless there is hard evidence Hermes core must change.

## Commands

Use project-local tooling from the repository root. If you use a virtualenv, activate it first.

```bash
python -m pytest -q
ruff check .
ruff format --check .
bash -n install.sh
bash -n uninstall.sh
python -m compileall -q .
git diff --check
```

Targeted test examples:

```bash
python -m pytest tests/test_sticky_footer.py -q
python -m pytest tests/test_telegram_format_monkeypatch.py -q
```

## Structure

- `hermes_progress_tail/runtime/` — plugin hook handlers, runtime state, commands, demos.
- `hermes_progress_tail/hooks/` — monkeypatch installers grouped by target: agent, platform, Telegram, compression, delegates.
- `hermes_progress_tail/rendering/` — renderer, focused layout, sections, delivery, delegates, background jobs, formatting.
- `hermes_progress_tail/settings/` — config schema, dataclasses, loaders, platform overrides.
- `hermes_progress_tail/cli/` — installer, profile discovery, interactive CLI.
- `tests/` — pytest regression suite. Keep test files under 600 lines; split by topic when they grow.

## Style and boundaries

- Keep every tracked text/source file at or below 600 lines.
- Preserve compatibility wrappers such as `hermes_progress_tail.plugin`, `renderer`, `formatter`, `config`, and `monkeypatches`; tests and installed plugins import those paths.
- Prefer small modules with explicit imports over god files.
- Do not directly edit copied profile plugins under `~/.hermes/.../plugins`; change this repo, then run `install.sh` when installation is requested.
- Do not restart Hermes gateway unless explicitly requested.
- Never print, store, or commit secrets. Redaction boundaries are part of product behavior.

## Testing expectations

- Add or update regression tests for behavior changes.
- For renderer, Telegram formatting, installer, config, monkeypatch, and runtime hook changes, run the full suite before claiming completion.
- For MarkdownV2/Telegram rendering, raw renderer assertions are not enough; test through the Telegram monkeypatch/formatter boundary when styling is affected.
- For installer/config changes, keep dataclasses, parser defaults, installer defaults, README examples, and config-contract tests in sync.

## Release/install notes

- Version surfaces: `pyproject.toml`, `plugin.yaml`, `hermes_progress_tail/__init__.py`, `hermes_progress_tail/runtime/plugin.py`, `install.sh`, `uninstall.sh`, README snippets, and version-sensitive tests.
- Release flow is direct `main` when requested: implementation commit, version-only release commit, annotated tag, push `main` plus exact tag, verify CI and GitHub Release.
- Install to all profiles without restart:

```bash
HPT_INTERACTIVE=0 \
HPT_SOURCE_DIR="$(pwd)" \
HPT_ALL_PROFILES=1 \
bash install.sh
```
