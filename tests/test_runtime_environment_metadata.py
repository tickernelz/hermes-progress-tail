import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from hermes_progress_tail.models.state import EnvironmentSnapshot, SessionContext
from hermes_progress_tail.runtime import environment as env


def ctx(environment=None):
    value = SessionContext("s", "k", "p", "c", None, None, None)
    value.environment = environment
    return value


def test_session_identifiers():
    assert env._agent_session_id(SimpleNamespace(session_id=7)) == "7"
    assert env._agent_session_id(SimpleNamespace()) == ""
    assert (
        env._agent_session_key(SimpleNamespace(gateway_session_key="a", _gateway_session_key="b"))
        == "a"
    )
    assert env._agent_session_key(SimpleNamespace(_gateway_session_key="b")) == "b"


def test_update_agent_none_terminal_precedence_and_atomic_failure(monkeypatch, tmp_path):
    current = EnvironmentSnapshot(cwd=str(tmp_path / "terminal"), model="old")
    value = ctx(current)
    env._update_environment_from_agent(value, None)
    assert value.environment is current
    value._progress_tail_cwd_source = "terminal"
    value.strategy = "snapshot"
    monkeypatch.setattr(
        env,
        "_runtime_git_snapshot",
        lambda cwd: {
            "branch": "main",
            "dirty": True,
            "ahead": "2",
            "behind": 1,
            "worktree": "repo",
        },
    )
    monkeypatch.setattr(env, "_runtime_profile_name", lambda: "profile")
    agent = SimpleNamespace(
        workdir=tmp_path / "agent",
        model="m",
        provider="p",
        reasoning_effort=" high ",
        context_length=100,
        context_compressor=SimpleNamespace(last_prompt_tokens=73),
    )
    env._update_environment_from_agent(value, agent)
    assert value.environment.cwd == str(tmp_path / "terminal")
    assert (value.environment.model, value.environment.provider, value.environment.profile) == (
        "m",
        "p",
        "profile",
    )
    assert (
        value.environment.context_tokens,
        value.environment.context_window,
        value.environment.context_kind,
    ) == (73, 100, "est")
    assert (
        value.environment.git_branch,
        value.environment.git_dirty,
        value.environment.git_ahead,
        value.environment.git_behind,
        value.environment.worktree,
        value.environment.strategy,
    ) == ("main", True, 2, 1, "repo", "snapshot")
    assert value.environment.reasoning_effort == "high"
    before = value.environment
    monkeypatch.setattr(env, "_runtime_git_snapshot", lambda cwd: {"behind": "bad"})
    env._update_environment_from_agent(value, agent)
    assert value.environment is before


def test_update_agent_replaces_nonterminal_cwd(monkeypatch, tmp_path):
    value = ctx(EnvironmentSnapshot(cwd="old"))
    monkeypatch.setattr(env, "_runtime_git_snapshot", lambda cwd: {})
    monkeypatch.setattr(env, "_runtime_profile_name", lambda: "")
    env._update_environment_from_agent(value, SimpleNamespace(workdir=tmp_path))
    assert value.environment.cwd == str(tmp_path)
    assert value._progress_tail_cwd_source == "agent"


def test_context_token_precedence(monkeypatch):
    compressor = SimpleNamespace(
        awaiting_real_usage_after_compression=True,
        last_compression_rough_tokens="8",
        last_prompt_tokens=7,
    )
    monkeypatch.setattr(env, "_estimate_request_tokens", lambda *a: 9)
    assert env._context_tokens(None, compressor, [{}]) == 9
    monkeypatch.setattr(env, "_estimate_request_tokens", lambda *a: 0)
    assert env._context_tokens(None, compressor) == 8
    compressor.awaiting_real_usage_after_compression = False
    compressor.last_compression_rough_tokens = -1
    assert env._context_tokens(None, compressor) == 7
    assert env._context_tokens(None, SimpleNamespace(last_prompt_tokens="bad")) == 0


def metadata_module(request=None, messages=None):
    module = ModuleType("agent.model_metadata")
    if request is not None:
        module.estimate_request_tokens_rough = request
    if messages is not None:
        module.estimate_messages_tokens_rough = messages
    return module


