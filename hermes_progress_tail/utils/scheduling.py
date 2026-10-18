from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any


def on_loop_thread(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


def call_on_loop_thread(loop: asyncio.AbstractEventLoop | None, action: Callable[[], Any]) -> None:
    if loop is None or on_loop_thread(loop):
        action()
        return
    loop.call_soon_threadsafe(action)


def submit_or_close(coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop) -> Future[Any]:
    try:
        return asyncio.run_coroutine_threadsafe(coro, loop)
    except BaseException:
        coro.close()
        raise


def create_task_or_close(
    loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any]
) -> asyncio.Task[Any]:
    try:
        return loop.create_task(coro)
    except BaseException:
        coro.close()
        raise


def schedule_task_on_loop(
    loop: asyncio.AbstractEventLoop, start: Callable[[], asyncio.Task[Any]]
) -> asyncio.Task[Any] | None:
    if on_loop_thread(loop):
        return start()
    loop.call_soon_threadsafe(start)
    return None
