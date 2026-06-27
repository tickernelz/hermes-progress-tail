import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import hermes_progress_tail.rendering.footer as footer_module
from hermes_progress_tail.config import load_settings
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.state import EnvironmentSnapshot, SessionContext, ToolEvent


class Result:
    def __init__(self, success=True, message_id=None, error=""):
        self.success = success
        self.message_id = message_id
        self.error = error


class EditableAdapter:
    name = "editable"

    def __init__(self):
        self.sent = []
        self.edits = []
        self.next_id = 1

    async def send(self, chat_id, content, metadata=None):
        message_id = f"m{self.next_id}"
        self.next_id += 1
        self.sent.append((chat_id, content, metadata))
        return Result(True, message_id)

    async def edit_message(self, chat_id, message_id, content):
        self.edits.append((chat_id, message_id, content))
        return Result(True, message_id)


@pytest.fixture(autouse=True)
def no_footer_update_check(monkeypatch):
    monkeypatch.setattr(footer_module, "_latest_release_info", lambda: None, raising=False)


def make_renderer(config=None):
    progress_tail = {
        "renderer": {"mode": "focused"},
        "tools": {"timestamp": False},
    }
    if config:
        progress_tail.update(config)
    return ProgressRenderer(load_settings({"progress_tail": progress_tail}))


def make_ctx(adapter, *, env=None):
    return SessionContext(
        "s1",
        "k1",
        "telegram",
        "chat",
        None,
        adapter,
        asyncio.get_running_loop(),
        "live_tail",
        timestamp=False,
        environment=env,
    )


def test_footer_defaults_on_with_normal_density():
    settings = load_settings({})

    assert settings.footer.enabled is True
    assert settings.footer.density == "normal"


def test_focused_footer_renders_normal_environment_snapshot(monkeypatch):
    async def run():
        monkeypatch.setattr(footer_module, "_latest_release_info", lambda: None, raising=False)
        adapter = EditableAdapter()
        renderer = make_renderer()
        env = EnvironmentSnapshot(
            context_tokens=82_000,
            context_window=256_000,
            context_kind="est",
            model="gpt-5.5",
            provider="custom",
            profile="default",
            cwd="/home/example/Projects/hermes-progress-tail",
            git_branch="main",
            git_dirty=True,
            git_ahead=1,
            worktree="main",
        )
        ctx = make_ctx(adapter, env=env)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )

        content = adapter.sent[-1][1]
        assert "**__Status__**" in content
        assert (
            "ctx 82k/256k est · compacted 0x · custom:gpt-5.5 · profile default · live_tail"
            in content
        )
        assert "git main* +1 · worktree main · cwd " in content
        assert "cwd ." not in content
        assert "hermes-progress-tail" in content

    asyncio.run(run())


def test_footer_renders_per_progress_compaction_count_and_resets_on_new_context(monkeypatch):
    async def run():
        monkeypatch.setattr(footer_module, "_latest_release_info", lambda: None, raising=False)
        env = EnvironmentSnapshot(
            context_tokens=82_000,
            context_window=256_000,
            context_kind="est",
            model="gpt-5.5",
            provider="custom",
            profile="default",
            cwd="/home/example/Projects/hermes-progress-tail",
        )
        adapter = EditableAdapter()
        renderer = make_renderer()
        ctx = make_ctx(adapter, env=env)
        renderer.register_context(ctx)

        assert ctx.compaction_count == 0
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "first"), force=True)
        assert "compacted 0x" in adapter.sent[-1][1]

        ctx.compaction_count = 2
        await renderer.handle_event(ToolEvent("s1", "k1", "telegram", "second"), force=True)
        assert "compacted 2x" in adapter.edits[-1][2]

        adapter2 = EditableAdapter()
        compact_renderer = make_renderer({"footer": {"density": "compact"}})
        fresh_ctx = make_ctx(adapter2, env=env)
        compact_renderer.register_context(fresh_ctx)
        await compact_renderer.handle_event(ToolEvent("s1", "k1", "telegram", "fresh"), force=True)

        assert fresh_ctx.compaction_count == 0
        assert "compacted 0x" in adapter2.sent[-1][1]

    asyncio.run(run())


