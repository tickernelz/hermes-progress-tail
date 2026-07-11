import os
import subprocess
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_project_requires_python_312_or_newer():
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert metadata["project"]["requires-python"] == ">=3.12"
    assert metadata["tool"]["ruff"]["target-version"] == "py312"


def _run_entrypoint(tmp_path: Path, script: str, version: str | None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    invocation_log = tmp_path / "python-invocations"
    if version is not None:
        fake_python = bin_dir / "python3"
        fake_python.write_text(
            "#!/bin/bash\n"
            'printf "%s\\n" "$*" >> "$FAKE_PYTHON_LOG"\n'
            'if [[ "$1" == "-c" ]]; then\n'
            f"  [[ '{version}' == '3.12' ]]\n"
            "  exit\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        fake_python.chmod(0o755)

    hermes_home = tmp_path / "untouched-hermes-home"
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "FAKE_PYTHON_LOG": str(invocation_log),
        "HERMES_HOME": str(hermes_home),
        "HPT_SOURCE_DIR": str(PROJECT_ROOT),
        "HPT_INTERACTIVE": "0",
        "HPT_DRY_RUN": "1",
    }
    result = subprocess.run(
        ["/bin/bash", str(PROJECT_ROOT / script)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    calls = (
        invocation_log.read_text(encoding="utf-8").splitlines() if invocation_log.exists() else []
    )
    assert not hermes_home.exists()
    return result, calls


def test_install_rejects_python_311_before_invoking_installer(tmp_path):
    result, calls = _run_entrypoint(tmp_path, "install.sh", "3.11")

    assert result.returncode != 0
    assert "Python 3.12 or newer is required" in result.stderr
    assert len(calls) == 1 and calls[0].startswith("-c ")


def test_install_accepts_python_312_and_reaches_dry_run_installer(tmp_path):
    result, calls = _run_entrypoint(tmp_path, "install.sh", "3.12")

    assert result.returncode == 0
    assert any("-m hermes_progress_tail.installer install" in call for call in calls)
    assert any("--dry-run" in call for call in calls)


def test_uninstall_legacy_and_current_python_reach_dry_run_installer(tmp_path):
    for version in ("3.11", "3.12"):
        case_dir = tmp_path / version
        case_dir.mkdir()
        result, calls = _run_entrypoint(case_dir, "uninstall.sh", version)

        assert result.returncode == 0
        assert any("-m hermes_progress_tail.installer uninstall" in call for call in calls)
        assert any("--dry-run" in call for call in calls)


def test_entrypoints_fail_safely_when_python3_is_missing(tmp_path):
    for script, message in (
        ("install.sh", "Python 3.12 or newer is required"),
        ("uninstall.sh", "python3 is required to uninstall"),
    ):
        case_dir = tmp_path / script
        case_dir.mkdir()
        result, calls = _run_entrypoint(case_dir, script, None)

        assert result.returncode != 0
        assert message in result.stderr
        assert calls == []


def test_tests_use_stdlib_tomllib_without_legacy_fallback():
    coverage_contract = (PROJECT_ROOT / "tests" / "test_coverage_config.py").read_text(
        encoding="utf-8"
    )

    assert "import tomllib" in coverage_contract
    assert "tomli" not in coverage_contract.replace("tomllib", "")
