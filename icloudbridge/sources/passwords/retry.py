"""Bounded retries that recheck identity after uncertain create responses."""

import asyncio

import httpx

TRANSIENT = {429, 500, 502, 503, 504}


async def retry_addition(create, exists) -> bool:
    """Return false if a fresh read finds the login; never blindly replay a POST."""
    for attempt in range(3):
        try:
            await create()
            return True
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in TRANSIENT:
                raise
            if attempt == 2:
                raise
            await asyncio.sleep(2**attempt)
            # A create may have succeeded before the error. A failed read aborts.
            if await exists():
                return False
    return False