def test_focused_footer_shows_github_latest_release_update_only_when_newer(monkeypatch):
    async def run():
        monkeypatch.setattr(
            footer_module,
            "_latest_release_info",
            lambda: {"tag_name": "v0.1.96", "html_url": "https://example.test/v0.1.96"},
            raising=False,
        )
        adapter = EditableAdapter()
        renderer = make_renderer()
        env = EnvironmentSnapshot(
            context_tokens=82_000,
            context_window=256_000,
            model="gpt-5.5",
            provider="custom",
            profile="default",
            cwd="/home/example/Projects/hermes-progress-tail",
            git_branch="main",
            git_dirty=True,
        )
        ctx = make_ctx(adapter, env=env)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )
        content = adapter.sent[-1][1]

        assert "**__Status__**" in content
        assert "⬆️ update `v0.1.96`" in content
        assert "https://example.test/v0.1.96" in content
        assert content.rfind("**__Status__**") > content.rfind("**__Tools__**")

        monkeypatch.setattr(
            footer_module,
            "_latest_release_info",
            lambda: {"tag_name": "v0.1.95", "html_url": "https://example.test/v0.1.95"},
            raising=False,
        )
        adapter2 = EditableAdapter()
        renderer2 = make_renderer()
        ctx2 = make_ctx(adapter2, env=env)
        renderer2.register_context(ctx2)

        await renderer2.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )
        current_content = adapter2.sent[-1][1]

        assert "⬆️ update" not in current_content
        assert "v0.1.95" not in current_content

    asyncio.run(run())


def test_footer_update_check_uses_github_release_not_local_git_status(monkeypatch):
    async def run():
        calls = []

        def latest_release():
            calls.append("github")
            return None

        monkeypatch.setattr(footer_module, "_latest_release_info", latest_release, raising=False)
        adapter = EditableAdapter()
        renderer = make_renderer()
        env = EnvironmentSnapshot(
            model="gpt-5.5",
            provider="custom",
            cwd="/home/example/Projects/hermes-progress-tail",
            git_branch="main",
            git_dirty=True,
            git_ahead=99,
        )
        ctx = make_ctx(adapter, env=env)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )
        content = adapter.sent[-1][1]

        assert calls == ["github"]
        assert "git main* +99" in content
        assert "⬆️ update" not in content

    asyncio.run(run())


def test_footer_does_not_collapse_project_root_to_dot(monkeypatch):
    async def run():
        monkeypatch.setattr(footer_module.Path, "home", lambda: Path("/home/runner"))
        adapter = EditableAdapter()
        renderer = make_renderer()
        env = EnvironmentSnapshot(
            model="gpt-5.5",
            provider="custom",
            cwd="/home/example/Projects/hermes-progress-tail",
        )
        ctx = make_ctx(adapter, env=env)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )

        content = adapter.sent[-1][1]
        assert "cwd ." not in content
        assert "cwd hermes-progress-tail" in content

    asyncio.run(run())


def test_focused_footer_hides_unknown_fields_and_can_be_disabled():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer({"footer": {"enabled": False}})
        ctx = make_ctx(adapter, env=EnvironmentSnapshot(model="gpt-5.5"))
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )

        assert "**__Status__**" not in adapter.sent[-1][1]

    asyncio.run(run())


