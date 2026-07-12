from __future__ import annotations

import contextlib
import re
import time
from collections.abc import Callable

from ..models.state import BackgroundJob, BackgroundJobEvent, SessionContext
from ..settings.types import BackgroundJobSettings, Settings
from ..utils.redaction import redact_text
from ..utils.text import truncate_text


def apply_background_job_event(
    ctx: SessionContext,
    event: BackgroundJobEvent,
    *,
    settings: BackgroundJobSettings,
    cancel_poll: Callable[[BackgroundJob], None],
) -> None:
    job = ctx.background.jobs.get(event.process_id)
    if event.event_type == "cleanup":
        prune_background_jobs(ctx, settings=settings, cancel_poll=cancel_poll, now=event.created_at)
        return
    if job is None:
        job = BackgroundJob(
            process_id=event.process_id,
            command=event.command,
            cwd=event.cwd,
            pid=event.pid,
            started_at=event.created_at,
            updated_at=event.created_at,
        )
        ctx.background.jobs[event.process_id] = job
        ctx.background.order.append(event.process_id)
    if event.command:
        job.command = event.command
    if event.cwd:
        job.cwd = event.cwd
    if event.pid is not None:
        job.pid = event.pid
    if event.output or event.event_type in {"output", "completed", "killed", "lost"}:
        update_background_output(job, event.output, settings=settings)
    if event.exited or event.event_type in {"completed", "killed", "lost"}:
        job.status = "completed" if event.exit_code in {0, None} else "failed"
        if event.event_type == "killed":
            job.status = "killed"
        elif event.event_type == "lost":
            job.status = "lost"
        job.exit_code = event.exit_code
        job.completed_at = event.created_at
        cancel_poll(job)
    elif event.event_type == "started":
        job.status = "running"
    job.updated_at = event.created_at
    prune_background_jobs(ctx, settings=settings, cancel_poll=cancel_poll)


def update_background_output(
    job: BackgroundJob, output: str, *, settings: BackgroundJobSettings
) -> None:
    clean = normalize_background_output(output)
    if clean == job.last_output:
        return
    job.last_output = clean
    job.output_chars = len(clean)
    lines = useful_output_lines(clean)
    job.output_head = tuple(lines[: settings.head_lines])
    job.output_tail = tuple(lines[-settings.tail_lines :])


def background_jobs_section(
    ctx: SessionContext,
    *,
    settings: Settings,
    section: Callable[[str, str, str], str],
    background_jobs_enabled: Callable[[SessionContext], bool],
    cancel_poll: Callable[[BackgroundJob], None],
) -> str:
    if not background_jobs_enabled(ctx):
        return ""
    prune_background_jobs(ctx, settings=settings.background_jobs, cancel_poll=cancel_poll)
    jobs = [ctx.background.jobs[jid] for jid in ctx.background.order if jid in ctx.background.jobs]
    visible = []
    for job in jobs:
        if job.status == "running" and not settings.background_jobs.list_running:
            continue
        if job.status != "running" and not settings.background_jobs.show_completed:
            continue
        visible.append(job)
    if not visible:
        return ""
    visible = visible[-settings.background_jobs.max_jobs :]
    lines: list[str] = []
    for idx, job in enumerate(visible, 1):
        lines.extend(background_job_lines(job, idx, settings=settings))
    return section("Background Jobs", "🖥", "\n".join(lines))


def background_job_lines(job: BackgroundJob, idx: int, *, settings: Settings) -> list[str]:
    emoji = settings.renderer.style == "emoji"
    marker = background_marker(job.status, emoji)
    command = truncate_text(redact_text(job.command or job.process_id), 72)
    elapsed = duration_short((job.completed_at or time.time()) - job.started_at)
    title = f"[{idx}] {marker} {job.process_id} · {command} · {elapsed}"
    if job.status != "running" and job.exit_code is not None:
        title += f" · exit {job.exit_code}"
    lines = [title]
    rendered_head = [
        cap_bg_line(line, settings=settings.background_jobs) for line in job.output_head
    ]
    rendered_tail = [
        cap_bg_line(line, settings=settings.background_jobs) for line in job.output_tail
    ]
    if rendered_head:
        lines.append("    start: " + rendered_head[0])
        for line in rendered_head[1:]:
            lines.append("           " + line)
    tail_label = "end" if job.status != "running" else "tail"
    tail_lines = [line for line in rendered_tail if line not in rendered_head]
    if tail_lines:
        lines.append(f"    {tail_label}: " + tail_lines[0])
        for line in tail_lines[1:]:
            lines.append("         " + line)
    return lines


def background_marker(status: str, emoji: bool) -> str:
    if not emoji:
        return status
    return {
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "killed": "🛑",
        "lost": "⚠️",
    }.get(status, "•")


def cap_bg_line(line: str, *, settings: BackgroundJobSettings) -> str:
    return truncate_text(redact_text(line), settings.max_line_chars)


def duration_short(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def normalize_background_output(output: str) -> str:
    text = str(output or "").replace("\r", "\n")
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    return text


def useful_output_lines(output: str) -> list[str]:
    lines = []
    skip_next_hushlogin_path = False
    for raw in output.splitlines():
        line = " ".join(raw.strip().split())
        if not line:
            continue
        if skip_next_hushlogin_path:
            skip_next_hushlogin_path = False
            if ".hushlogin" in line:
                continue
        if _is_background_output_noise(line):
            if line.startswith("This message is shown once a day"):
                skip_next_hushlogin_path = True
            continue
        lines.append(line)
    return lines


def _is_background_output_noise(line: str) -> bool:
    if any(
        noise in line
        for noise in (
            "bash: cannot set terminal process group",
            "bash: no job control in this shell",
            "no job control in this shell",
            "Welcome to Ubuntu",
            "Strictly confined Kubernetes makes edge and IoT secure",
            "This message is shown once a day",
        )
    ):
        return True
    return bool(re.match(r"^\* (Documentation|Management|Support):\s+https?://", line))


def prune_background_jobs(
    ctx: SessionContext,
    *,
    settings: BackgroundJobSettings,
    cancel_poll: Callable[[BackgroundJob], None],
    now: float | None = None,
) -> None:
    ttl = settings.completed_ttl_seconds
    now = time.time() if now is None else now
    removed_any = False
    for process_id in list(ctx.background.order):
        job = ctx.background.jobs.get(process_id)
        if job is None:
            with contextlib.suppress(ValueError):
                ctx.background.order.remove(process_id)
            continue
        if job.status != "running" and job.completed_at and now - job.completed_at > ttl:
            cancel_poll(job)
            ctx.background.jobs.pop(process_id, None)
            removed_any = True
            with contextlib.suppress(ValueError):
                ctx.background.order.remove(process_id)
    while len(ctx.background.order) > settings.max_jobs * 3:
        process_id = ctx.background.order.popleft()
        job = ctx.background.jobs.get(process_id)
        if job is not None and job.status == "running":
            ctx.background.order.append(process_id)
            break
        job = ctx.background.jobs.pop(process_id, None)
        if job is not None:
            removed_any = True
            cancel_poll(job)
    if (
        removed_any
        and not ctx.background.jobs
        and ctx.delivery.progress_state == "background_active"
    ):
        ctx.delivery.progress_state = "finalized"


def cancel_background_poll(job: BackgroundJob) -> None:
    task = job.poll_task
    if task is not None and not task.done():
        task.cancel()
    job.poll_task = None
