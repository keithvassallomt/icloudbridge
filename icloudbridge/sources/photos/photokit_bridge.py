"""Client for the menubar app's PhotoKit export bridge.

The backend cannot use PhotoKit itself. TCC attributes it to the Homebrew
python binary (bundle id ``org.python.python``), whose Info.plist carries no
photo-library usage description, so ``requestAuthorization`` is denied outright
with no prompt - and the plist cannot be patched because Homebrew owns it and
``brew upgrade`` would revert it.

The signed menubar app *does* hold the Photos grant, so it runs the PhotoKit
work and exposes it over loopback HTTP. This module is the client half.

Falling back to AppleScript stays necessary: the bridge is only present when
the menubar app is running.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

HANDSHAKE_PATH = (
    Path.home()
    / "Library"
    / "Application Support"
    / "iCloudBridge"
    / "photokit-service.json"
)

# How often to ask the bridge for newly exported items. Cloud originals are
# downloaded on demand, so a job can sit quiet for a while between items.
POLL_INTERVAL_SECONDS = 1.0

# Ceiling on a single job with no observable progress at all.
STALL_TIMEOUT_SECONDS = 900.0

# How long to wait for someone to answer the macOS Photos permission prompt.
AUTHORIZATION_PROMPT_TIMEOUT = 120.0


class PhotoKitUnavailable(RuntimeError):
    """The bridge is not reachable (menubar app not running, or not authorized)."""


@dataclass
class ExportedItem:
    """One file the bridge wrote to the staging directory."""

    identifier: str  # the asset's ZUUID, so matching is exact - never by filename
    filename: str
    path: Path
    kind: str  # "original" or "pairedVideo"
    bytes: int

    @classmethod
    def from_json(cls, raw: dict) -> ExportedItem:
        return cls(
            identifier=raw["identifier"],
            filename=raw["filename"],
            path=Path(raw["path"]),
            kind=raw.get("kind", "original"),
            bytes=int(raw.get("bytes", 0)),
        )


@dataclass
class ExportFailure:
    identifier: str
    message: str

    @classmethod
    def from_json(cls, raw: dict) -> ExportFailure:
        return cls(identifier=raw["identifier"], message=raw.get("message", ""))


@dataclass
class ExportProgress:
    """One poll's worth of results, streamed as the job runs."""

    items: list[ExportedItem] = field(default_factory=list)
    completed: int = 0
    total: int = 0
    state: str = "running"
    failures: list[ExportFailure] = field(default_factory=list)