def test_runtime_hook_updates_footer_environment_from_agent(monkeypatch):
    async def run():
        import hermes_progress_tail.plugin as plugin

        adapter = EditableAdapter()
        plugin._renderer = None
        monkeypatch.setattr(
            plugin,
            "_load_runtime_settings",
            lambda: load_settings(
                {
                    "progress_tail": {
                        "renderer": {"mode": "focused"},
                        "tools": {"timestamp": False},
                        "reasoning": {"min_update_chars": 1},
                    }
                }
            ),
        )
        monkeypatch.setattr(plugin, "_runtime_profile_name", lambda: "default", raising=False)
        monkeypatch.setattr(
            plugin,
            "_git_snapshot",
            lambda cwd: {
                "branch": "feature/footer",
                "dirty": True,
                "ahead": 2,
                "behind": 1,
                "worktree": "hermes-progress-tail",
            },
            raising=False,
        )
        monkeypatch.setattr(
            plugin.Path,
            "cwd",
            lambda: Path("/home/example/Projects/hermes-progress-tail"),
        )
        agent = type(
            "Agent",
            (),
            {
                "session_id": "s1",
                "_gateway_session_key": "k1",
                "model": "custom/gpt-5.5",
                "provider": "custom:local",
                "_config_context_length": 256_000,
                "context_compressor": type("Compressor", (), {"last_estimated_tokens": 82_000})(),
            },
        )()
        renderer = plugin._get_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        plugin.on_reasoning_delta_from_agent(agent, "runtime metadata")
        await asyncio.sleep(0.05)

        content = adapter.sent[-1][1]
        assert (
            "ctx 82k/256k est · compacted 0x · custom:gpt-5.5 · profile default · live_tail"
            in content
        )
        assert "git feature/footer* +2 -1 · worktree hermes-progress-tail · cwd " in content
        assert "cwd ." not in content
        assert "hermes-progress-tail" in content

    asyncio.run(run())


def test_terminal_post_tool_updates_footer_cwd_from_live_terminal_env(monkeypatch):
    async def run():
        import hermes_progress_tail.plugin as plugin

        adapter = EditableAdapter()
        plugin._renderer = None
        monkeypatch.setattr(
            plugin,
            "_load_runtime_settings",
            lambda: load_settings(
                {
                    "progress_tail": {
                        "renderer": {"mode": "focused"},
                        "tools": {"timestamp": False},
                    }
                }
            ),
        )
        monkeypatch.setattr(plugin, "_runtime_profile_name", lambda: "dev_profile", raising=False)
        monkeypatch.setattr(plugin, "_git_snapshot", lambda cwd: {}, raising=False)
        live_env = SimpleNamespace(cwd="/tmp/project/subdir")
        terminal_module = SimpleNamespace(get_active_env=lambda task_id: live_env)
        monkeypatch.setitem(sys.modules, "tools", SimpleNamespace(terminal_tool=terminal_module))
        monkeypatch.setitem(sys.modules, "tools.terminal_tool", terminal_module)

        renderer = plugin._get_renderer()
        ctx = make_ctx(
            adapter,
            env=EnvironmentSnapshot(
                context_tokens=82_000,
                context_window=256_000,
                context_kind="est",
                model="gpt-5.5",
                provider="custom",
                profile="dev_profile",
                cwd="/home/example/.hermes/profiles/dev_profile",
            ),
        )
        renderer.register_context(ctx)

        plugin._on_post_tool_call(
            "terminal",
            {"command": "cd /tmp/project/subdir && pwd"},
            result='{"output":"/tmp/project/subdir", "exit_code":0}',
            task_id="k1",
            session_id="s1",
            tool_call_id="terminal-1",
        )
        await asyncio.sleep(0.05)

        assert ctx.environment.cwd == "/tmp/project/subdir"
        content = adapter.sent[-1][1]
        assert (
            "ctx 82k/256k est · compacted 0x · custom:gpt-5.5 · profile dev_profile · live_tail"
            in content
        )
        assert "cwd /tmp/project/subdir" in content
        assert "profiles/dev_profile" not in content

    asyncio.run(run())


