"""Base-safe characterization for the final structural checker."""

import subprocess
from pathlib import Path

from scripts.check_structure import line_limit_violations, repository_violations, tracked_text_files

ROOT = Path(__file__).parents[1]


def _git(root: Path, *args: str):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_base_line_and_inventory_contract(tmp_path):
    _git(tmp_path, "init", "-q")
    exact = tmp_path / "exact.txt"
    over = tmp_path / "over.txt"
    binary = tmp_path / "binary"
    missing = tmp_path / "missing.txt"
    untracked = tmp_path / "untracked"
    exact.write_bytes(b"x\n" * 600)
    over.write_bytes(b"x\n" * 601)
    binary.write_bytes(b"x\n" * 700 + b"\0")
    missing.write_text("removed after tracking")
    untracked.write_text("ignored")
    _git(tmp_path, "add", "exact.txt", "over.txt", "binary", "missing.txt")
    missing.unlink()
    paths = tracked_text_files(tmp_path)
    assert paths == (exact, over)
    assert line_limit_violations(paths) == ((over, 601),)


def test_live_base_checker_is_clean():
    assert repository_violations(ROOT) == ()
