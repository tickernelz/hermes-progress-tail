import asyncio
from pathlib import Path

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


def test_focused_footer_renders_normal_environment_snapshot():
    async def run():
        adapter = EditableAdapter()
        renderer = make_renderer()
        env = EnvironmentSnapshot(
            context_tokens=82_000,
            context_window=256_000,
            context_kind="est",
            model="gpt-5.5",
            provider="custom",
            profile="default",
            cwd="/home/zhafron/Projects/hermes-progress-tail",
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
        assert "ctx 82k/256k est · custom:gpt-5.5 · profile default · live_tail" in content
        assert "git main* +1 · worktree main · cwd ~/Projects/hermes-progress-tail" in content

    asyncio.run(run())


def test_footer_does_not_collapse_project_root_to_dot(monkeypatch):
    async def run():
        monkeypatch.setattr(footer_module.Path, "home", lambda: Path("/home/runner"))
        adapter = EditableAdapter()
        renderer = make_renderer()
        env = EnvironmentSnapshot(
            model="gpt-5.5",
            provider="custom",
            cwd="/home/zhafron/Projects/hermes-progress-tail",
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
            lambda: Path("/home/zhafron/Projects/hermes-progress-tail"),
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
        assert "ctx 82k/256k est · custom:gpt-5.5 · profile default · live_tail" in content
        assert (
            "git feature/footer* +2 -1 · worktree hermes-progress-tail · cwd ~/Projects/hermes-progress-tail"
            in content
        )

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
                cwd="/home/zhafron/Projects/hermes-progress-tail",
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

    plugin._renderer = None
    monkeypatch.setattr(
        plugin,
        "_load_runtime_settings",
        lambda: load_settings({"progress_tail": {"footer": {"density": "compact"}}}),
    )

    status = plugin._command("status")

    assert "footer=enabled density:compact max_path_chars:56" in status