def test_tool_hooks_refresh_footer_context_from_current_agent(monkeypatch):
    async def run():
        import hermes_progress_tail.plugin as plugin
        from hermes_progress_tail.monkeypatches import (
            install_monkeypatches,
            uninstall_monkeypatches,
        )

        class FakeCompressor:
            last_prompt_tokens = 125_000
            context_length = 256_000

        class FakeAgent:
            session_id = "s1"
            _gateway_session_key = "k1"
            model = "custom/gpt-5.5"
            provider = "custom:local"
            context_compressor = FakeCompressor()

            def __init__(self):
                self.stream_delta_callback = None
                self.reasoning_callback = None

            def _fire_reasoning_delta(self, *_args, **_kwargs):
                return None

            def _emit_interim_assistant_message(self, *_args, **_kwargs):
                return None

            def _invoke_tool(
                self,
                function_name,
                function_args,
                effective_task_id,
                tool_call_id=None,
                messages=None,
                pre_tool_block_checked=False,
                skip_tool_request_middleware=False,
                tool_request_middleware_trace=None,
            ):
                plugin._on_pre_tool_call(
                    function_name,
                    function_args,
                    task_id=effective_task_id,
                    session_id=self.session_id,
                    tool_call_id=tool_call_id or "",
                )
                result = '{"output":"ok", "exit_code":0}'
                plugin._on_post_tool_call(
                    function_name,
                    function_args,
                    result=result,
                    task_id=effective_task_id,
                    session_id=self.session_id,
                    tool_call_id=tool_call_id or "",
                )
                return result

        adapter = EditableAdapter()
        plugin._renderer = None
        monkeypatch.setattr(
            plugin,
            "_load_runtime_settings",
            lambda: load_settings(
                {
                    "progress_tail": {
                        "renderer": {"mode": "focused"},
                        "tools": {"timestamp": False},
                    }
                }
            ),
        )
        monkeypatch.setattr(plugin, "_runtime_profile_name", lambda: "default", raising=False)
        monkeypatch.setattr(plugin, "_git_snapshot", lambda cwd: {}, raising=False)
        monkeypatch.setattr(plugin.Path, "cwd", lambda: Path("/tmp/project"))
        renderer = plugin._get_renderer()
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        install_monkeypatches(FakeAgent)
        try:
            FakeAgent()._invoke_tool("terminal", {"command": "pwd"}, "k1", tool_call_id="tc1")
            await asyncio.sleep(0.05)
        finally:
            uninstall_monkeypatches(FakeAgent)

        assert ctx.environment.context_tokens == 125_000
        assert ctx.environment.context_window == 256_000
        assert ctx.environment.model == "custom/gpt-5.5"
        assert ctx.environment.provider == "custom:local"
        assert "ctx 125k/256k est · compacted 0x · custom:gpt-5.5" in adapter.sent[-1][1]

    asyncio.run(run())


def test_footer_hides_when_only_strategy_is_known():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(adapter)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )

        assert adapter.sent[-1][1] == "▰ 🧰 Tools\nread_file: renderer.py"

    asyncio.run(run())


def test_default_sectioned_renderer_shows_footer_when_enabled():
    async def run():
        adapter = EditableAdapter()
        renderer = ProgressRenderer(
            load_settings({"progress_tail": {"tools": {"timestamp": False}}})
        )
        ctx = make_ctx(
            adapter,
            env=EnvironmentSnapshot(
                model="gpt-5.5",
                provider="custom",
                profile="default",
                cwd="/home/example/Projects/hermes-progress-tail",
            ),
        )
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )

        content = adapter.sent[-1][1]
        assert "▰ 🧭 Status" in content
        assert "custom:gpt-5.5 · profile default · live_tail" in content
        assert content.rfind("▰ 🧭 Status") > content.rfind("▰ 🧰 Tools")

    asyncio.run(run())


def test_status_command_reports_footer_config(monkeypatch):
    import hermes_progress_tail.plugin as plugin
    from hermes_progress_tail.runtime import commands

    plugin._renderer = None
    monkeypatch.setattr(
        plugin,
        "_load_runtime_settings",
        lambda: load_settings({"progress_tail": {"footer": {"density": "compact"}}}),
    )
    monkeypatch.setattr(commands, "_latest_release_info", lambda: None)

    status = plugin._command("status")

    assert "footer=enabled density:compact max_path_chars:56" in status
