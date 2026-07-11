from __future__ import annotations

import json
import re
import shlex
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from ..hooks.install_report import PatchInstallReport, safe_patch_reason
from ..hooks.monkeypatches import _CAPABILITY_SPECS
from ..hooks.platform import _legacy_global_suppression_warnings
from ..settings.config import find_retired_config_keys, find_unknown_config_keys
from ..utils.redaction import redact_text
from .config_runtime import (
    _background_job_config_warnings,
    _load_runtime_config,
    _load_runtime_settings,
)
from .demo import _demo_command

_GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/tickernelz/hermes-progress-tail/releases/latest"
)
_LATEST_RELEASE_CACHE: dict[str, object] = {"checked_at": 0.0, "info": None}


class CommandRuntime(Protocol):
    def get_renderer(self) -> Any: ...

    assistant_capture: dict[str, Any]
    patch_report: PatchInstallReport

    def load_runtime_config(self) -> dict: ...


class _DefaultCommandRuntime:
    assistant_capture: dict[str, Any] = {}
    patch_report = PatchInstallReport()
    _renderer: Any = None

    def get_renderer(self) -> Any:
        if self._renderer is None:
            from ..rendering.renderer import ProgressRenderer

            self._renderer = ProgressRenderer(_load_runtime_settings())
        return self._renderer

    def load_runtime_config(self) -> dict:
        return _load_runtime_config()


_COMMAND_RUNTIME: CommandRuntime = _DefaultCommandRuntime()
_COMMAND_VERSION = ""


def configure_command_runtime(runtime: CommandRuntime, *, version: str) -> None:
    global _COMMAND_RUNTIME, _COMMAND_VERSION
    _COMMAND_RUNTIME = runtime
    _COMMAND_VERSION = version


def _patch_health_rows(report: PatchInstallReport, *, doctor: bool) -> list[str]:
    expected = {spec.name: spec.target for spec in _CAPABILITY_SPECS}
    statuses = {status.name: status for status in report.statuses}
    installed = sum(bool(status.installed) for status in report.statuses if status.name in expected)
    healthy = set(statuses) == set(expected) and installed == len(expected)
    rows = [f"hooks={'healthy' if healthy else 'degraded'} installed={installed}/11"]
    if not doctor:
        return rows
    for name, target in expected.items():
        status = statuses.get(name)
        if status is not None and status.installed:
            continue
        if status is None:
            category = "target_api_missing"
            reason = "status absent from patch report"
        else:
            category = status.failure_category.value
            target = status.target
            reason = status.reason
        safe_reason = safe_patch_reason(reason).replace("Traceback", "").strip()
        suffix = f" reason={safe_reason}" if safe_reason else ""
        rows.append(f"hook {name}: {category} target={target}{suffix}")
    return rows


def _latest_release_info(timeout: float = 1.5, *, refresh: bool = False) -> dict[str, str] | None:
    now = time.time()
    cached_at = float(_LATEST_RELEASE_CACHE.get("checked_at") or 0.0)
    if not refresh and now - cached_at < 300:
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


def _fresh_latest_release_info() -> dict[str, str] | None:
    try:
        return _latest_release_info(refresh=True)
    except TypeError:
        return _latest_release_info()


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


def _config_reasoning_effort(config: dict) -> str:
    for path in (
        ("reasoning", "effort"),
        ("reasoning", "reasoning_effort"),
        ("agent", "reasoning", "effort"),
        ("agent", "reasoning_effort"),
        ("model", "reasoning", "effort"),
        ("model", "reasoning_effort"),
    ):
        value = _nested_config_value(config, path)
        if value:
            return str(value).strip()
    return "auto"


def _nested_config_value(config: dict, path: tuple[str, ...]) -> object:
    value: object = config
    for key in path:
        if not isinstance(value, dict):
            return ""
        value = value.get(key)
    return value or ""


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


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _config_cleanup_command(args: str) -> str:
    tokens = {token for token in str(args or "").split() if token}
    dry_run = "--dry-run" in tokens or "dry-run" in tokens
    apply = "--apply" in tokens or "apply" in tokens or "--yes" in tokens
    if not dry_run and not apply:
        return (
            "Usage: /progresstail config cleanup --dry-run\n"
            "       /progresstail config cleanup --apply"
        )
    from ..installer import cleanup_legacy_global_suppression

    result = cleanup_legacy_global_suppression(_hermes_home(), dry_run=dry_run)
    return "\n".join(result.messages)


def _update_usage() -> str:
    return (
        "Usage: /progresstail update --dry-run\n"
        "       /progresstail update --apply\n"
        "Options: --ref vX.Y.Z, --profile NAME, --all-profiles, --force"
    )


