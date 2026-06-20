import pytest


@pytest.fixture(autouse=True)
def no_official_announcements_by_default(monkeypatch, request):
    if request.node.get_closest_marker("real_announcements_fetcher"):
        return
    try:
        from hermes_progress_tail.rendering import announcements
    except Exception:
        return
    monkeypatch.setattr(announcements, "official_announcements_markdown", lambda: "")
