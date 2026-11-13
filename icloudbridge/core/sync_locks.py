"""Run at most one writing sync per service at a time.

Manual syncs (the API) and scheduled syncs run in the same event loop but did
not coordinate. Two bidirectional Notes syncs that overlapped both saw a new
Markdown file as unmapped and both created it in Apple Notes, leaving an
orphaned duplicate. Writing syncs now take a per-service lock: the scheduler
waits for its turn, a manual request is refused while one runs. Dry runs write
nothing and skip the lock.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_locks: dict[str, asyncio.Lock] = {}


class SyncAlreadyRunning(RuntimeError):
    """A writing sync for the service is already in progress."""


def service_lock(service: str) -> asyncio.Lock:
    """The lock shared by every sync of one service."""
    lock = _locks.get(service)
    if lock is None:
        lock = _locks[service] = asyncio.Lock()
    return lock


@asynccontextmanager
async def exclusive_sync(service: str, *, wait: bool, dry_run: bool = False) -> AsyncIterator[None]:
    """Hold the service's sync lock for the duration of a writing sync.

    wait=False raises SyncAlreadyRunning instead of queueing behind a running
    sync (used for interactive requests).
    """
    if dry_run:
        yield
        return
    lock = service_lock(service)
    if not wait and lock.locked():
        raise SyncAlreadyRunning(f"A {service} sync is already running; try again when it finishes.")
    async with lock:
        yield
