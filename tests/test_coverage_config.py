import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_coverage_gate_targets_production_with_branch_measurement():
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    run = data["tool"]["coverage"]["run"]
    report = data["tool"]["coverage"]["report"]

    assert run["branch"] is True
    assert run["source"] == ["hermes_progress_tail"]
    assert report["fail_under"] >= 90
