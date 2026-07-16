"""Worker runtime bridge tests — `run_async` marshals coroutines onto the process's persistent
loop, returns their results, and re-raises their exceptions so Celery sees a normal failure.
"""

import asyncio

import pytest

from deallens.worker.runtime import run_async


def test_run_async_returns_result() -> None:
    async def _coro() -> int:
        await asyncio.sleep(0)
        return 21 * 2

    assert run_async(_coro()) == 42


def test_run_async_propagates_exceptions() -> None:
    async def _boom() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        run_async(_boom())


def test_reused_loop_across_calls() -> None:
    # The loop is created once per process and reused — several calls all succeed on it.
    async def _echo(n: int) -> int:
        await asyncio.sleep(0)
        return n

    assert [run_async(_echo(i)) for i in range(5)] == [0, 1, 2, 3, 4]
