from copy import deepcopy

import pytest

from hermes_progress_tail.settings.loading import load_settings
from hermes_progress_tail.settings.migration import (
    extract_progress_tail_section,
    find_retired_config_keys,
    find_unknown_config_keys,
)


class OpaqueLeaf:
    def __deepcopy__(self, memo):
        raise TypeError("opaque leaf cannot be deep-copied")


def _config_for_shape(shape, section):
    if shape == "current":
        return {"progress_tail": section}
    if shape == "legacy":
        return {"tool_progress_tail": section}
    return section


def test_current_section_wins_over_simultaneous_legacy_and_is_copied():
    config = {
        "progress_tail": {"tools": {"lines": 8}},
        "tool_progress_tail": {"defaults": {"lines": 9}},
    }

    section = extract_progress_tail_section(config)

    assert section == {"tools": {"lines": 8}}
    assert section is not config["progress_tail"]
    assert section["tools"] is not config["progress_tail"]["tools"]


def test_legacy_section_is_converted_to_current_shape_with_canonical_defaults():
    section = extract_progress_tail_section(
        {
            "tool_progress_tail": {
                "enabled": False,
                "defaults": {
                    "lines": 6,
                    "preview_length": 80,
                    "show_completed": False,
                    "show_duration": False,
                    "timestamp": False,
                    "timestamp_format": "%S",
                    "edit_interval": 2.5,
                    "stale_ttl_seconds": 45,
                    "redact_secrets": False,
                },
                "delegates": {"enabled": False},
                "assistant": {"max_lines": 1},
                "no_edit": {"interval_seconds": 10},
                "platforms": {"custom": {"strategy": "off"}},
            }
        }
    )

    assert section == {
        "enabled": False,
        "tools": {
            "enabled": True,
            "lines": 6,
            "preview_length": 80,
            "show_completed": False,
            "show_duration": False,
            "timestamp": False,
            "timestamp_format": "%S",
        },
        "delegates": {"enabled": False},
        "assistant": {"max_lines": 1},
        "renderer": {
            "strategy": "auto",
            "edit_interval": 2.5,
            "stale_ttl_seconds": 45,
            "redact_secrets": False,
        },
        "no_edit": {"interval_seconds": 10},
        "platforms": {"custom": {"strategy": "off"}},
    }


def test_legacy_missing_values_use_settings_defaults():
    section = extract_progress_tail_section({"tool_progress_tail": {}})

    assert section == {
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
        "delegates": {},
        "assistant": {},
        "renderer": {
            "strategy": "auto",
            "edit_interval": 5.0,
            "stale_ttl_seconds": 900,
            "redact_secrets": True,
        },
        "no_edit": {},
        "platforms": {},
    }


def test_bare_mapping_is_supported_and_deep_copied():
    config = {"tools": {"lines": 4}, "platforms": {"invented": {"enabled": True}}}

    section = extract_progress_tail_section(config)

    assert section == config
    assert section is not config
    assert section["tools"] is not config["tools"]
    assert section["platforms"]["invented"] is not config["platforms"]["invented"]


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (None, {}),
        (["not", "a", "mapping"], {}),
        (
            {"progress_tail": "bad", "tools": {"lines": 4}},
            {"progress_tail": "bad", "tools": {"lines": 4}},
        ),
        (
            {"progress_tail": [], "tool_progress_tail": {"defaults": {"lines": 7}}},
            {
                "enabled": True,
                "tools": {
                    "enabled": True,
                    "lines": 7,
                    "preview_length": 120,
                    "show_completed": True,
                    "show_duration": True,
                    "timestamp": True,
                    "timestamp_format": "%H:%M",
                },
                "delegates": {},
                "assistant": {},
                "renderer": {
                    "strategy": "auto",
                    "edit_interval": 5.0,
                    "stale_ttl_seconds": 900,
                    "redact_secrets": True,
                },
                "no_edit": {},
                "platforms": {},
            },
        ),
    ],
)
def test_malformed_wrappers_follow_extraction_precedence(config, expected):
    assert extract_progress_tail_section(config) == expected


def test_malformed_nested_values_are_ignored_by_diagnostics():
    config = {
        "progress_tail": {
            "tools": ["bad"],
            "renderer": None,
            "platforms": {"string": "bad", "list": [], "number": 3},
        }
    }

    assert find_unknown_config_keys(config) == []
    assert find_retired_config_keys(config) == []


def test_unknown_keys_are_recursive_and_sorted():
    config = {
        "progress_tail": {
            "z_unknown": True,
            "tools": {"z": 1, "a": 2},
            "renderer": {"mystery": True},
            "platforms": {"arbitrary-platform": {"z": 1, "a": 2}},
        }
    }

    assert find_unknown_config_keys(config) == [
        "progress_tail.platforms.arbitrary-platform.a",
        "progress_tail.platforms.arbitrary-platform.z",
        "progress_tail.renderer.mystery",
        "progress_tail.tools.a",
        "progress_tail.tools.z",
        "progress_tail.z_unknown",
    ]


def test_retired_keys_have_exact_order_and_are_separate_from_unknowns():
    config = {
        "progress_tail": {
            "telegram": {
                "details_open_on_failure": True,
                "collapsible_details": True,
                "unknown": 1,
            },
            "background_jobs": {"default_notify_on_complete": False, "unknown": 2},
            "finalization": {},
        }
    }

    assert find_retired_config_keys(config) == [
        "progress_tail.finalization",
        "progress_tail.background_jobs.default_notify_on_complete",
        "progress_tail.telegram.collapsible_details",
        "progress_tail.telegram.details_open_on_failure",
    ]
    assert find_unknown_config_keys(config) == [
        "progress_tail.background_jobs.unknown",
        "progress_tail.telegram.unknown",
    ]


