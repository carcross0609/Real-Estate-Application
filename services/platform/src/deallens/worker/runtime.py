"""The async↔sync bridge and the worker's DB-session seam.

Celery's prefork workers are synchronous, but every service entrypoint in the platform is
`async` over asyncpg — and asyncpg binds each pooled connection to the event loop that
opened it (the same constraint the integration-test fixtures call out). Spinning up a fresh
`asyncio.run` loop per task would therefore invalidate `core.db`'s pooled connections on the
second task ("attached to a different loop"). So each worker process runs **one** persistent
event loop on a background thread, created lazily on first use; `run_async` marshals a
coroutine onto it and blocks for the result. One loop per process → the async engines' pools
stay valid for the life of the worker.

Lazy, per-process creation is what makes this fork-safe: the parent process never opens a
loop or a connection (importing `core.db` only constructs engine objects), so each forked
child builds its own loop + connections on its first task. `reset_event_loop` is wired to
Celery's `worker_process_init` as a belt-and-suspenders guard against an inherited handle.

Worker tasks are trusted internal jobs that write shared reference data and system-generated
notifications, so they run on the owner-role `system_session` (RLS does not apply) — the same
trust level `scoring`/`engine` already use for their scheduled write paths. `task_session`
is the one sanctioned way a task gets a session; it wraps `system_session` so every task
shares one commit/rollback discipline.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from deallens.core.db import system_session
from deallens.core.logging import get_logger

_log = get_logger("worker.runtime")

_loop: asyncio.AbstractEventLoop | None = None
_thread: threading.Thread | None = None
_lock = threading.Lock()


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """The process-local event loop, started on first use behind a lock (a burst of tasks at
    worker startup must not race two loops into existence)."""
    global _loop, _thread
    if _loop is not None:
        return _loop
    with _lock:
        if _loop is not None:  # another thread won the race while we waited on the lock
            return _loop
        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=loop.run_forever, name="deallens-worker-loop", daemon=True
        )
        thread.start()
        _loop, _thread = loop, thread
        _log.info("worker_loop_started")
        return loop


def run_async[T](coro: Coroutine[object, object, T]) -> T:
    """Run `coro` to completion on the process's persistent loop and return its result.

    Blocks the calling (Celery) thread until the coroutine finishes, re-raising whatever it
    raised so Celery's retry/failure handling sees a normal exception."""
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


def reset_event_loop() -> None:
    """Drop the reference to any inherited loop so the next `run_async` builds a fresh one.
    Wired to `worker_process_init`: a forked child must never reuse the parent's loop/handle."""
    global _loop, _thread
    with _lock:
        _loop = None
        _thread = None


@asynccontextmanager
async def task_session() -> AsyncIterator[AsyncSession]:
    """The session a worker task runs on: the owner-role `system_session`, committed on clean
    exit. Tasks generate shared reference data (scores, valuations) and system notifications,
    so they legitimately run above RLS — never on behalf of a specific end-user request."""
    async with system_session() as session:
        yield session


__all__ = ["reset_event_loop", "run_async", "task_session"]
