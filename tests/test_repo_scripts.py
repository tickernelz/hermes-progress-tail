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
        "curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-progress-tail/v0.1.13/install.sh | bash"
        in readme
    )
    assert (
        "curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-progress-tail/v0.1.13/uninstall.sh | bash"
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
    env["HPT_INTERACTIVE"] = "0"
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


def test_install_script_defaults_to_interactive_when_not_dry_run():
    script = Path("install.sh").read_text(encoding="utf-8")

    assert "INTERACTIVE_DEFAULT=1" in script
    assert "--interactive --prompt-input /dev/tty" in script


def test_install_script_profiles_disable_default_interactive_prompting(tmp_path):
    env = os.environ.copy()
    env["HPT_SOURCE_DIR"] = str(Path.cwd())
    env["HPT_DRY_RUN"] = "1"
    env["HPT_PROFILES"] = "work,personal"
    env["HERMES_HOME"] = str(tmp_path / "hermes")
    (tmp_path / "hermes" / "profiles" / "work").mkdir(parents=True)
    (tmp_path / "hermes" / "profiles" / "work" / "config.yaml").write_text("{}\n")
    (tmp_path / "hermes" / "profiles" / "personal").mkdir(parents=True)
    (tmp_path / "hermes" / "profiles" / "personal" / "config.yaml").write_text("{}\n")

    result = subprocess.run(
        ["bash", "install.sh"],
        check=True,
        env=env,
        text=True,
        capture_output=True,
    )

    assert "[work]" in result.stdout
    assert "[personal]" in result.stdout
    assert "interactive installer" not in result.stdout
