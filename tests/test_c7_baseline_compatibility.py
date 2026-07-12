"""C7 characterizations using only the public surface available on its base."""
# ruff: noqa: F401 -- pytest collects the imported characterization aliases

from collections import deque

from hermes_progress_tail.models.state import BackgroundJob, DelegateBranch, SessionContext
from hermes_progress_tail.renderer import ProgressRenderer
from hermes_progress_tail.settings.loading import load_settings
from tests.test_background_jobs_and_fencing import (
    test_background_job_renders_head_tail_completion_and_survives_finalize as test_background_poll_cancellation_pruning_and_active_finalization,
)
from tests.test_delegate_progress import (
    test_completed_delegate_reuse_cancels_pending_cleanup as test_delegate_cleanup_cancellation_and_reset,
)
from tests.test_session_registry import (
    test_characterization_registration_reuse_and_non_reuse as test_registry_same_source_and_cross_turn_reuse,
)


def context(**kwargs):
    return SessionContext("s", "k", "discord", "c", None, None, None, **kwargs)


def test_legacy_keywords_and_flat_collection_identity():
    branches, delegate_order = {"d": DelegateBranch("d")}, deque(["d"])
    jobs, background_order = {"p": BackgroundJob("p")}, deque(["p"])
    ctx = context(
        delegate_branches=branches,
        delegate_order=delegate_order,
        background_jobs=jobs,
        background_order=background_order,
    )
    assert ctx.delegate_branches is branches
    assert ctx.delegate_order is delegate_order
    assert ctx.background_jobs is jobs
    assert ctx.background_order is background_order


def test_default_collections_are_independent():
    first, second = context(), context()
    assert first.delegate_branches is not second.delegate_branches
    assert first.delegate_order is not second.delegate_order
    assert first.background_jobs is not second.background_jobs
    assert first.background_order is not second.background_order


def test_flat_collection_assignment_preserves_identity():
    ctx = context()
    for name, value in (
        ("delegate_branches", {}),
        ("delegate_order", deque()),
        ("background_jobs", {}),
        ("background_order", deque()),
    ):
        setattr(ctx, name, value)
        assert getattr(ctx, name) is value


def test_registry_reuse_preserves_delegate_and_background_collection_identity():
    renderer = ProgressRenderer(load_settings({}))
    old = context(source_message_id="m1")
    old.delegate_branches["d"] = DelegateBranch("d")
    old.delegate_order.append("d")
    old.background_jobs["p"] = BackgroundJob("p")
    old.background_order.append("p")
    renderer.register_context(old)

    reused = context(source_message_id="m1")
    renderer.register_context(reused)
    assert reused.delegate_branches is old.delegate_branches
    assert reused.delegate_order is old.delegate_order
    assert reused.background_jobs is old.background_jobs
    assert reused.background_order is old.background_order

    fenced = context(source_message_id="m2")
    renderer.register_context(fenced)
    assert fenced.delegate_branches is not reused.delegate_branches
    assert fenced.delegate_order is not reused.delegate_order
    assert fenced.background_jobs is reused.background_jobs
    assert fenced.background_order is reused.background_order
