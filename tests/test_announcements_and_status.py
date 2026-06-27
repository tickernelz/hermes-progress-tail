import asyncio
from types import SimpleNamespace

import pytest

from hermes_progress_tail.config import find_unknown_config_keys, load_settings
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


def make_renderer(*, mode="focused"):
    return ProgressRenderer(
        load_settings(
            {
                "progress_tail": {
                    "renderer": {"mode": mode},
                    "tools": {"timestamp": False},
                }
            }
        )
    )


def make_ctx(adapter, *, env=None, platform="telegram"):
    return SessionContext(
        "s1",
        "k1",
        platform,
        "chat",
        None,
        adapter,
        asyncio.get_running_loop(),
        "live_tail",
        timestamp=False,
        environment=env,
    )


def test_announcement_refresh_interval_is_three_minutes():
    from hermes_progress_tail.rendering.announcements import DEFAULT_TTL_SECONDS

    assert DEFAULT_TTL_SECONDS == 180.0


def test_announcement_markdown_sanitizer_keeps_markdown_but_removes_empty_noise():
    from hermes_progress_tail.rendering.announcements import sanitize_announcement_markdown

    markdown = """
<!-- internal comment -->
<script>alert('x')</script>
# Release **note**
![logo](https://example.test/logo.png)
- Use `/progresstail-update`
"""

    sanitized = sanitize_announcement_markdown(markdown, max_chars=900)

    assert "# Release **note**" in sanitized
    assert "- Use `/progresstail-update`" in sanitized
    assert "<!--" not in sanitized
    assert "<script" not in sanitized
    assert "alert" not in sanitized
    assert "![" not in sanitized
    assert (
        sanitize_announcement_markdown(" \n<!-- hidden -->\n![only](https://example.test/x.png)")
        == ""
    )


@pytest.mark.real_announcements_fetcher
def test_official_announcements_fetcher_returns_empty_on_blank_and_caches(monkeypatch):
    from hermes_progress_tail.rendering import announcements

    calls = []

    class Response:
        status = 200
        headers = {"content-type": "text/markdown; charset=utf-8"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            calls.append("read")
            return b"  \n"

    monkeypatch.setattr(
        announcements.urllib.request, "urlopen", lambda *_args, **_kwargs: Response()
    )
    announcements.clear_announcements_cache()

    assert announcements.official_announcements_markdown(timeout=0.1, refresh=True) == ""
    assert announcements.official_announcements_markdown(timeout=0.1) == ""
    assert calls == ["read"]


def test_focused_announcements_render_above_status_when_markdown_has_content(monkeypatch):
    async def run():
        from hermes_progress_tail.rendering import announcements

        monkeypatch.setattr(
            announcements,
            "official_announcements_markdown",
            lambda: "### Ship it\n- **Official** announcement",
        )
        adapter = EditableAdapter()
        renderer = make_renderer(mode="focused")
        env = EnvironmentSnapshot(model="gpt-5.5", provider="custom", profile="default")
        ctx = make_ctx(adapter, env=env)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )

        content = adapter.sent[-1][1]
        assert "**__Announcements__**" in content
        assert "### Ship it" in content
        assert "- **Official** announcement" in content
        assert "**__Status__**" in content
        assert content.rfind("**__Announcements__**") < content.rfind("**__Status__**")

    asyncio.run(run())


def test_announcements_hide_when_official_markdown_is_empty(monkeypatch):
    async def run():
        from hermes_progress_tail.rendering import announcements

        monkeypatch.setattr(announcements, "official_announcements_markdown", lambda: "")
        adapter = EditableAdapter()
        renderer = make_renderer(mode="focused")
        env = EnvironmentSnapshot(model="gpt-5.5", provider="custom", profile="default")
        ctx = make_ctx(adapter, env=env)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )

        content = adapter.sent[-1][1]
        assert "**__Announcements__**" not in content
        assert "**__Status__**" in content

    asyncio.run(run())


def test_sectioned_announcements_render_above_status(monkeypatch):
    async def run():
        from hermes_progress_tail.rendering import announcements

        monkeypatch.setattr(
            announcements, "official_announcements_markdown", lambda: "Hello **everyone**"
        )
        adapter = EditableAdapter()
        renderer = make_renderer(mode="sectioned")
        env = EnvironmentSnapshot(model="gpt-5.5", provider="custom", profile="default")
        ctx = make_ctx(adapter, env=env)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )

        content = adapter.sent[-1][1]
        assert "▰ 📣 Announcements\nHello **everyone**" in content
        assert "▰ 🧭 Status" in content
        assert content.rfind("▰ 📣 Announcements") < content.rfind("▰ 🧭 Status")

    asyncio.run(run())


def test_reasoning_effort_renders_in_status_with_auto_fallback(monkeypatch):
    async def run():
        from hermes_progress_tail.rendering import announcements

        monkeypatch.setattr(announcements, "official_announcements_markdown", lambda: "")
        adapter = EditableAdapter()
        renderer = make_renderer(mode="focused")
        env = EnvironmentSnapshot(
            model="gpt-5.5",
            provider="custom",
            profile="default",
            reasoning_effort="high",
        )
        ctx = make_ctx(adapter, env=env)
        renderer.register_context(ctx)

        await renderer.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )
        assert "reasoning_effort=high" in adapter.sent[-1][1]

        adapter2 = EditableAdapter()
        renderer2 = make_renderer(mode="focused")
        ctx2 = make_ctx(
            adapter2,
            env=EnvironmentSnapshot(model="gpt-5.5", provider="custom", profile="default"),
        )
        renderer2.register_context(ctx2)

        await renderer2.handle_event(
            ToolEvent("s1", "k1", "telegram", "read_file: renderer.py"), force=True
        )
        assert "reasoning_effort=auto" in adapter2.sent[-1][1]

    asyncio.run(run())


def test_runtime_environment_extracts_reasoning_effort_from_agent():
    from hermes_progress_tail.runtime.environment import _update_environment_from_agent

    ctx = SessionContext(
        "s1",
        "k1",
        "telegram",
        "chat",
        None,
        SimpleNamespace(),
        None,
        "live_tail",
    )
    agent = SimpleNamespace(
        model="gpt-5.5",
        provider="custom",
        reasoning={"effort": "medium"},
        context_compressor=None,
    )

    _update_environment_from_agent(ctx, agent)

    assert ctx.environment.reasoning_effort == "medium"


def test_announcements_are_not_user_configurable():
    assert find_unknown_config_keys(
        {"progress_tail": {"announcements": {"enabled": False, "url": "https://example.test/a.md"}}}
    ) == ["progress_tail.announcements"]