def test_request_token_estimators(monkeypatch):
    agent_pkg = ModuleType("agent")
    agent_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "agent", agent_pkg)
    seen = {}

    def request(messages, **kwargs):
        seen.update(kwargs)
        return "12"

    monkeypatch.setitem(sys.modules, "agent.model_metadata", metadata_module(request, lambda _: 3))
    agent = SimpleNamespace(system_message="prompt", tools=["tool"])
    assert env._estimate_request_tokens(agent, [{}]) == 12
    assert seen == {"system_prompt": "prompt", "tools": ["tool"]}

    def fail(*args, **kwargs):
        raise ValueError

    monkeypatch.setitem(sys.modules, "agent.model_metadata", metadata_module(fail, lambda _: 6))
    assert env._estimate_request_tokens(agent, [{}]) == 6
    monkeypatch.setitem(sys.modules, "agent.model_metadata", metadata_module(fail, fail))
    assert env._estimate_request_tokens(agent, [{}]) == 0
    assert env._estimate_request_tokens(agent, []) == 0


def test_system_prompt_cwd_and_reasoning(monkeypatch, tmp_path):
    assert env._agent_system_prompt(SimpleNamespace(system_message=0, _system_message=9)) == "9"
    assert env._agent_system_prompt(SimpleNamespace()) == ""
    assert env._agent_cwd(SimpleNamespace(working_dir=tmp_path)) == tmp_path
    monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: tmp_path))
    assert env._agent_cwd(SimpleNamespace()) == tmp_path
    assert env._agent_reasoning_effort(SimpleNamespace(reasoningEffort=" low ")) == "low"
    assert (
        env._agent_reasoning_effort(
            SimpleNamespace(reasoning_config={"reasoning": {"effort": "high"}})
        )
        == "high"
    )
    assert (
        env._agent_reasoning_effort(
            SimpleNamespace(model_kwargs=SimpleNamespace(reasoning_effort="medium"))
        )
        == "medium"
    )
    assert env._agent_reasoning_effort(SimpleNamespace()) == ""


def test_profile_host_resolver(monkeypatch):
    pkg = ModuleType("hermes_cli")
    pkg.__path__ = []
    profiles = ModuleType("hermes_cli.profiles")
    profiles.get_active_profile_name = lambda: "hermes"
    monkeypatch.setitem(sys.modules, "hermes_cli", pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", profiles)
    assert env._runtime_profile_name() == "hermes"
    profiles.get_active_profile_name = lambda: (_ for _ in ()).throw(RuntimeError())
    assert env._runtime_profile_name() == ""


def test_terminal_and_replace_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(env, "_terminal_live_cwd", lambda task="": str(tmp_path / "live"))
    monkeypatch.setattr(env, "_runtime_git_snapshot", lambda path: {"branch": "b", "worktree": "w"})
    value = ctx(EnvironmentSnapshot(model="keep", profile="p", strategy="s"))
    env._update_environment_from_terminal(value, {"workdir": str(tmp_path / "arg")})
    assert (value.environment.cwd, value.environment.model, value.environment.git_branch) == (
        str(tmp_path / "live"),
        "keep",
        "b",
    )
    assert value._progress_tail_cwd_source == "terminal"
    monkeypatch.setattr(env, "_terminal_live_cwd", lambda task="": "")
    env._update_environment_from_terminal(value, {"workdir": str(tmp_path / "arg")})
    assert value.environment.cwd == str(tmp_path / "arg")
    before = value.environment
    env._update_environment_from_terminal(value, None)
    assert value.environment is before


def test_terminal_live_cwd_default_and_exception(monkeypatch):
    tools = ModuleType("tools")
    tools.__path__ = []
    terminal = ModuleType("tools.terminal_tool")
    seen = []
    terminal.get_active_env = lambda task: seen.append(task) or SimpleNamespace(cwd="/synthetic")
    monkeypatch.setitem(sys.modules, "tools", tools)
    monkeypatch.setitem(sys.modules, "tools.terminal_tool", terminal)
    assert env._terminal_live_cwd() == "/synthetic" and seen == ["default"]
    terminal.get_active_env = lambda task: (_ for _ in ()).throw(RuntimeError())
    assert env._terminal_live_cwd("x") == ""


def test_replace_initializes_and_swallow_conversion(monkeypatch, tmp_path):
    value = ctx(None)
    monkeypatch.setattr(env, "_runtime_git_snapshot", lambda path: {})
    monkeypatch.setattr(env, "_runtime_profile_name", lambda: "p")
    env._replace_environment_cwd(value, tmp_path, source="terminal")
    assert (value.environment.profile, value.environment.strategy) == ("p", "auto")

    class Bad:
        def __str__(self):
            raise ValueError

    before = value.environment
    env._replace_environment_cwd(value, Bad(), source="agent")
    assert value.environment is before
