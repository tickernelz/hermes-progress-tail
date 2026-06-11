from __future__ import annotations

import time

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
        lines = [
            f"hermes-progress-tail {runtime_plugin.VERSION}",
            f"plugin={'enabled' if 'hermes-progress-tail' in enabled_plugins else 'not listed'}",
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
        return "\n".join(lines)
    if args in {"test", "demo", "demo plain", "demo failed"}:
        return _demo_command(plain=args == "demo plain", failed=args == "demo failed")
    return "Usage: /progresstail status | doctor | jobs [all] | demo [plain|failed]"