class PhotoKitBridgeClient:
    """Talks to the menubar app's loopback PhotoKit service."""

    def __init__(self, handshake_path: Path | None = None):
        self.handshake_path = handshake_path or HANDSHAKE_PATH
        self._base_url: str | None = None
        self._token: str | None = None

    # ------------------------------------------------------------------ setup

    def _load_handshake(self) -> tuple[str, str]:
        """Read the port/token the app published on launch."""
        try:
            raw = json.loads(self.handshake_path.read_text())
            port = int(raw["port"])
            token = str(raw["token"])
        except FileNotFoundError:
            raise PhotoKitUnavailable(
                "PhotoKit bridge handshake not found - is the iCloudBridge menubar "
                "app running?"
            ) from None
        except (ValueError, KeyError, OSError) as e:
            raise PhotoKitUnavailable(f"Unreadable PhotoKit handshake: {e}") from None

        return f"http://127.0.0.1:{port}", token

    def _ensure_loaded(self) -> tuple[str, str]:
        if self._base_url is None or self._token is None:
            self._base_url, self._token = self._load_handshake()
        return self._base_url, self._token

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    # ----------------------------------------------------------------- health

    async def authorization_status(self) -> str:
        """Returns the app's Photos authorization state.

        Raises:
            PhotoKitUnavailable: if the bridge cannot be reached.
        """
        base, token = self._ensure_loaded()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{base}/health", headers=self._headers(token)
                )
        except httpx.HTTPError as e:
            # A stale handshake (app restarted on a new port) looks like this.
            self._base_url = self._token = None
            raise PhotoKitUnavailable(f"PhotoKit bridge unreachable: {e}") from None

        if response.status_code == 401:
            self._base_url = self._token = None
            raise PhotoKitUnavailable("PhotoKit bridge rejected our token")
        if response.status_code != 200:
            raise PhotoKitUnavailable(
                f"PhotoKit bridge returned HTTP {response.status_code}"
            )

        return str(response.json().get("authorization", "unknown"))

    async def is_available(self) -> bool:
        """True when the bridge is reachable and Photos access is granted.

        On a Mac that has never been asked, this triggers the system prompt
        through the menubar app and waits for an answer. Without that, the
        first sync on a new machine would quietly take the slow AppleScript
        path forever and nobody would ever see a permission dialog.
        """
        try:
            status = await self.authorization_status()
        except PhotoKitUnavailable as e:
            logger.debug("PhotoKit bridge unavailable: %s", e)
            return False

        if status == "notDetermined":
            logger.info(
                "Photos access has not been requested yet; asking via the "
                "menubar app (a system prompt will appear)"
            )
            try:
                await self.request_authorization()
                status = await self._await_authorization_decision()
            except PhotoKitUnavailable as e:
                logger.debug("PhotoKit authorization request failed: %s", e)
                return False

        if status not in ("authorized", "limited"):
            logger.info(
                "PhotoKit bridge reachable but Photos access is '%s'; "
                "falling back to AppleScript export",
                status,
            )
            return False
        return True

    async def _await_authorization_decision(self) -> str:
        """Wait for the person to answer the system permission prompt."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + AUTHORIZATION_PROMPT_TIMEOUT
        status = "notDetermined"
        while loop.time() < deadline:
            await asyncio.sleep(1.0)
            status = await self.authorization_status()
            if status != "notDetermined":
                logger.info("Photos access answered: %s", status)
                return status
        logger.warning(
            "No answer to the Photos permission prompt after %.0fs",
            AUTHORIZATION_PROMPT_TIMEOUT,
        )
        return status

    async def request_authorization(self) -> None:
        """Ask the app to prompt for Photos access (returns immediately)."""
        base, token = self._ensure_loaded()
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(f"{base}/authorize", headers=self._headers(token))

    # ----------------------------------------------------------------- export

    async def export(
        self, uuids: list[str], destination: Path
    ) -> AsyncIterator[ExportProgress]:
        """Export assets by UUID, yielding results as they are written.

        Files are staged in `destination`; the caller is expected to move them
        out. Items are matched by asset UUID, so assets sharing an original
        filename are never confused with one another.

        Raises:
            PhotoKitUnavailable: if the bridge cannot be reached or refuses.
        """
        if not uuids:
            return

        base, token = self._ensure_loaded()
        headers = self._headers(token)

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                start = await client.post(
                    f"{base}/export",
                    headers=headers,
                    json={"uuids": uuids, "destination": str(destination)},
                )
            except httpx.HTTPError as e:
                raise PhotoKitUnavailable(f"PhotoKit export failed to start: {e}") from None

            if start.status_code not in (200, 202):
                detail = start.json().get("error", start.text)
                raise PhotoKitUnavailable(f"PhotoKit export refused: {detail}")

            job_id = start.json()["job_id"]
            logger.info(
                "PhotoKit export job %s started for %d assets", job_id, len(uuids)
            )

            cursor = 0
            last_change = asyncio.get_event_loop().time()

            try:
                while True:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)

                    try:
                        poll = await client.get(
                            f"{base}/job",
                            headers=headers,
                            params={"id": job_id, "since": cursor},
                        )
                    except httpx.HTTPError as e:
                        raise PhotoKitUnavailable(
                            f"Lost contact with PhotoKit bridge: {e}"
                        ) from None

                    if poll.status_code == 404:
                        raise PhotoKitUnavailable(
                            f"PhotoKit job {job_id} disappeared"
                        )
                    poll.raise_for_status()
                    snapshot = poll.json()

                    items = [
                        ExportedItem.from_json(raw)
                        for raw in snapshot.get("items", [])
                    ]
                    if items:
                        cursor = int(snapshot.get("next_cursor", cursor))
                        last_change = asyncio.get_event_loop().time()

                    state = snapshot.get("state", "running")
                    failures = [
                        ExportFailure.from_json(raw)
                        for raw in snapshot.get("failures", [])
                    ]

                    if items or state != "running":
                        yield ExportProgress(
                            items=items,
                            completed=int(snapshot.get("completed", 0)),
                            total=int(snapshot.get("total", len(uuids))),
                            state=state,
                            failures=failures,
                        )

                    if state != "running":
                        return

                    elapsed = asyncio.get_event_loop().time() - last_change
                    if elapsed > STALL_TIMEOUT_SECONDS:
                        await self._cancel(client, base, headers, job_id)
                        raise PhotoKitUnavailable(
                            f"PhotoKit export stalled for {elapsed:.0f}s"
                        )
            finally:
                await self._forget(client, base, headers, job_id)

    async def _cancel(self, client, base: str, headers: dict, job_id: str) -> None:
        try:
            await client.post(f"{base}/cancel", headers=headers, params={"id": job_id})
        except httpx.HTTPError:
            pass

    async def _forget(self, client, base: str, headers: dict, job_id: str) -> None:
        try:
            await client.post(f"{base}/forget", headers=headers, params={"id": job_id})
        except httpx.HTTPError:
            pass
