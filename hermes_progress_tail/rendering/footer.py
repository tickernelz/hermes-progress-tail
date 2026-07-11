from __future__ import annotations

from pathlib import Path

from ..models.release import FooterInfo, FooterInfoProvider, is_newer_version, no_footer_info
from ..models.state import EnvironmentSnapshot, SessionContext
from ..settings.config import Settings
from ..utils.redaction import simplify_path
from ..utils.text import truncate_text


def focused_footer(
    ctx: SessionContext,
    *,
    settings: Settings,
    footer_info_provider: FooterInfoProvider = no_footer_info,
) -> str:
    if not settings.footer.enabled:
        return ""
    body = footer_body(ctx, settings=settings, footer_info_provider=footer_info_provider)
    if not body:
        return ""
    from .focused import focused_block

    return focused_block("Status", body, platform=ctx.platform)


def sectioned_footer(
    ctx: SessionContext,
    *,
    settings: Settings,
    footer_info_provider: FooterInfoProvider = no_footer_info,
) -> str:
    if not settings.footer.enabled:
        return ""
    body = footer_body(ctx, settings=settings, footer_info_provider=footer_info_provider)
    if not body:
        return ""
    from .sections import section

    return section("Status", "🧭", body, style=settings.renderer.style)


def footer_body(
    ctx: SessionContext,
    *,
    settings: Settings,
    footer_info_provider: FooterInfoProvider = no_footer_info,
) -> str:
    env = ctx.environment or EnvironmentSnapshot()
    if not _has_runtime_signal(env):
        return ""
    update = _footer_update_label(_safe_footer_info(footer_info_provider))
    density = settings.footer.density
    if density == "compact":
        return _compact_footer(ctx, env, settings=settings, update=update)
    return _normal_footer(ctx, env, settings=settings, debug=density == "debug", update=update)


def _has_runtime_signal(env: EnvironmentSnapshot) -> bool:
    return any(
        (
            env.context_tokens > 0,
            env.context_window > 0,
            bool(str(env.model or "").strip()),
            bool(str(env.provider or "").strip()),
            bool(str(env.profile or "").strip()),
            bool(str(env.cwd or "").strip()),
            bool(str(env.git_branch or "").strip()),
            bool(str(env.worktree or "").strip()),
        )
    )


def _normal_footer(
    ctx: SessionContext,
    env: EnvironmentSnapshot,
    *,
    settings: Settings,
    debug: bool = False,
    update: str = "",
) -> str:
    first = _clean_parts(
        [
            _context_label(env),
            _compaction_label(ctx),
            _model_label(env),
            _profile_label(env),
            _strategy_label(ctx, env),
            _reasoning_effort_label(env),
        ]
    )
    second = _clean_parts(
        [
            _git_label(env),
            _worktree_label(env),
            _cwd_label(env, settings=settings),
        ]
    )
    lines = [_prefix_line("🧠", " · ".join(first)), _prefix_line("🌿", " · ".join(second))]
    if update:
        lines.append(update)
    if debug:
        debug_parts = _clean_parts([_provider_label(env), _context_percent_label(env)])
        if debug_parts:
            lines.append(_prefix_line("🔎", " · ".join(debug_parts)))
    return "\n".join(line for line in lines if line)


def _compact_footer(
    ctx: SessionContext, env: EnvironmentSnapshot, *, settings: Settings, update: str = ""
) -> str:
    parts = _clean_parts(
        [
            _context_percent_label(env) or _context_label(env),
            _compaction_label(ctx),
            _short_model_label(env),
            _git_label(env),
            _short_cwd_label(env, settings=settings),
            _strategy_label(ctx, env),
            _reasoning_effort_label(env),
        ]
    )
    body = " · ".join(parts)
    return f"{body}\n{update}" if body and update else body or update


def _prefix_line(icon: str, body: str) -> str:
    body = str(body or "").strip()
    return f"{icon} {body}" if body else ""


def _clean_parts(values: list[str]) -> list[str]:
    return [value for value in values if value]


def _context_label(env: EnvironmentSnapshot) -> str:
    if env.context_tokens > 0 and env.context_window > 0:
        suffix = f" {env.context_kind.strip()}" if env.context_kind.strip() else ""
        return (
            f"ctx {_compact_count(env.context_tokens)}/{_compact_count(env.context_window)}{suffix}"
        )
    if env.context_window > 0:
        return f"ctx {_compact_count(env.context_window)} window"
    return ""


