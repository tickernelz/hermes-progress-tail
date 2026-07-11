from types import SimpleNamespace

import pytest

from hermes_progress_tail.runtime import environment as env


def test_git_cache_fresh_expired_separate_and_resolve_failure(monkeypatch, tmp_path):
    env._GIT_CACHE.clear()
    calls = []
    monkeypatch.setattr(
        env, "_load_git_snapshot", lambda path: calls.append(path) or {"n": len(calls)}
    )
    times = iter((10.0, 14.9, 15.0, 15.1))
    monkeypatch.setattr(env.time, "monotonic", lambda: next(times))
    first = env._git_snapshot(tmp_path / ".")
    assert env._git_snapshot(tmp_path) is first
    assert env._git_snapshot(tmp_path) == {"n": 2}
    assert env._git_snapshot(tmp_path / "other") == {"n": 3}
    assert len(calls) == 3

    class BadPath:
        def expanduser(self):
            raise OSError("no resolve")

        def __str__(self):
            return "synthetic"

    monkeypatch.setattr(env.time, "monotonic", lambda: 20.0)
    assert env._git_snapshot(BadPath()) == {"n": 4}
    env._GIT_CACHE.clear()


def test_load_git_snapshot_non_worktree_and_detached(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_git_command", lambda *_: "false")
    assert env._load_git_snapshot(tmp_path) == {}

    answers = iter(("true", "", "abc123", str(tmp_path / "repo"), "## HEAD (no branch)"))
    monkeypatch.setattr(env, "_git_command", lambda *_: next(answers))
    assert env._load_git_snapshot(tmp_path) == {
        "branch": "abc123",
        "dirty": False,
        "ahead": 0,
        "behind": 0,
        "worktree": "repo",
    }


@pytest.mark.parametrize(
    ("status", "ahead", "behind", "dirty"),
    [
        ("## main...origin/main [ahead 12]", 12, 0, False),
        ("## main [behind 3]", 0, 3, False),
        ("## main [ahead 2, behind 4]\n M x", 2, 4, True),
        ("## main [ahead nope, behind ?]\n\n", 0, 0, False),
    ],
)
def test_load_git_snapshot_status(monkeypatch, tmp_path, status, ahead, behind, dirty):
    answers = iter(("true", "main", str(tmp_path / "root"), status))
    monkeypatch.setattr(env, "_git_command", lambda *_: next(answers))
    result = env._load_git_snapshot(tmp_path)
    assert result == {
        "branch": "main",
        "dirty": dirty,
        "ahead": ahead,
        "behind": behind,
        "worktree": "root",
    }


@pytest.mark.parametrize(
    "line,label,expected",
    [
        ("plain", "ahead", 0),
        ("ahead 123]", "ahead", 123),
        ("ahead x", "ahead", 0),
        ("ahead 12x9", "ahead", 12),
    ],
)
def test_branch_count(line, label, expected):
    assert env._branch_count(line, label) == expected


def test_git_command_contract_and_failures(monkeypatch, tmp_path):
    calls = []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=" main \n")

    monkeypatch.setattr(env.subprocess, "run", run)
    assert env._git_command(tmp_path, "branch", "--show-current") == "main"
    assert calls == [
        (
            (["git", "branch", "--show-current"],),
            {
                "cwd": str(tmp_path),
                "text": True,
                "stdout": env.subprocess.PIPE,
                "stderr": env.subprocess.DEVNULL,
                "timeout": 0.15,
                "check": False,
            },
        )
    ]
    monkeypatch.setattr(
        env.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="x")
    )
    assert env._git_command(tmp_path, "x") == ""
    for error in (TimeoutError(), OSError()):
        monkeypatch.setattr(
            env.subprocess, "run", lambda *a, _error=error, **k: (_ for _ in ()).throw(_error)
        )
        assert env._git_command(tmp_path, "x") == ""


def test_runtime_git_snapshot_override_and_exception_fallback(monkeypatch, tmp_path):
    from hermes_progress_tail.runtime import plugin

    monkeypatch.setattr(plugin, "_git_snapshot", lambda cwd: {"branch": "override"})
    assert env._runtime_git_snapshot(tmp_path) == {"branch": "override"}
    monkeypatch.setattr(plugin, "_git_snapshot", lambda cwd: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(env, "_git_snapshot", lambda cwd: {"branch": "local"})
    assert env._runtime_git_snapshot(tmp_path) == {"branch": "local"}
