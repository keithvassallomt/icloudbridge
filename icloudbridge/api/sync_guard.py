"""Hold a service's sync lock for an API request."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, status

from icloudbridge.core.config import AppConfig
from icloudbridge.core.sync_locks import SyncAlreadyRunning, exclusive_sync


@asynccontextmanager
async def sync_guard(service: str, config: AppConfig, *, dry_run: bool = False) -> AsyncIterator[None]:
    """Run the request's work under the service's sync lock.

    Answers 409 while another sync of the service runs, in this process (a
    scheduled sync, say) or another one (the command line).
    """
    try:
        async with exclusive_sync(service, config.general.data_dir, wait=False, dry_run=dry_run):
            yield
    except SyncAlreadyRunning as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