@pytest.mark.parametrize(
    "pair",
    [
        ("tools", "tools_enabled"),
        ("assistant", "assistant_enabled"),
        ("reasoning", "reasoning_enabled"),
        ("delegates", "delegates_enabled"),
        ("background_jobs", "background_jobs_enabled"),
    ],
)
def test_all_deliberate_platform_alias_pairs_are_accepted_for_arbitrary_platforms(pair):
    short, explicit = pair
    config = {
        "progress_tail": {"platforms": {"my-custom-platform": {short: True, explicit: False}}}
    }

    assert find_unknown_config_keys(config) == []


@pytest.mark.parametrize(
    "operation",
    [extract_progress_tail_section, find_unknown_config_keys, find_retired_config_keys],
)
@pytest.mark.parametrize(
    "config",
    [
        {"progress_tail": {"tools": {"unknown": [1, {"nested": True}]}}},
        {"tool_progress_tail": {"defaults": {"lines": 6}, "platforms": {"x": {"bad": []}}}},
        {"tools": {"unknown": {"deep": [1]}}, "finalization": {}},
    ],
)
def test_extraction_and_diagnostics_never_mutate_caller_inputs(operation, config):
    before = deepcopy(config)

    operation(config)

    assert config == before


@pytest.mark.parametrize("shape", ["current", "bare", "legacy"])
def test_opaque_leaves_are_atomic_while_configuration_containers_are_owned(shape):
    leaf = OpaqueLeaf()
    section = {
        "platforms": {
            "custom": {
                "opaque": leaf,
                "nested": [{"value": leaf}],
                "tuple_value": (leaf,),
                "set_value": {leaf},
            }
        }
    }
    if shape == "current":
        config = {"progress_tail": section}
    elif shape == "legacy":
        config = {"tool_progress_tail": section}
    else:
        config = section

    extracted = extract_progress_tail_section(config)

    custom = extracted["platforms"]["custom"]
    source_custom = section["platforms"]["custom"]
    assert extracted["platforms"] is not section["platforms"]
    assert custom is not source_custom
    assert custom["nested"] is not source_custom["nested"]
    assert custom["nested"][0] is not source_custom["nested"][0]
    assert custom["tuple_value"] is not source_custom["tuple_value"]
    assert custom["set_value"] is not source_custom["set_value"]
    assert custom["opaque"] is leaf
    assert custom["nested"][0]["value"] is leaf
    assert custom["tuple_value"][0] is leaf
    assert next(iter(custom["set_value"])) is leaf
    assert load_settings(config).platforms["custom"]["opaque"] is leaf
    assert find_unknown_config_keys(config) == [
        "progress_tail.platforms.custom.nested",
        "progress_tail.platforms.custom.opaque",
        "progress_tail.platforms.custom.set_value",
        "progress_tail.platforms.custom.tuple_value",
    ]
    assert find_retired_config_keys(config) == []
    if shape == "current":
        assert config == {"progress_tail": section}
    elif shape == "legacy":
        assert config == {"tool_progress_tail": section}
    else:
        assert config == section


@pytest.mark.parametrize("shape", ["current", "bare", "legacy"])
@pytest.mark.parametrize("container_type", [dict, list])
def test_cyclic_mutable_containers_are_copied_with_self_cycles_preserved(shape, container_type):
    cyclic = container_type()
    if container_type is dict:
        cyclic["self"] = cyclic
    else:
        cyclic.append(cyclic)
    section = {"platforms": {"custom": {"cyclic": cyclic}}}

    extracted = extract_progress_tail_section(_config_for_shape(shape, section))

    copied = extracted["platforms"]["custom"]["cyclic"]
    assert copied is not cyclic
    assert (copied["self"] if container_type is dict else copied[0]) is copied
    assert (cyclic["self"] if container_type is dict else cyclic[0]) is cyclic


@pytest.mark.parametrize("shape", ["current", "bare", "legacy"])
def test_shared_container_aliases_are_preserved_in_the_owned_copy(shape):
    shared = [{"value": 1}]
    section = {"platforms": {"custom": {"left": shared, "right": shared}}}

    extracted = extract_progress_tail_section(_config_for_shape(shape, section))

    custom = extracted["platforms"]["custom"]
    assert custom["left"] is custom["right"]
    assert custom["left"] is not shared
    assert custom["left"][0] is not shared[0]
    assert section["platforms"]["custom"]["left"] is shared
    assert section["platforms"]["custom"]["right"] is shared


@pytest.mark.parametrize("shape", ["current", "bare", "legacy"])
def test_indirect_tuple_list_cycles_are_preserved_in_the_owned_copy(shape):
    mutable = []
    cycle = (mutable,)
    mutable.append(cycle)
    section = {"platforms": {"custom": {"cycle": cycle}}}

    extracted = extract_progress_tail_section(_config_for_shape(shape, section))

    copied_tuple = extracted["platforms"]["custom"]["cycle"]
    copied_list = copied_tuple[0]
    assert copied_tuple is not cycle
    assert copied_list is not mutable
    assert copied_list[0] is copied_tuple
    assert mutable[0] is cycle
