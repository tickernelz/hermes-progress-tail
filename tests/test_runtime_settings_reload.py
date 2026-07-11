from hermes_progress_tail.runtime import plugin
from hermes_progress_tail.settings.config import load_settings


def test_get_renderer_replaces_settings_for_every_collaborator(monkeypatch):
    old_settings = load_settings(
        {"progress_tail": {"delegates": {"lines_per_delegate": 2}}}
    )
    new_settings = load_settings(
        {"progress_tail": {"delegates": {"lines_per_delegate": 7}}}
    )
    monkeypatch.setattr(plugin, "_renderer", plugin.ProgressRenderer(old_settings))
    monkeypatch.setattr(plugin, "_load_runtime_settings", lambda: new_settings)

    renderer = plugin._get_renderer()

    assert renderer.settings is new_settings
    assert renderer.delegate_renderer.settings is new_settings
    assert renderer.delegate_renderer.settings.delegates.lines_per_delegate == 7
