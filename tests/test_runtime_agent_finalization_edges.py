import asyncio
from types import SimpleNamespace as NS

import pytest

from hermes_progress_tail.runtime import agent_events as ae


def plugin(monkeypatch, renderer, *, background=False):
    calls = []
    p = NS(
        _get_renderer=lambda: renderer,
        _is_background_review_thread=lambda: background,
        _finalize_target_context=ae._finalize_target_context,
        _schedule_finalize=lambda **kw: calls.append(kw),
        _should_suppress_agent_progress=lambda agent: background,
        _agent_session_key=lambda agent: "key",
    )
    monkeypatch.setattr(ae, "_runtime_plugin", lambda: p)
    return calls


def ctx(sid="sid", platform="discord", state="active", loop=object()):
    return NS(
        session_id=sid,
        session_key=f"k-{sid}",
        platform=platform,
        progress_state=state,
        loop=loop,
        generation=7,
    )


def test_finalize_target_exact_and_background(monkeypatch):
    exact = ctx()
    renderer = NS(find_context=lambda *a: exact, sessions={"x": exact})
    plugin(monkeypatch, renderer)
    assert ae._finalize_target_context(renderer, "sid", "discord", "key") is exact
    plugin(monkeypatch, renderer, background=True)
    assert ae._finalize_target_context(renderer) is None


@pytest.mark.parametrize(
    "items,platform,expected",
    [
        ([ctx("a")], "discord", "a"),
        ([ctx("a"), ctx("b")], "discord", None),
        ([ctx("a", state="done")], "discord", None),
        ([ctx("a", "telegram")], "discord", None),
        ([], "", None),
    ],
)
def test_finalize_active_fallback(monkeypatch, items, platform, expected):
    renderer = NS(find_context=lambda *a: None, sessions={str(i): v for i, v in enumerate(items)})
    plugin(monkeypatch, renderer)
    result = ae._finalize_target_context(renderer, platform=platform, session_key="missing")
    assert (result.session_id if result else None) == expected
    assert ae._finalize_target_context(renderer, session_id="explicit") is None


def test_schedule_finalize_success_and_done_error(monkeypatch):
    target = ctx()
    finalized = []

    async def finalize(**kw):
        finalized.append(kw)

    renderer = NS(finalize=finalize)
    p_calls = plugin(monkeypatch, renderer)
    ae._runtime_plugin()._finalize_target_context = lambda *a: target

    class Future:
        def add_done_callback(self, callback):
            self.callback = callback

        def result(self):
            raise RuntimeError("done")

    future = Future()
    run = asyncio.run

    def submit(coro, loop):
        assert loop is target.loop
        run(coro)
        return future

    monkeypatch.setattr(ae.asyncio, "run_coroutine_threadsafe", submit)
    ae._schedule_finalize("sid", purge=True, success=False)
    assert p_calls == []
    assert finalized == [{"session_id": "sid", "purge": True, "generation": 7, "success": False}]
    assert future.callback
    future.callback(future)


@pytest.mark.parametrize("target", [None, NS(loop=None)])
def test_schedule_finalize_missing_target_or_loop(monkeypatch, target):
    renderer = NS()
    plugin(monkeypatch, renderer)
    ae._runtime_plugin()._finalize_target_context = lambda *a: target
    ae._schedule_finalize()


def test_schedule_finalize_submission_exception_closes(monkeypatch):
    target = ctx()

    async def finalize(**kw):
        pass

    renderer = NS(finalize=finalize)
    plugin(monkeypatch, renderer)
    ae._runtime_plugin()._finalize_target_context = lambda *a: target
    seen = []

    def reject(coro, loop):
        seen.append(coro)
        coro.close()
        raise RuntimeError("reject")

    monkeypatch.setattr(ae.asyncio, "run_coroutine_threadsafe", reject)
    ae._schedule_finalize(purge=True)
    assert seen


def test_gateway_stop_outcomes(monkeypatch):
    renderer = NS()
    calls = plugin(monkeypatch, renderer)
    ae._runtime_plugin()._finalize_target_context = lambda *a, **k: None
    assert ae.on_gateway_stop_from_runner(session_key="x") is False
    target = ctx()
    ae._runtime_plugin()._finalize_target_context = lambda *a, **k: target
    assert ae.on_gateway_stop_from_runner(session_key="x", source=NS(platform="discord")) is True
    assert calls == [
        {"session_id": "sid", "session_key": "k-sid", "platform": "discord", "success": False}
    ]


@pytest.mark.parametrize("exact", [False, True])
def test_gateway_stop_platform_fallback_and_schedule_failure(monkeypatch, exact):
    target, submitted = ctx(), []

    async def finalize(**kw):
        pass

    renderer = NS(
        find_context=lambda *a: target if exact else None,
        sessions={"target": target},
        finalize=finalize,
    )
    plugin(monkeypatch, renderer)
    runtime = ae._runtime_plugin()
    runtime._finalize_target_context = ae._finalize_target_context
    runtime._schedule_finalize = ae._schedule_finalize

    def reject(coro, loop):
        submitted.append(loop)
        coro.close()
        raise RuntimeError("schedule failed")

    monkeypatch.setattr(ae.asyncio, "run_coroutine_threadsafe", reject)
    assert ae.on_gateway_stop_from_runner(
        session_key="k-sid" if exact else "missing", source=NS(platform="discord")
    )
    assert submitted == [target.loop]


def test_reset_inline_reasoning_guards(monkeypatch):
    ae._reset_inline_reasoning(None)
    import hermes_progress_tail.hooks.monkeypatches as hooks

    calls = []
    monkeypatch.setattr(hooks, "_reset_inline_reasoning_state", lambda agent: calls.append(agent))
    agent = object()
    ae._reset_inline_reasoning(agent)
    assert calls == [agent]
    monkeypatch.setattr(
        hooks, "_reset_inline_reasoning_state", lambda agent: (_ for _ in ()).throw(RuntimeError())
    )
    ae._reset_inline_reasoning(agent)


def test_post_llm_reset_suppression_and_foreground(monkeypatch):
    renderer = NS()
    calls = plugin(monkeypatch, renderer, background=True)
    reset = []
    monkeypatch.setattr(ae, "_reset_inline_reasoning", lambda a: reset.append(a))
    agent = object()
    assert ae._on_post_llm_call("sid", agent) is None
    assert reset == [agent] and calls == []
    ae._runtime_plugin()._should_suppress_agent_progress = lambda a: False
    ae._on_post_llm_call("sid", agent)
    assert calls == [{"session_id": "sid", "session_key": "key"}]


def test_session_reset_and_finalize(monkeypatch):
    purges = []
    renderer = NS(purge=lambda **kw: purges.append(kw))
    calls = plugin(monkeypatch, renderer)
    monkeypatch.setattr(ae, "_reset_inline_reasoning", lambda a: None)
    ae._on_session_reset("sid", "discord", object())
    assert purges == [{"session_id": "sid", "platform": "discord"}]
    ae._on_session_finalize("sid", "discord", object())
    assert calls == [
        {"session_id": "sid", "platform": "discord", "session_key": "key", "purge": True}
    ]
    ae._runtime_plugin()._should_suppress_agent_progress = lambda a: True
    ae._on_session_finalize("x", agent=object())
    assert len(calls) == 1
