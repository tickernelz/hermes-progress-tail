from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..models.state import EnvironmentSnapshot, SessionContext

logger = logging.getLogger(__name__)
_GIT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _agent_session_id(agent: Any) -> str:
    return str(getattr(agent, "session_id", "") or "")


def _agent_session_key(agent: Any) -> str:
    return str(
        getattr(agent, "gateway_session_key", None)
        or getattr(agent, "_gateway_session_key", None)
        or ""
    )


def _update_environment_from_agent(
    ctx: SessionContext, agent: Any, messages: list[dict[str, Any]] | None = None
) -> None:
    if agent is None:
        return
    try:
        agent_cwd = _agent_cwd(agent)
        cwd_source = str(getattr(ctx, "_progress_tail_cwd_source", "") or "")
        if cwd_source == "terminal" and ctx.environment and ctx.environment.cwd:
            cwd = Path(ctx.environment.cwd).expanduser()
        else:
            cwd = agent_cwd
            ctx._progress_tail_cwd_source = "agent"
        git = _runtime_git_snapshot(cwd)
        compressor = getattr(agent, "context_compressor", None)
        ctx.environment = EnvironmentSnapshot(
            context_tokens=_context_tokens(agent, compressor, messages),
            context_window=_positive_attr(
                agent,
                "_config_context_length",
                "context_length",
                "max_context_tokens",
                "max_context_length",
            )
            or _positive_attr(compressor, "context_length", "max_context_tokens"),
            context_kind="est" if compressor is not None or messages is not None else "",
            model=_agent_string(agent, "model", "model_name"),
            provider=_agent_string(agent, "provider", "provider_name", "model_provider"),
            profile=_runtime_profile_name(),
            cwd=str(cwd),
            git_branch=str(git.get("branch") or ""),
            git_dirty=bool(git.get("dirty")),
            git_ahead=int(git.get("ahead") or 0),
            git_behind=int(git.get("behind") or 0),
            worktree=str(git.get("worktree") or ""),
            strategy=ctx.strategy,
        )
    except Exception:
        logger.debug("hermes-progress-tail environment snapshot update failed", exc_info=True)


def _context_tokens(
    agent: Any, compressor: Any, messages: list[dict[str, Any]] | None = None
) -> int:
    estimated = _estimate_request_tokens(agent, messages)
    if estimated > 0:
        return estimated
    if bool(getattr(compressor, "awaiting_real_usage_after_compression", False)):
        rough = _positive_attr(compressor, "last_compression_rough_tokens")
        if rough > 0:
            return rough
    return _positive_attr(
        compressor,
        "last_prompt_tokens",
        "last_estimated_tokens",
        "current_context_tokens",
        "current_tokens",
        "approx_tokens",
        "last_compression_rough_tokens",
    )


def _estimate_request_tokens(agent: Any, messages: list[dict[str, Any]] | None) -> int:
    if not isinstance(messages, list) or not messages:
        return 0
    try:
        from agent.model_metadata import estimate_request_tokens_rough

        return _positive_int(
            estimate_request_tokens_rough(
                messages,
                system_prompt=_agent_system_prompt(agent),
                tools=getattr(agent, "tools", None) or None,
            )
        )
    except Exception:
        logger.debug("hermes-progress-tail request token estimate failed", exc_info=True)
    try:
        from agent.model_metadata import estimate_messages_tokens_rough

        return _positive_int(estimate_messages_tokens_rough(messages))
    except Exception:
        logger.debug("hermes-progress-tail message token estimate failed", exc_info=True)
    return 0


def _agent_system_prompt(agent: Any) -> str:
    for attr in ("system_message", "_system_message", "current_system_message"):
        value = getattr(agent, attr, None)
        if value:
            return str(value)
    return ""


def _agent_cwd(agent: Any) -> Path:
    for attr in ("workdir", "working_dir", "cwd", "project_dir"):
        value = getattr(agent, attr, None)
        if value:
            return Path(str(value)).expanduser()
    return Path.cwd()


def _agent_string(agent: Any, *attrs: str) -> str:
    for attr in attrs:
        value = getattr(agent, attr, None)
        if value:
            return str(value)
    return ""