def _parse_update_tokens(args: str) -> dict[str, object] | str:
    try:
        tokens = shlex.split(str(args or ""))
    except ValueError as exc:
        return f"Invalid update arguments: {exc}"
    opts: dict[str, object] = {
        "dry_run": False,
        "apply": False,
        "force": False,
        "ref": "",
        "profiles": [],
        "all_profiles": False,
    }
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in {"--dry-run", "dry-run"}:
            opts["dry_run"] = True
        elif token in {"--apply", "apply", "--yes"}:
            opts["apply"] = True
        elif token == "--force":
            opts["force"] = True
        elif token == "--all-profiles":
            opts["all_profiles"] = True
        elif token == "--ref":
            idx += 1
            if idx >= len(tokens):
                return "Invalid update arguments: --ref requires a value"
            opts["ref"] = tokens[idx]
        elif token.startswith("--ref="):
            opts["ref"] = token.split("=", 1)[1]
        elif token == "--profile":
            idx += 1
            if idx >= len(tokens):
                return "Invalid update arguments: --profile requires a value"
            opts["profiles"].extend(_split_csv(tokens[idx]))
        elif token.startswith("--profile="):
            opts["profiles"].extend(_split_csv(token.split("=", 1)[1]))
        else:
            return f"Invalid update arguments: unknown option {token}"
        idx += 1
    return opts


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _validate_update_ref(value: str) -> str:
    ref = str(value or "").strip()
    if not ref:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", ref):
        return ""
    if ref.startswith(("/", ".", "-")) or ".." in ref or "//" in ref:
        return ""
    return ref


def _is_safe_tar_member(destination: Path, member_name: str) -> bool:
    target = (destination / member_name).resolve()
    return target == destination or destination in target.parents


