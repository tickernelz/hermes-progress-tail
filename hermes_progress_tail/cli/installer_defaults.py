from __future__ import annotations

DEFAULT_CONFIG = {
    "enabled": True,
    "tools": {
        "enabled": True,
        "lines": 3,
        "preview_length": 120,
        "show_completed": True,
        "show_duration": True,
        "timestamp": True,
        "timestamp_format": "%H:%M",
    },
    "delegates": {
        "enabled": True,
        "max_delegates": 4,
        "lines_per_delegate": 2,
        "max_goal_chars": 48,
        "max_line_chars": 120,
        "show_model": False,
        "show_tool_count": True,
        "show_completion": True,
        "completed_ttl_seconds": 5,
        "thinking": "off",
    },
    "todo": {
        "sticky": True,
        "hide_tool_line": True,
        "max_pending": 3,
        "max_completed": 3,
        "max_cancelled": 2,
        "max_item_chars": 40,
    },
    "patch": {
        "detail": "smart",
        "preview_chars": 48,
        "max_files": 3,
    },
    "assistant": {
        "enabled": True,
        "max_lines": 3,
        "max_chars": 500,
        "min_update_chars": 160,
    },
    "reasoning": {
        "enabled": True,
        "max_lines": 3,
        "max_chars": 600,
        "min_update_chars": 300,
        "no_edit_strategy": "off",
    },
    "background_jobs": {
        "enabled": True,
        "list_running": True,
        "show_completed": True,
        "completed_ttl_seconds": 5,
        "max_jobs": 4,
        "head_lines": 2,
        "tail_lines": 3,
        "max_line_chars": 120,
        "update_interval_seconds": 10,
        "suppress_native_notify": True,
        "suppress_watch_notifications": True,
    },
    "native_gateway": {
        "suppress": True,
    },
    "cleanup": {
        "auto_delete": False,
        "delay_seconds": 5,
        "delete_on_success": True,
        "delete_on_failure": False,
        "delete_background_active": False,
    },
    "footer": {
        "enabled": True,
        "density": "normal",
        "max_path_chars": 56,
    },
    "telegram": {
        "rich_messages": True,
        "verification_table": True,
        "thinking_blocks": True,
        "max_table_rows": 8,
        "compact_success": True,
        "max_detail_items": 8,
    },
    "renderer": {
        "strategy": "auto",
        "edit_interval": 5.0,
        "stale_ttl_seconds": 900,
        "redact_secrets": True,
        "mode": "focused",
        "style": "emoji",
        "density": "normal",
        "agent_label": "",
    },
    "no_edit": {
        "interval_seconds": 30,
        "min_new_events": 3,
        "final_summary": True,
        "max_snapshots_per_turn": 5,
    },
}

TELEGRAM_FLOOD_SAFE_CONFIG = {
    "assistant": {
        "min_update_chars": 160,
    },
    "reasoning": {
        "min_update_chars": 300,
    },
    "background_jobs": {
        "update_interval_seconds": 10,
    },
    "cleanup": {
        "auto_delete": False,
    },
    "telegram": {
        "rich_messages": True,
    },
    "renderer": {
        "edit_interval": 5.0,
        "density": "normal",
    },
}
