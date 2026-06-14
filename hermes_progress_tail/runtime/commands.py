from __future__ import annotations

import json
import re
import time
import urllib.request

from ..settings.config import find_retired_config_keys, find_unknown_config_keys
from ..utils.redaction import redact_text
from .config_runtime import (
    _background_job_config_warnings,
    _builtin_interim_conflict,
    _builtin_reasoning_conflict,
    _core_notifier_conflict,
    _core_notifier_conflict_warning,
    _interim_conflict_warning,
    _reasoning_conflict_warning,
)
from .demo import _demo_command

_GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/tickernelz/hermes-progress-tail/releases/latest"
)
_LATEST_RELEASE_CACHE: dict[str, object] = {"checked_at": 0.0, "info": None}


def _latest_release_info(timeout: float = 1.5) -> dict[str, str] | None:
    now = time.time()
    cached_at = float(_LATEST_RELEASE_CACHE.get("checked_at") or 0.0)
    if now - cached_at < 300:
        cached = _LATEST_RELEASE_CACHE.get("info")
        return cached if isinstance(cached, dict) else None
    info: dict[str, str] | None = None
    try:
        request = urllib.request.Request(
            _GITHUB_LATEST_RELEASE_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "hermes-progress-tail-status",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if isinstance(payload, dict):
            tag_name = str(payload.get("tag_name") or "").strip()
            html_url = str(payload.get("html_url") or "").strip()
            if tag_name:
                info = {"tag_name": tag_name, "html_url": html_url}
    except Exception:
        info = None
    _LATEST_RELEASE_CACHE["checked_at"] = now
    _LATEST_RELEASE_CACHE["info"] = info
    return info


def _version_parts(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+(?:\.\d+){0,3})", str(value or ""))
    if not match:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _is_newer_version(current: str, latest: str) -> bool:
    current_parts = _version_parts(current)
    latest_parts = _version_parts(latest)
    if not current_parts or not latest_parts:
        return False
    width = max(len(current_parts), len(latest_parts))
    return latest_parts + (0,) * (width - len(latest_parts)) > current_parts + (0,) * (
        width - len(current_parts)
    )


def _markdown_table(rows: list[tuple[str, str]]) -> str:
    lines = ["| Field | Value |", "|:--|:--|"]
    for key, value in rows:
        safe_value = str(value).replace("|", "\\|")
        lines.append(f"| {key} | {safe_value} |")
    return "\n".join(lines)


def _status_markdown(
    *,
    version: str,
    runtime_rows: list[str],
    plugin_state: str,
    active_sessions: int,
    latest_release: dict[str, str] | None,
    doctor: bool,
) -> str:
    blocks = [
        "## Hermes Progress Tail",
        _markdown_table(
            [
                ("Version", f"`{version}`"),
                ("Plugin", plugin_state),
                ("Active sessions", str(active_sessions)),
            ]
        ),
    ]
    if not doctor and latest_release:
        latest_tag = str(latest_release.get("tag_name") or "").strip()
        if latest_tag and _is_newer_version(version, latest_tag):
            latest_url = str(latest_release.get("html_url") or "").strip()
            release_line = f"\n\nRelease: {latest_url}" if latest_url else ""
            blocks.append(
                f"## Update available\n\nNew version: v{version} → {latest_tag}{release_line}"
            )
    label = "Doctor" if doctor else "Runtime"
    blocks.append(f"## {label}\n\n```text\n" + "\n".join(runtime_rows).strip() + "\n```")
    return "\n\n".join(blocks).strip()


def _command(raw_args: str = "") -> str:
    args = (raw_args or "").strip().lower()
    from . import plugin as runtime_plugin

    renderer = runtime_plugin._get_renderer()
    if args in {"jobs", "jobs all"}:
        include_all = args == "jobs all"
        lines = [
            f"background_jobs={'enabled' if renderer.settings.background_jobs.enabled else 'disabled'}"
        ]
        for sid, ctx in renderer.sessions.items():
            for process_id in ctx.background_order:
                job = ctx.background_jobs.get(process_id)
                if job is None:
                    continue
                if not include_all and job.status != "running":
                    continue
                command = redact_text(job.command or process_id)
                lines.append(
                    f"{process_id} {job.status} exit={job.exit_code} session={ctx.session_key or sid} {command}"
                )
        return "\n".join(lines)
    if args in {"", "status", "doctor"}:
        active = len(renderer.sessions)
        monkeypatch_active = False
        delegate_monkeypatch_active = False
        try:
            from run_agent import AIAgent

            monkeypatch_active = bool(getattr(AIAgent, "_hermes_progress_tail_patched", False))
        except Exception:
            monkeypatch_active = False
        try:
            from tools import delegate_tool

            delegate_monkeypatch_active = bool(
                getattr(delegate_tool, "_hermes_progress_tail_delegate_patched", False)
            )
        except Exception:
            delegate_monkeypatch_active = False
        settings = renderer.settings
        runtime_config = runtime_plugin._load_runtime_config()
        display = (
            runtime_config.get("display") if isinstance(runtime_config.get("display"), dict) else {}
        )
        plugins = (
            runtime_config.get("plugins") if isinstance(runtime_config.get("plugins"), dict) else {}
        )
        enabled_plugins = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
        agent_config = (
            runtime_config.get("agent") if isinstance(runtime_config.get("agent"), dict) else {}
        )
        capture_at = float(runtime_plugin._ASSISTANT_CAPTURE.get("updated_at") or 0.0)
        capture_when = (
            time.strftime("%H:%M:%S", time.localtime(capture_at)) if capture_at else "never"
        )
        plugin_state = "enabled" if "hermes-progress-tail" in enabled_plugins else "not listed"
        lines = [
            f"hermes-progress-tail {runtime_plugin.VERSION}",
            f"plugin={plugin_state}",
            f"sessions={active}",
            f"agent.gateway_notify_interval={agent_config.get('gateway_notify_interval', '<default:180>')}",
            f"tools={'enabled' if settings.tools.enabled else 'disabled'} lines={settings.tools.lines} completed={settings.tools.show_completed} duration={settings.tools.show_duration} timestamp={settings.tools.timestamp_format if settings.tools.timestamp else 'off'}",
            f"todo=sticky:{settings.todo.sticky} hide_tool_line:{settings.todo.hide_tool_line}",
            f"patch=detail:{settings.patch.detail} preview_chars:{settings.patch.preview_chars} max_files:{settings.patch.max_files}",
            f"assistant={'enabled' if settings.assistant.enabled else 'disabled'} max_lines={settings.assistant.max_lines} max_chars={settings.assistant.max_chars}",
            f"assistant_capture={runtime_plugin._ASSISTANT_CAPTURE.get('status', 'never')} already_streamed={runtime_plugin._ASSISTANT_CAPTURE.get('already_streamed', False)} session={runtime_plugin._ASSISTANT_CAPTURE.get('session_id') or '-'} key_present={runtime_plugin._ASSISTANT_CAPTURE.get('session_key_present', False)} at={capture_when}",
            f"reasoning={'enabled' if settings.reasoning.enabled else 'disabled'} max_lines={settings.reasoning.max_lines} max_chars={settings.reasoning.max_chars}",
            "reasoning_sources=structured_reasoning,inline_think,provider_delimiters",
            f"delegates={'enabled' if settings.delegates.enabled else 'disabled'} max={settings.delegates.max_delegates} lines={settings.delegates.lines_per_delegate} ttl={settings.delegates.completed_ttl_seconds}s thinking={settings.delegates.thinking}",
            f"background_jobs={'enabled' if settings.background_jobs.enabled else 'disabled'} list_running={settings.background_jobs.list_running} show_completed={settings.background_jobs.show_completed} max={settings.background_jobs.max_jobs} ttl={settings.background_jobs.completed_ttl_seconds}s head={settings.background_jobs.head_lines} tail={settings.background_jobs.tail_lines} update={settings.background_jobs.update_interval_seconds}s suppress_native_notify={settings.background_jobs.suppress_native_notify} suppress_watch={settings.background_jobs.suppress_watch_notifications}",
            f"footer={'enabled' if settings.footer.enabled else 'disabled'} density:{settings.footer.density} max_path_chars:{settings.footer.max_path_chars}",
            f"telegram=rich:{settings.telegram.rich_messages} table:{settings.telegram.verification_table} thinking:{settings.telegram.thinking_blocks} max_table_rows:{settings.telegram.max_table_rows} compact_success:{settings.telegram.compact_success} max_detail_items:{settings.telegram.max_detail_items}",
            f"renderer=mode:{settings.renderer.mode} strategy:{settings.renderer.strategy} style:{settings.renderer.style} density:{settings.renderer.density} edit_interval:{settings.renderer.edit_interval} agent_label:{settings.renderer.agent_label or '-'}",
            f"display.tool_progress={display.get('tool_progress', '<unset>')}",
            f"display.show_reasoning={display.get('show_reasoning', '<unset>')}",
            f"monkeypatch={monkeypatch_active}",
            f"delegate_monkeypatch={delegate_monkeypatch_active}",
        ]
        if args == "doctor":
            if display.get("tool_progress") != "off":
                lines.append("warning: display.tool_progress is not off; progress may duplicate")
            if _builtin_interim_conflict(runtime_config):
                lines.append(_interim_conflict_warning())
            if _builtin_reasoning_conflict(runtime_config):
                lines.append(_reasoning_conflict_warning())
            if _core_notifier_conflict(runtime_config):
                lines.append(_core_notifier_conflict_warning())
            lines.extend(_background_job_config_warnings(settings))
            for key in find_retired_config_keys(runtime_config):
                lines.append(
                    f"warning: retired config key {key}; remove it from progress_tail config"
                )
            for key in find_unknown_config_keys(runtime_config):
                lines.append(f"warning: unknown config key {key}; check for typos or stale docs")
            for sid, ctx in renderer.sessions.items():
                label = ctx.session_key or sid
                lines.append(
                    f"session {label}: strategy={ctx.strategy} disabled={ctx.disabled} events={ctx.total_events}"
                )
                if ctx.downgrade_reason:
                    lines.append(f"session {label}: downgraded={redact_text(ctx.downgrade_reason)}")
                if ctx.last_error:
                    lines.append(f"session {label}: last_error={redact_text(ctx.last_error)}")
                if ctx.last_assistant_at:
                    when = time.strftime("%H:%M:%S", time.localtime(ctx.last_assistant_at))
                    lines.append(
                        f"session {label}: assistant chars={ctx.last_assistant_chars} at={when}"
                    )
                if ctx.last_reasoning_source:
                    when = time.strftime("%H:%M:%S", time.localtime(ctx.last_reasoning_at))
                    lines.append(
                        f"session {label}: last_reasoning source={ctx.last_reasoning_source} chars={ctx.last_reasoning_chars} at={when}"
                    )
        else:
            if _builtin_interim_conflict(runtime_config):
                lines.append(_interim_conflict_warning())
            if _builtin_reasoning_conflict(runtime_config):
                lines.append(_reasoning_conflict_warning())
            if _core_notifier_conflict(runtime_config):
                lines.append(_core_notifier_conflict_warning())
        latest_release = None if args == "doctor" else _latest_release_info()
        return _status_markdown(
            version=runtime_plugin.VERSION,
            runtime_rows=lines,
            plugin_state=plugin_state,
            active_sessions=active,
            latest_release=latest_release,
            doctor=args == "doctor",
        )
    if args in {"test", "demo", "demo plain", "demo failed"}:
        return _demo_command(plain=args == "demo plain", failed=args == "demo failed")
    return "Usage: /progresstail status | doctor | jobs [all] | demo [plain|failed]"