def _extract_update_archive(archive_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise ValueError(f"unsafe archive link: {member.name}")
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported archive member: {member.name}")
            if not _is_safe_tar_member(destination, member.name):
                raise ValueError(f"unsafe archive member: {member.name}")
        archive.extractall(destination)


def _download_update_source(ref: str, destination: Path) -> Path:
    archive_path = destination / "source.tar.gz"
    url = f"https://github.com/tickernelz/hermes-progress-tail/archive/{ref}.tar.gz"
    request = urllib.request.Request(url, headers={"User-Agent": "hermes-progress-tail-update"})
    with urllib.request.urlopen(request, timeout=30) as response:
        archive_path.write_bytes(response.read())
    _extract_update_archive(archive_path, destination)
    roots = [path for path in destination.iterdir() if path.is_dir()]
    if not roots:
        raise FileNotFoundError("downloaded archive did not contain a source directory")
    return roots[0]


def _run_update_install(
    ref: str,
    *,
    dry_run: bool,
    profiles: list[str] | None = None,
    all_profiles: bool = False,
) -> list[str]:
    from ..installer import install_many

    with tempfile.TemporaryDirectory(prefix="hpt-update-") as tmp_dir:
        source_dir = _download_update_source(ref, Path(tmp_dir))
        result = install_many(
            _hermes_home(),
            source_dir,
            profiles=profiles,
            all_profiles=all_profiles,
            set_display_off=True,
            dry_run=dry_run,
        )
    return result.messages


def _update_command(args: str) -> str:
    parsed = _parse_update_tokens(args)
    if isinstance(parsed, str):
        return parsed + "\n" + _update_usage()
    dry_run = bool(parsed["dry_run"])
    apply = bool(parsed["apply"])
    if dry_run == apply:
        return _update_usage()
    explicit_ref = _validate_update_ref(str(parsed["ref"] or ""))
    if parsed["ref"] and not explicit_ref:
        return "Invalid update ref. Use a tag/branch/ref like vX.Y.Z."
    latest = None if explicit_ref else _fresh_latest_release_info()
    ref = explicit_ref or str((latest or {}).get("tag_name") or "").strip()
    if not ref:
        return "Could not determine latest hermes-progress-tail release. Use --ref vX.Y.Z."
    if not bool(parsed["force"]) and not _is_newer_version(_COMMAND_VERSION, ref):
        return f"Already up to date: v{_COMMAND_VERSION}. Use --force with --ref to reinstall."
    profiles = parsed["profiles"] or None
    try:
        messages = _run_update_install(
            ref,
            dry_run=dry_run,
            profiles=profiles,
            all_profiles=bool(parsed["all_profiles"]),
        )
    except Exception as exc:
        return f"Update failed: {redact_text(str(exc))}"
    verb = "dry-run" if dry_run else "applied"
    lines = [f"Update {verb}: v{_COMMAND_VERSION} → {ref}"]
    lines.extend(messages)
    if dry_run:
        lines.append("No files changed. Re-run with --apply to update.")
    else:
        lines.append("Restart Hermes gateway for changes to take effect: /restart")
    return "\n".join(lines)


def _command(raw_args: str = "") -> str:
    raw = (raw_args or "").strip()
    args = raw.lower()

    if args.startswith("config cleanup"):
        return _config_cleanup_command(raw[len("config cleanup") :].strip())
    if args.startswith("update"):
        return _update_command(raw[len("update") :].strip())

    renderer = _COMMAND_RUNTIME.get_renderer()
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
        try:
            from ..hooks.monkeypatches import command_menu_monkeypatch_active

            command_menu_active = command_menu_monkeypatch_active()
        except Exception:
            command_menu_active = False
        settings = renderer.settings
        runtime_config = _COMMAND_RUNTIME.load_runtime_config()
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
        assistant_capture = _COMMAND_RUNTIME.assistant_capture
        capture_at = float(assistant_capture.get("updated_at") or 0.0)
        capture_when = (
            time.strftime("%H:%M:%S", time.localtime(capture_at)) if capture_at else "never"
        )
        plugin_state = "enabled" if "hermes-progress-tail" in enabled_plugins else "not listed"
        lines = [
            f"hermes-progress-tail {_COMMAND_VERSION}",
            f"plugin={plugin_state}",
            f"sessions={active}",
            f"agent.gateway_notify_interval={agent_config.get('gateway_notify_interval', '<default:180>')}",
            f"tools={'enabled' if settings.tools.enabled else 'disabled'} lines={settings.tools.lines} completed={settings.tools.show_completed} duration={settings.tools.show_duration} timestamp={settings.tools.timestamp_format if settings.tools.timestamp else 'off'}",
            f"todo=sticky:{settings.todo.sticky} hide_tool_line:{settings.todo.hide_tool_line}",
            f"patch=detail:{settings.patch.detail} preview_chars:{settings.patch.preview_chars} max_files:{settings.patch.max_files}",
            f"assistant={'enabled' if settings.assistant.enabled else 'disabled'} max_lines={settings.assistant.max_lines} max_chars={settings.assistant.max_chars}",
            f"assistant_capture={assistant_capture.get('status', 'never')} already_streamed={assistant_capture.get('already_streamed', False)} session={assistant_capture.get('session_id') or '-'} key_present={assistant_capture.get('session_key_present', False)} at={capture_when}",
            f"reasoning={'enabled' if settings.reasoning.enabled else 'disabled'} max_lines={settings.reasoning.max_lines} max_chars={settings.reasoning.max_chars}",
            f"reasoning_effort={_config_reasoning_effort(runtime_config)}",
            "reasoning_sources=structured_reasoning,inline_think,provider_delimiters",
            f"delegates={'enabled' if settings.delegates.enabled else 'disabled'} max={settings.delegates.max_delegates} lines={settings.delegates.lines_per_delegate} ttl={settings.delegates.completed_ttl_seconds}s thinking={settings.delegates.thinking}",
            f"background_jobs={'enabled' if settings.background_jobs.enabled else 'disabled'} list_running={settings.background_jobs.list_running} show_completed={settings.background_jobs.show_completed} max={settings.background_jobs.max_jobs} ttl={settings.background_jobs.completed_ttl_seconds}s head={settings.background_jobs.head_lines} tail={settings.background_jobs.tail_lines} update={settings.background_jobs.update_interval_seconds}s suppress_native_notify={settings.background_jobs.suppress_native_notify} suppress_watch={settings.background_jobs.suppress_watch_notifications}",
            f"native_gateway=suppress:{settings.native_gateway.suppress}",
            f"footer={'enabled' if settings.footer.enabled else 'disabled'} density:{settings.footer.density} max_path_chars:{settings.footer.max_path_chars}",
            f"telegram=rich:{settings.telegram.rich_messages} table:{settings.telegram.verification_table} thinking:{settings.telegram.thinking_blocks} max_table_rows:{settings.telegram.max_table_rows} compact_success:{settings.telegram.compact_success} max_detail_items:{settings.telegram.max_detail_items}",
            f"renderer=mode:{settings.renderer.mode} strategy:{settings.renderer.strategy} style:{settings.renderer.style} density:{settings.renderer.density} edit_interval:{settings.renderer.edit_interval} agent_label:{settings.renderer.agent_label or '-'}",
            f"display.tool_progress={display.get('tool_progress', '<unset>')}",
            f"display.show_reasoning={display.get('show_reasoning', '<unset>')}",
            f"monkeypatch={monkeypatch_active}",
            f"delegate_monkeypatch={delegate_monkeypatch_active}",
            f"command_menu_monkeypatch={command_menu_active}",
        ]
        lines.extend(_patch_health_rows(_COMMAND_RUNTIME.patch_report, doctor=args == "doctor"))
        if args == "doctor":
            lines.extend(_legacy_global_suppression_warnings(runtime_config))
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
            lines.extend(_legacy_global_suppression_warnings(runtime_config))
        latest_release = None if args == "doctor" else _latest_release_info()
        return _status_markdown(
            version=_COMMAND_VERSION,
            runtime_rows=lines,
            plugin_state=plugin_state,
            active_sessions=active,
            latest_release=latest_release,
            doctor=args == "doctor",
        )
    if args in {"test", "demo", "demo plain", "demo failed"}:
        return _demo_command(plain=args == "demo plain", failed=args == "demo failed")
    return "Usage: /progresstail status | doctor | jobs [all] | update --dry-run|--apply | config cleanup --dry-run|--apply | demo [plain|failed]"
