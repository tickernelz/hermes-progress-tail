import hermes_progress_tail.plugin as plugin
from hermes_progress_tail.config import load_settings


def test_demo_command_returns_focused_markdown_sample(monkeypatch):
    plugin._renderer = None
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: {})
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings({}))

    demo = plugin._command("demo")

    assert demo.startswith("**Hermes is working**\n────────────────")
    assert "**Now** running git diff --check" in demo
    assert "**State** 5 tools · 4 done · 1 running · 2 queued" in demo
    assert "**__Plan__**\n✓ Inspect renderer" in demo
    assert "**__Delegates__**" in demo
    assert "└ result\n  demo smoke check passed" in demo
    assert "4 tools · read_file, search_files, terminal, read_file" not in demo
    assert "**__Tools__**\n✓ search_files: focused_block · 0.1s" in demo
    assert "tool:" not in demo
    assert "[22:41]" not in demo


def test_demo_plain_returns_plain_focused_sample(monkeypatch):
    plugin._renderer = None
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: {})
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings({}))

    demo = plugin._command("demo plain")

    assert demo.startswith("Hermes is working\n────────────────")
    assert "Now     running git diff --check" in demo
    assert "**Now**" not in demo
    assert "Delegates\n" in demo


def test_demo_failed_marks_failed_tool(monkeypatch):
    plugin._renderer = None
    monkeypatch.setattr(plugin, "_load_runtime_config", lambda: {})
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: load_settings({}))

    demo = plugin._command("demo failed")

    assert "× terminal: pytest tests/test_renderer.py -q · 2.1s" in demo
    assert "**__Tools__**" in demo
