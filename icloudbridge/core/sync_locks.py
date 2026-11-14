"""Run at most one writing sync per service at a time.

Manual syncs (the API) and scheduled syncs run in the same event loop but did
not coordinate. Two bidirectional Notes syncs that overlapped both saw a new
Markdown file as unmapped and both created it in Apple Notes, leaving an
orphaned duplicate. The command line runs in a separate process and could
overlap either of them the same way.

Writing syncs now take a per-service lock: an asyncio lock for syncs in this
process, plus an OS file lock in the data directory for other processes. The
OS releases a file lock when its process exits, so a crash never leaves a
stale lock behind. The scheduler waits for its turn; a manual request or a
command is refused while another sync of the service runs. Dry runs write
nothing and skip the lock.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

POLL_SECONDS = 1.0

_locks: dict[str, asyncio.Lock] = {}


class SyncAlreadyRunning(RuntimeError):
    """A writing sync for the service is already in progress."""


def service_lock(service: str) -> asyncio.Lock:
    """The lock shared by every sync of one service in this process."""
    lock = _locks.get(service)
    if lock is None:
        lock = _locks[service] = asyncio.Lock()
    return lock


def lock_file_path(lock_dir: Path | str, service: str) -> Path:
    return Path(lock_dir) / f".sync-{service}.lock"


def _try_file_lock(path: Path) -> int | None:
    """Take the file lock without waiting; return its descriptor, or None if another process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _busy(service: str) -> SyncAlreadyRunning:
    return SyncAlreadyRunning(f"A {service} sync is already running; try again when it finishes.")


@asynccontextmanager
async def exclusive_sync(
    service: str, lock_dir: Path | str, *, wait: bool, dry_run: bool = False
) -> AsyncIterator[None]:
    """Hold the service's sync lock for the duration of a writing sync.

    lock_dir is the data directory, shared by the app and the command line.
    wait=False raises SyncAlreadyRunning instead of queueing behind a running
    sync (used for interactive requests and commands).
    """
    if dry_run:
        yield
        return
    lock = service_lock(service)
    if not wait and lock.locked():
        raise _busy(service)
    async with lock:
        path = lock_file_path(lock_dir, service)
        fd = _try_file_lock(path)
        while fd is None:
            if not wait:
                raise _busy(service)
            await asyncio.sleep(POLL_SECONDS)
            fd = _try_file_lock(path)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
