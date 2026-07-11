from __future__ import annotations

import time

from ..models.state import DelegateEvent, RoutingState, SessionContext, TodoItem
from ..rendering.renderer import ProgressRenderer
from ..settings.config import load_settings


def _demo_command(*, plain: bool = False, failed: bool = False) -> str:
    renderer = ProgressRenderer(
        load_settings(
            {
                "progress_tail": {
                    "tools": {"timestamp": False, "lines": 4},
                    "renderer": {"mode": "focused", "density": "verbose", "style": "emoji"},
                    "delegates": {"lines_per_delegate": 5, "max_line_chars": 180},
                }
            }
        )
    )
    platform = "sms" if plain else "telegram"
    ctx = SessionContext(
        "demo-session",
        "demo-session-key",
        platform,
        "demo-chat",
        None,
        None,
        None,
        routing=RoutingState(strategy="live_tail", timestamp=False, agent_label="Hermes"),
    )
    ctx.tool.todo_items = (
        TodoItem("Inspect renderer", "completed"),
        TodoItem("Build deterministic demo", "in_progress"),
        TodoItem("Run tests", "pending"),
        TodoItem("Review release", "pending"),
    )
    ctx.tool.started_count = 5
    ctx.tool.completed_count = 4
    ctx.tool.failed_count = 1 if failed else 0
    renderer.delegate_renderer.apply_event(
        ctx,
        DelegateEvent(
            "demo-session",
            "demo-session-key",
            platform,
            "demo-agent",
            task_index=0,
            task_count=1,
            goal="demo UI review",
            event_type="subagent.start",
            status="running",
            created_at=1,
        ),
    )
    for index, (tool_name, preview, args) in enumerate(
        (
            ("read_file", "hermes_progress_tail/rendering/focused.py:1+120", {}),
            ("search_files", "focused_block", {"pattern": "focused_block"}),
            (
                "terminal",
                "python -m pytest tests/test_renderer.py -q",
                {"command": "python -m pytest tests/test_renderer.py -q"},
            ),
            ("read_file", "tests/test_focused_live_markdown.py:1+80", {}),
        ),
        start=2,
    ):
        renderer.delegate_renderer.apply_event(
            ctx,
            DelegateEvent(
                "demo-session",
                "demo-session-key",
                platform,
                "demo-agent",
                task_index=0,
                task_count=1,
                goal="demo UI review",
                event_type="subagent.tool",
                tool_name=tool_name,
                preview=preview,
                args=args,
                status="running",
                created_at=index,
            ),
        )
    renderer.delegate_renderer.apply_event(
        ctx,
        DelegateEvent(
            "demo-session",
            "demo-session-key",
            platform,
            "demo-agent",
            task_index=0,
            task_count=1,
            goal="demo UI review",
            event_type="subagent.complete",
            status="completed",
            duration_seconds=12,
            summary="demo smoke check passed",
            created_at=time.time(),
        ),
    )
    ctx.tool.lines.extend(
        [
            "✅ read_file: rendering/focused.py:1+120 · done · 0.2s",
            "✅ search_files: focused_block · done · 0.1s",
            (
                "❌ terminal: pytest tests/test_renderer.py -q · failed · 2.1s"
                if failed
                else "✅ terminal: pytest tests/test_renderer.py -q · done · 2.1s"
            ),
            "terminal: git diff --check · running",
        ]
    )
    return renderer._content(ctx)
