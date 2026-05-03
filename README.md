# hermes-progress-tail

![hermes-progress-tail banner](assets/hermes-progress-tail-banner.png)

Compact Hermes gateway plugin for live progress tails.

## What it does

- Shows the latest tool calls in one compact progress bubble.
- Shows live reasoning/thinking tail when Hermes exposes reasoning deltas.
- Keeps editable platforms tidy by updating one message instead of spamming chat.
- Falls back conservatively on no-edit platforms.
- Redacts common secrets before rendering progress.
- Disables Hermes built-in `display.show_reasoning` during install when plugin reasoning is enabled, to avoid duplicate final output.

## Before / after

![Before and after progress tail](assets/hermes-progress-tail-before-after.png)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-progress-tail/v0.1.3/install.sh | bash
```

Dry-run:

```bash
curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-progress-tail/v0.1.3/install.sh | env HPT_DRY_RUN=1 bash
```

Uninstall:

```bash
curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-progress-tail/v0.1.3/uninstall.sh | bash
```

Local install:

```bash
python -m hermes_progress_tail.installer install --hermes-home ~/.hermes --set-display-off
```

Restart Hermes manually after install/uninstall:

```text
/restart
```

## Expected config

```yaml
plugins:
  enabled:
    - hermes-progress-tail

display:
  tool_progress: off
  show_reasoning: false

progress_tail:
  enabled: true
  tools:
    enabled: true
    lines: 3
    show_completed: false
    show_duration: true
    timestamp: true
    timestamp_format: "%H:%M"
  todo:
    sticky: true
    hide_tool_line: true
  patch:
    detail: smart # off|path|smart|stats
    preview_chars: 48
    max_files: 3
  renderer:
    style: emoji # emoji|plain
    density: normal # compact|normal|debug
  reasoning:
    enabled: true
    max_lines: 3
    max_chars: 600
```

## Commands

```text
/progresstail status
/progresstail doctor
/progresstail demo
/progresstail demo plain
/progresstail demo failed
```

## Development

```bash
python -m pip install -e '.[dev]'
pre-commit install
pre-commit run --all-files
python -m pytest
```

Useful direct checks:

```bash
ruff format .
ruff check .
python -m compileall -q .
git diff --check
```

## Notes

Reasoning tail uses guarded plugin-local monkeypatches around Hermes `AIAgent` internals. Hermes source files are not modified. If upstream internals change, the plugin should fail closed and `/progresstail status` should show the issue.
