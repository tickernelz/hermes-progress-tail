# Task D1 Report — Settings compatibility characterization

## Status

Completed as characterization-only test work against baseline `a9d7b7a504ff80f3739c2e4fbbd7ca592b4157b4`. No production source was changed.

## Changes

Created `tests/test_settings_compatibility.py` to freeze current observable behavior:

- exact `Settings()` dataclass tree and computed `Settings.defaults` values;
- accepted malformed and boundary coercion behavior for booleans, strings, integers, floats, minima, zero-valued fields, style/density, and compact renderer-mode normalization;
- extraction precedence for current nested, legacy nested, bare, malformed-current, and simultaneous current/legacy inputs;
- input immutability across loading and both diagnostic functions;
- config module facade identity and public object identity;
- documented compatibility facades and their primary object identities;
- exact ordered pre-D `runtime.plugin.__all__` tuple, including checks against baseline disappearance and underscore-prefixed expansion.

## Verification

- Controller-supplied pre-edit run: `tests/test_config.py tests/test_config_contract.py -q` — **9 passed** (observed green twice).
- Exact focused command after creation: `.venv/bin/python -m pytest tests/test_settings_compatibility.py tests/test_config.py tests/test_config_contract.py -q` — **48 passed**.
- Full suite: `.venv/bin/python -m pytest -q` — **1171 passed**, with one pre-existing/runtime warning about an un-awaited `ProgressRenderer.finalize` coroutine in `test_interrupt_non_stop_and_callback_failure_preserve_native_result`.
- `ruff check .` — passed.
- `ruff format --check .` — passed (191 files already formatted).
- `git diff --check` — passed.
- New test file is 333 lines, below the 600-line limit.

## Self-review

Assertions record actual current behavior rather than preferred behavior. The diff is test/report-only, production files are untouched, and no helper has been exported or otherwise promoted into the public API. Compatibility assertions import only already-observable facade objects; the explicit private-name check freezes the existing runtime export surface without adding to it.

## Important-finding follow-up

- Parameterized input immutability over representative current nested, legacy nested, bare, malformed-current-wrapper, and simultaneous current/legacy configurations. Each case is deep-copied, then checked unchanged after `load_settings`, `find_unknown_config_keys`, and `find_retired_config_keys` individually.
- Added explicit GREEN characterization of current integer coercion for `False`: `tools.lines` falls back to `3` (alongside the existing `True -> 1` case).
- Retained the plan-required baseline-disappearance and private-name assertions. Minor review note recorded for final review: exact runtime `__all__` equality makes the later set assertions logically redundant; no scope expansion was made.

## Follow-up verification

- Covering command: `.venv/bin/python -m pytest tests/test_settings_compatibility.py tests/test_config.py tests/test_config_contract.py -q` — **53 passed**.
- Full suite on final test code: `.venv/bin/python -m pytest -q` — **1176 passed**, with the same pre-existing/runtime un-awaited `ProgressRenderer.finalize` warning noted above.
- Changed-test Ruff: `.venv/bin/ruff check tests/test_settings_compatibility.py` — **passed**; `.venv/bin/ruff format --check tests/test_settings_compatibility.py` — **1 file already formatted**.
- `git diff --check` — **passed**.
- Updated test file is 368 lines and this report is 46 lines, both below the 600-line limit.
