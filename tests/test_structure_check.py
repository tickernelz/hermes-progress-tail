import os
import subprocess
import sys
from pathlib import Path

from scripts.check_structure import (
    line_limit_violations,
    repository_violations,
    tracked_text_files,
)

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "check_structure.py"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def test_line_limit_uses_exact_boundary_and_skips_nul_binary(tmp_path):
    exact = tmp_path / "exact.py"
    over = tmp_path / "over.py"
    binary = tmp_path / "binary.bin"
    exact.write_bytes(b"x\n" * 600)
    over.write_bytes(b"x\n" * 601)
    binary.write_bytes((b"x\n" * 700) + b"\0")

    assert line_limit_violations((exact, over, binary)) == ((over, 601),)


def test_violations_are_sorted_by_descending_count_then_path(tmp_path):
    alpha = tmp_path / "alpha.py"
    beta = tmp_path / "beta.py"
    largest = tmp_path / "largest.py"
    alpha.write_bytes(b"x\n" * 601)
    beta.write_bytes(b"x\n" * 601)
    largest.write_bytes(b"x\n" * 602)

    assert line_limit_violations((beta, alpha, largest)) == (
        (largest, 602),
        (alpha, 601),
        (beta, 601),
    )


def test_line_limit_skips_missing_paths_and_directories(tmp_path):
    missing = tmp_path / "missing.py"

    assert line_limit_violations((missing, tmp_path)) == ()


def test_tracked_text_files_uses_git_and_skips_missing_and_binary(tmp_path):
    _git(tmp_path, "init", "-q")
    text = tmp_path / "space name.txt"
    binary = tmp_path / "binary.bin"
    missing = tmp_path / "missing.txt"
    untracked = tmp_path / "untracked.txt"
    non_utf8_name = os.fsdecode(b"non-utf8-\xff.txt")
    non_utf8 = tmp_path / non_utf8_name
    text.write_text("hello\n", encoding="utf-8")
    binary.write_bytes(b"a\0b")
    missing.write_text("gone\n", encoding="utf-8")
    untracked.write_text("ignore\n", encoding="utf-8")
    non_utf8.write_text("decoded with surrogateescape\n", encoding="utf-8")
    _git(
        tmp_path,
        "add",
        "space name.txt",
        "binary.bin",
        "missing.txt",
        non_utf8_name,
    )
    missing.unlink()

    assert tracked_text_files(tmp_path) == tuple(sorted((non_utf8, text), key=str))


def test_repository_structure_is_valid():
    assert repository_violations(ROOT) == ()


def test_cli_success_is_quiet():
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_cli_reports_deterministic_diagnostics_and_exits_one(tmp_path):
    _git(tmp_path, "init", "-q")
    alpha = tmp_path / "alpha.py"
    zeta = tmp_path / "zeta.py"
    alpha.write_bytes(b"x\n" * 601)
    zeta.write_bytes(b"x\n" * 602)
    _git(tmp_path, "add", "alpha.py", "zeta.py")

    result = subprocess.run(
        [sys.executable, str(SCRIPT)], cwd=tmp_path, capture_output=True, text=True
    )

    assert result.returncode == 1
    assert result.stdout.splitlines() == [
        "zeta.py: 602 lines (limit 600)",
        "alpha.py: 601 lines (limit 600)",
    ]
    assert result.stderr == ""
