from hermes_progress_tail.config import find_retired_config_keys, find_unknown_config_keys


def test_config_contract_reports_unknown_keys_without_flagging_platform_names():
    unknown = find_unknown_config_keys(
        {
            "progress_tail": {
                "mystery": True,
                "tools": {"enabled": True, "typo_lines": 4},
                "telegram": {
                    "rich_messages": True,
                    "max_table_rows": 6,
                    "compact_success": True,
                    "max_detail_items": 4,
                },
                "platforms": {
                    "telegram": {
                        "strategy": "live_tail",
                        "bogus": "value",
                    }
                },
            }
        }
    )

    assert "progress_tail.mystery" in unknown
    assert "progress_tail.tools.typo_lines" in unknown
    assert "progress_tail.telegram" not in unknown
    assert "progress_tail.telegram.rich_messages" not in unknown
    assert "progress_tail.telegram.max_table_rows" not in unknown
    assert "progress_tail.telegram.compact_success" not in unknown
    assert "progress_tail.telegram.max_detail_items" not in unknown
    assert "progress_tail.platforms.telegram.bogus" in unknown
    assert "progress_tail.platforms.telegram" not in unknown


def test_config_contract_reports_retired_keys_separately_from_unknown_keys():
    config = {
        "progress_tail": {
            "finalization": {"delete_on_success": True},
            "background_jobs": {"default_notify_on_complete": False},
            "telegram": {"collapsible_details": True, "details_open_on_failure": True},
        }
    }

    assert find_retired_config_keys(config) == [
        "progress_tail.finalization",
        "progress_tail.background_jobs.default_notify_on_complete",
        "progress_tail.telegram.collapsible_details",
        "progress_tail.telegram.details_open_on_failure",
    ]
    assert find_unknown_config_keys(config) == []


def test_config_contract_accepts_legacy_tool_progress_tail_shape():
    legacy_config = {
        "tool_progress_tail": {
            "enabled": True,
            "defaults": {"lines": 4},
            "no_edit": {"interval_seconds": 60},
            "platforms": {"discord": {"strategy": "snapshot"}},
        }
    }

    assert find_unknown_config_keys(legacy_config) == []