def _positive_attr(obj: Any, *attrs: str) -> int:
    if obj is None:
        return 0
    for attr in attrs:
        parsed = _positive_int(getattr(obj, attr, None))
        if parsed > 0:
            return parsed
    return 0


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _runtime_profile_name() -> str:
    try:
        from hermes_cli.profiles import get_active_profile_name

        return str(get_active_profile_name() or "")
    except Exception:
        return ""


def _git_snapshot(cwd: Path) -> dict[str, Any]:
    try:
        path = cwd.expanduser().resolve(strict=False)
    except Exception:
        path = cwd
    key = str(path)
    now = time.monotonic()
    cached = _GIT_CACHE.get(key)
    if cached and now - cached[0] < 5.0:
        return cached[1]
    data = _load_git_snapshot(path)
    _GIT_CACHE[key] = (now, data)
    return data


def _load_git_snapshot(cwd: Path) -> dict[str, Any]:
    result = _git_command(cwd, "rev-parse", "--is-inside-work-tree")
    if result != "true":
        return {}
    branch = _git_command(cwd, "branch", "--show-current") or _git_command(
        cwd, "rev-parse", "--short", "HEAD"
    )
    root = _git_command(cwd, "rev-parse", "--show-toplevel")
    status = _git_command(cwd, "status", "--porcelain=v1", "--branch")
    ahead = 0
    behind = 0
    dirty = False
    for line in status.splitlines():
        if line.startswith("## "):
            if "ahead " in line:
                ahead = _branch_count(line, "ahead")
            if "behind " in line:
                behind = _branch_count(line, "behind")
            continue
        if line.strip():
            dirty = True
    return {
        "branch": branch,
        "dirty": dirty,
        "ahead": ahead,
        "behind": behind,
        "worktree": Path(root).name if root else "",
    }


def _branch_count(line: str, label: str) -> int:
    marker = f"{label} "
    if marker not in line:
        return 0
    tail = line.split(marker, 1)[1]
    digits = []
    for ch in tail:
        if ch.isdigit():
            digits.append(ch)
        elif digits:
            break
    return int("".join(digits) or "0")


def _git_command(cwd: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=0.15,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _runtime_git_snapshot(cwd: Path) -> dict[str, Any]:
    # Keep plugin._git_snapshot monkeypatches from tests/debug sessions effective
    # after this helper moved out of runtime.plugin.
    try:
        from . import plugin as runtime_plugin

        func = getattr(runtime_plugin, "_git_snapshot", None)
        if callable(func) and func is not _git_snapshot:
            return func(cwd)
    except Exception:
        pass
    return _git_snapshot(cwd)


def _update_environment_from_terminal(
    ctx: SessionContext, args: dict | None, task_id: str = ""
) -> None:
    cwd = _terminal_live_cwd(task_id) or str((args or {}).get("workdir") or "")
    if not cwd:
        return
    _replace_environment_cwd(ctx, cwd, source="terminal")


def _terminal_live_cwd(task_id: str = "") -> str:
    try:
        from tools.terminal_tool import get_active_env

        env = get_active_env(task_id or "default")
        cwd = getattr(env, "cwd", None)
        return str(cwd) if cwd else ""
    except Exception:
        return ""


def _replace_environment_cwd(ctx: SessionContext, cwd: str | Path, *, source: str) -> None:
    try:
        path = Path(str(cwd)).expanduser()
    except Exception:
        return
    if not str(path):
        return
    git = _runtime_git_snapshot(path)
    env = ctx.environment or EnvironmentSnapshot(
        profile=_runtime_profile_name(), strategy=ctx.strategy
    )
    ctx.environment = replace(
        env,
        cwd=str(path),
        git_branch=str(git.get("branch") or ""),
        git_dirty=bool(git.get("dirty")),
        git_ahead=int(git.get("ahead") or 0),
        git_behind=int(git.get("behind") or 0),
        worktree=str(git.get("worktree") or ""),
        profile=env.profile or _runtime_profile_name(),
        strategy=env.strategy or ctx.strategy,
    )
    ctx._progress_tail_cwd_source = source