def _context_percent_label(env: EnvironmentSnapshot) -> str:
    if env.context_tokens <= 0 or env.context_window <= 0:
        return ""
    percent = round((env.context_tokens / env.context_window) * 100)
    return f"ctx {percent}%"


def _compaction_label(ctx: SessionContext) -> str:
    try:
        diagnostics = getattr(ctx, "diagnostics", ctx)
        count = max(0, int(getattr(diagnostics, "compaction_count", 0)))
    except (TypeError, ValueError):
        count = 0
    return f"compacted {count}x"


def _model_label(env: EnvironmentSnapshot) -> str:
    model = _short_model(env.model)
    provider = _short_provider(env.provider)
    if model and provider:
        return f"{provider}:{model}"
    return model


def _short_model_label(env: EnvironmentSnapshot) -> str:
    return _short_model(env.model)


def _provider_label(env: EnvironmentSnapshot) -> str:
    provider = _short_provider(env.provider)
    return f"provider {provider}" if provider else ""


def _reasoning_effort_label(env: EnvironmentSnapshot) -> str:
    effort = str(getattr(env, "reasoning_effort", "") or "").strip()
    return f"reasoning_effort={effort or 'auto'}"


def _profile_label(env: EnvironmentSnapshot) -> str:
    profile = str(env.profile or "").strip()
    return f"profile {profile}" if profile else ""


def _strategy_label(ctx: SessionContext, env: EnvironmentSnapshot) -> str:
    return str(env.strategy or ctx.routing.strategy or "").strip()


def _git_label(env: EnvironmentSnapshot) -> str:
    branch = str(env.git_branch or "").strip()
    if not branch:
        return ""
    label = f"git {branch}"
    if env.git_dirty:
        label += "*"
    if env.git_ahead:
        label += f" +{env.git_ahead}"
    if env.git_behind:
        label += f" -{env.git_behind}"
    return label


def _worktree_label(env: EnvironmentSnapshot) -> str:
    worktree = str(env.worktree or "").strip()
    return f"worktree {worktree}" if worktree else ""


def _cwd_label(env: EnvironmentSnapshot, *, settings: Settings) -> str:
    cwd = _short_path(env.cwd, settings=settings)
    return f"cwd {cwd}" if cwd else ""


def _short_cwd_label(env: EnvironmentSnapshot, *, settings: Settings) -> str:
    return _short_path(env.cwd, settings=settings)


def _short_path(path: str, *, settings: Settings) -> str:
    raw = str(path or "").strip()
    if not raw:
        return ""
    simplified = _display_path(raw)
    return truncate_text(simplified, settings.footer.max_path_chars)


def _display_path(raw: str) -> str:
    try:
        path = Path(raw).expanduser().resolve(strict=False)
        home = Path.home().resolve(strict=False)
        try:
            return "~/" + path.relative_to(home).as_posix()
        except ValueError:
            simplified = simplify_path(raw)
            if simplified in {"", "."}:
                return path.name or path.as_posix()
            return simplified
    except Exception:
        simplified = simplify_path(raw)
        return raw if simplified in {"", "."} else simplified


def _short_provider(provider: str) -> str:
    value = str(provider or "").strip()
    if value.startswith("custom:"):
        return "custom"
    return value


def _short_model(model: str) -> str:
    value = str(model or "").strip()
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    return value


def _safe_footer_info(provider: FooterInfoProvider) -> FooterInfo:
    try:
        info = provider()
        return info if isinstance(info, FooterInfo) else FooterInfo()
    except Exception:
        return FooterInfo()


def _footer_update_label(info: FooterInfo) -> str:
    latest_tag = str(info.latest_tag or "").strip()
    if not latest_tag or not is_newer_version(info.current_version, latest_tag):
        return ""
    latest_url = str(info.latest_url or "").strip()
    label = f"⬆️ update `{latest_tag}`"
    return f"{label} · {latest_url}" if latest_url else label


def _compact_count(value: int) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number >= 1_000_000:
        return f"{round(number / 1_000_000)}m"
    if number >= 1000:
        return f"{round(number / 1000)}k"
    return str(number)
