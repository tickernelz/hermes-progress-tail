import os
import subprocess
from pathlib import Path


def test_readme_omits_inline_version_note():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "Version:" not in readme
    assert "v0.1.0" not in readme


def test_curl_install_commands_are_documented():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert (
        "curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-progress-tail/v0.1.2/install.sh | bash"
        in readme
    )
    assert (
        "curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-progress-tail/v0.1.2/uninstall.sh | bash"
        in readme
    )


def test_shell_scripts_exist_and_are_executable():
    assert Path("install.sh").exists()
    assert Path("uninstall.sh").exists()
    assert Path("install.sh").stat().st_mode & 0o111
    assert Path("uninstall.sh").stat().st_mode & 0o111


def test_install_script_supports_local_source_dir(tmp_path):
    env = os.environ.copy()
    env["HPT_SOURCE_DIR"] = str(Path.cwd())
    env["HPT_DRY_RUN"] = "1"
    env["HERMES_HOME"] = str(tmp_path / "hermes")

    result = subprocess.run(
        ["bash", "install.sh"],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )

    assert "Would copy plugin" in result.stdout
    assert "Restart Hermes manually" in result.stdout
