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
        "curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-progress-tail/v0.1.27/install.sh | bash"
        in readme
    )
    assert (
        "curl -fsSL https://raw.githubusercontent.com/tickernelz/hermes-progress-tail/v0.1.27/uninstall.sh | bash"
        in readme
    )


def test_readme_keeps_install_section_simple_and_moves_env_options():
    readme = Path("README.md").read_text(encoding="utf-8")
    install_section = readme.split("## Install", 1)[1].split("## Install options", 1)[0]
    options_section = readme.split("## Install options", 1)[1].split("## Expected config", 1)[0]

    assert install_section.count("curl -fsSL") == 2
    assert "HPT_INTERACTIVE" not in install_section
    assert "HPT_DRY_RUN" not in install_section
    for name in (
        "HPT_INTERACTIVE",
        "HPT_DRY_RUN",
        "HPT_PROFILES",
        "HPT_ALL_PROFILES",
        "HERMES_HOME",
        "HPT_REPO",
        "HPT_REF",
        "HPT_SOURCE_DIR",
    ):
        assert name in options_section


def test_readme_documents_background_job_defaults_without_finalization_config():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "background_jobs:" in readme
    assert "finalization:" not in readme
    assert "cleanup_stale_on_next_turn" not in readme
    assert "mode: focused # focused|sectioned|compact" in readme
    assert "density: verbose # compact|normal|verbose|debug" in readme
    assert (
        "code_fence: auto # auto|on|off; auto fences Discord/Slack/Mattermost, not Telegram"
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
