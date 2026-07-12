import pytest

from hermes_progress_tail.settings.coercion import (
    as_bool,
    as_delegate_thinking,
    as_density,
    as_float,
    as_footer_density,
    as_int,
    as_patch_detail,
    as_strategy,
    as_style,
    renderer_mode_and_density,
)


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (True, False, True),
        (False, True, False),
        (None, True, True),
        (None, False, False),
        ("  YES ", False, True),
        ("true", False, True),
        ("1", False, True),
        ("on", False, True),
        ("false", True, False),
        ("anything", True, False),
        (1, False, True),
        (0, True, False),
        ([], True, False),
        ([1], False, True),
    ],
)
def test_as_bool_preserves_truth_table(value, default, expected):
    assert as_bool(value, default) is expected


@pytest.mark.parametrize(
    ("value", "default", "minimum", "expected"),
    [
        ("7", 3, 1, 7),
        ("bad", 3, 1, 3),
        (None, 3, 1, 3),
        (True, 3, 1, 1),
        (False, 3, 0, 0),
        (0, 3, 0, 0),
        (0, 3, 1, 3),
        (-1, 3, 0, 3),
    ],
)
def test_as_int_preserves_parsing_and_inclusive_minimum(value, default, minimum, expected):
    assert as_int(value, default, min_value=minimum) == expected


@pytest.mark.parametrize(
    ("value", "default", "minimum", "expected"),
    [
        ("1.5", 5.0, 0.0, 1.5),
        ("bad", 5.0, 0.0, 5.0),
        (None, 5.0, 0.0, 5.0),
        (True, 5.0, 0.0, 1.0),
        (0.0, 5.0, 0.0, 5.0),
        (-1.0, 5.0, 0.0, 5.0),
        (2.0, 5.0, 2.0, 5.0),
        (2.1, 5.0, 2.0, 2.1),
    ],
)
def test_as_float_preserves_parsing_and_strict_minimum(value, default, minimum, expected):
    assert as_float(value, default, min_value=minimum) == expected


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (" LIVE_TAIL ", "auto", "live_tail"),
        ("snapshot", "auto", "snapshot"),
        ("off", "auto", "off"),
        ("invalid", "off", "off"),
        (None, "snapshot", "snapshot"),
        ("", "auto", "auto"),
    ],
)
def test_as_strategy_normalizes_and_falls_back(value, default, expected):
    assert as_strategy(value, default) == expected


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (" PLAIN ", "emoji", "plain"),
        ("emoji", "plain", "emoji"),
        ("unknown", "plain", "emoji"),
        (None, "plain", "plain"),
    ],
)
def test_as_style_normalizes_with_emoji_fallback(value, default, expected):
    assert as_style(value, default) == expected


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        ("compact", "normal", "compact"),
        (" NORMAL ", "compact", "normal"),
        ("verbose", "normal", "verbose"),
        ("debug", "normal", "debug"),
        (None, "verbose", "verbose"),
        ("invalid", "verbose", "normal"),
    ],
)
def test_as_density_accepts_values_and_invalid_always_falls_back_to_normal(
    value, default, expected
):
    assert as_density(value, default) == expected


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        ("compact", "normal", "compact"),
        (" NORMAL ", "compact", "normal"),
        ("debug", "normal", "debug"),
        (None, "debug", "debug"),
        ("verbose", "debug", "normal"),
        ("invalid", "compact", "normal"),
    ],
)
def test_as_footer_density_preserves_valid_set_and_normal_fallback(value, default, expected):
    assert as_footer_density(value, default) == expected


@pytest.mark.parametrize(
    ("raw", "default_mode", "default_density", "expected"),
    [
        ({}, "sectioned", "normal", ("sectioned", "normal")),
        ({"mode": "compact", "density": "debug"}, "sectioned", "normal", ("sectioned", "compact")),
        ({"mode": "focused", "density": "verbose"}, "sectioned", "normal", ("focused", "verbose")),
        ({"mode": "sectioned", "density": "debug"}, "focused", "compact", ("sectioned", "debug")),
        ({"mode": "unknown", "density": "verbose"}, "focused", "normal", ("sectioned", "verbose")),
        ({"mode": "  FOCUSED  "}, "sectioned", "normal", ("focused", "normal")),
        ({"mode": "", "density": None}, "focused", "debug", ("focused", "debug")),
    ],
)
def test_renderer_mode_and_density_preserves_modes(raw, default_mode, default_density, expected):
    assert renderer_mode_and_density(raw, default_mode, default_density) == expected


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        ("off", "smart", "off"),
        ("path", "smart", "path"),
        (" SMART ", "off", "smart"),
        ("stats", "smart", "stats"),
        ("invalid", "path", "path"),
        (None, "stats", "stats"),
    ],
)
def test_as_patch_detail_preserves_valid_set(value, default, expected):
    assert as_patch_detail(value, default) == expected


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (" SUMMARY ", "off", "summary"),
        ("off", "summary", "off"),
        ("invalid", "summary", "off"),
        (None, "summary", "summary"),
    ],
)
def test_as_delegate_thinking_preserves_summary_or_off(value, default, expected):
    assert as_delegate_thinking(value, default) == expected
