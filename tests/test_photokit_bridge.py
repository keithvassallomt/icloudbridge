"""Tests for the PhotoKit bridge client and its use by the export engine.

The backend cannot call PhotoKit directly (TCC attributes it to the Homebrew
python binary, which has no photo-library usage description), so the signed
menubar app exposes it over loopback HTTP. These tests run a stand-in for that
service so the client and the engine integration are exercised for real.

The behaviour that matters most: PhotoKit returns each file tagged with its
asset UUID, so assets sharing an original filename can no longer be confused
with one another the way the AppleScript path allowed.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from icloudbridge.core.photos_export_engine import ExportConfig, PhotoExportEngine
from icloudbridge.sources.photos import photokit_bridge
from icloudbridge.sources.photos.photokit_bridge import (
    PhotoKitBridgeClient,
    PhotoKitUnavailable,
)

TOKEN = "test-token"


class FakeBridge:
    """A stand-in for the menubar app's PhotoKit service."""

    def __init__(self, authorization="authorized", script=None, require_token=True):
        self.authorization = authorization
        # `script` is the sequence of job snapshots handed out on each poll.
        self.script = script or []
        self.require_token = require_token
        self.polls = 0
        self.started_with: dict | None = None
        self.forgotten = False

        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _authorized(self) -> bool:
                if not bridge.require_token:
                    return True
                header = self.headers.get("Authorization", "")
                return header == f"Bearer {TOKEN}"

            def _reply(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._authorized():
                    return self._reply(401, {"error": "bad token"})
                if self.path.startswith("/health"):
                    return self._reply(
                        200, {"status": "ok", "authorization": bridge.authorization}
                    )
                if self.path.startswith("/job"):
                    index = min(bridge.polls, len(bridge.script) - 1)
                    bridge.polls += 1
                    return self._reply(200, bridge.script[index])
                return self._reply(404, {"error": "not found"})

            def do_POST(self):
                if not self._authorized():
                    return self._reply(401, {"error": "bad token"})
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                if self.path.startswith("/export"):
                    bridge.started_with = json.loads(raw)
                    return self._reply(202, {"job_id": "job-1", "total": 1})
                if self.path.startswith("/forget"):
                    bridge.forgotten = True
                    return self._reply(200, {"status": "forgotten"})
                return self._reply(200, {"status": "ok"})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def handshake(self, tmp_path: Path) -> Path:
        path = tmp_path / "photokit-service.json"
        path.write_text(json.dumps({"port": self.port, "token": TOKEN}))
        return path

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(autouse=True)
def fast_polling(monkeypatch):
    monkeypatch.setattr(photokit_bridge, "POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(photokit_bridge, "AUTHORIZATION_PROMPT_TIMEOUT", 3.0)


def snapshot(items, *, state="running", completed=0, total=1, failures=()):
    return {
        "job_id": "job-1",
        "state": state,
        "total": total,
        "completed": completed,
        "exported": len(items),
        "failed": len(failures),
        "message": "",
        "next_cursor": len(items),
        "items": list(items),
        "failures": list(failures),
    }


def item(identifier, filename, path, kind="original", size=3):
    return {
        "identifier": identifier,
        "filename": filename,
        "path": str(path),
        "kind": kind,
        "bytes": size,
    }


# --------------------------------------------------------------- availability


@pytest.mark.asyncio
async def test_unavailable_when_handshake_missing(tmp_path):
    client = PhotoKitBridgeClient(handshake_path=tmp_path / "absent.json")
    assert await client.is_available() is False


@pytest.mark.asyncio
async def test_unavailable_when_photos_access_denied(tmp_path):
    bridge = FakeBridge(authorization="denied")
    try:
        client = PhotoKitBridgeClient(handshake_path=bridge.handshake(tmp_path))
        assert await client.is_available() is False
    finally:
        bridge.stop()


@pytest.mark.asyncio
async def test_available_when_authorized(tmp_path):
    bridge = FakeBridge(authorization="authorized")
    try:
        client = PhotoKitBridgeClient(handshake_path=bridge.handshake(tmp_path))
        assert await client.is_available() is True
    finally:
        bridge.stop()


@pytest.mark.asyncio
async def test_bad_token_is_reported_as_unavailable(tmp_path):
    bridge = FakeBridge()
    try:
        handshake = tmp_path / "photokit-service.json"
        handshake.write_text(json.dumps({"port": bridge.port, "token": "wrong"}))
        client = PhotoKitBridgeClient(handshake_path=handshake)
        with pytest.raises(PhotoKitUnavailable):
            await client.authorization_status()
    finally:
        bridge.stop()


# --------------------------------------------------------------------- export


@pytest.mark.asyncio
async def test_export_streams_items_then_completes(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "a.HEIC").write_bytes(b"aaa")

    bridge = FakeBridge(
        script=[
            snapshot([item("uuid-a", "a.HEIC", staged / "a.HEIC")], completed=1),
            snapshot([], state="done", completed=1),
        ]
    )
    try:
        client = PhotoKitBridgeClient(handshake_path=bridge.handshake(tmp_path))
        collected = []
        async for progress in client.export(["uuid-a"], staged):
            collected.extend(progress.items)

        assert [i.identifier for i in collected] == ["uuid-a"]
        assert bridge.started_with["uuids"] == ["uuid-a"]
        assert bridge.forgotten, "client should release the finished job"
    finally:
        bridge.stop()


# ---------------------------------------------------------- engine integration


@dataclass
class FakeAsset:
    uuid: str
    filename: str
    original_filename: str | None
    media_type: str = "image"
    created_date: datetime = datetime(2026, 2, 1)


class FakeDB:
    def __init__(self):
        self.records = []

    async def get_export_by_hash(self, h):
        return None

    async def get_by_hash(self, h):
        return None

    async def record_export(self, **kw):
        self.records.append(kw)


def make_engine(tmp_path) -> tuple[PhotoExportEngine, FakeDB]:
    export_folder = tmp_path / "out"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")
    db = FakeDB()
    engine.db = db
    return engine, db


@pytest.mark.asyncio
async def test_duplicate_filenames_are_kept_apart_by_uuid(tmp_path):
    """The AppleScript path's core failure, now impossible.

    Three assets all named IMG_1234.HEIC. PhotoKit tags each exported file with
    its asset UUID, so all three are stored rather than collapsing to one.
    """
    staged = tmp_path / "staged"
    staged.mkdir()
    for name, content in [
        ("IMG_1234.HEIC", b"aaa"),
        ("IMG_1234 2.HEIC", b"bbb"),
        ("IMG_1234 3.HEIC", b"ccc"),
    ]:
        (staged / name).write_bytes(content)

    bridge = FakeBridge(
        script=[
            snapshot(
                [
                    item("uuid-0", "IMG_1234.HEIC", staged / "IMG_1234.HEIC"),
                    item("uuid-1", "IMG_1234.HEIC", staged / "IMG_1234 2.HEIC"),
                    item("uuid-2", "IMG_1234.HEIC", staged / "IMG_1234 3.HEIC"),
                ],
                completed=3,
                total=3,
            ),
            snapshot([], state="done", completed=3, total=3),
        ]
    )
    try:
        engine, db = make_engine(tmp_path)
        client = PhotoKitBridgeClient(handshake_path=bridge.handshake(tmp_path))
        assets = [
            FakeAsset(uuid=f"uuid-{i}", filename=f"{i}.heic",
                      original_filename="IMG_1234.HEIC")
            for i in range(3)
        ]

        exported, errors, skipped, handled = await engine._export_cloud_only_photokit(
            assets, client
        )

        assert (exported, errors, skipped) == (3, 0, 0)
        assert handled == {"uuid-0", "uuid-1", "uuid-2"}
        # Three distinct files, so three distinct hashes - none overwrote another.
        assert len({r["content_hash"] for r in db.records}) == 3
    finally:
        bridge.stop()


@pytest.mark.asyncio
async def test_live_photo_video_is_recorded_under_its_own_key(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "IMG_1.HEIC").write_bytes(b"img")
    (staged / "IMG_1.mov").write_bytes(b"vid")

    bridge = FakeBridge(
        script=[
            snapshot(
                [
                    item("uuid-a", "IMG_1.HEIC", staged / "IMG_1.HEIC"),
                    item("uuid-a", "IMG_1.mov", staged / "IMG_1.mov",
                         kind="pairedVideo"),
                ],
                completed=1,
            ),
            snapshot([], state="done", completed=1),
        ]
    )
    try:
        engine, db = make_engine(tmp_path)
        client = PhotoKitBridgeClient(handshake_path=bridge.handshake(tmp_path))
        assets = [FakeAsset(uuid="uuid-a", filename="x.heic",
                            original_filename="IMG_1.HEIC")]

        exported, errors, skipped, handled = await engine._export_cloud_only_photokit(
            assets, client
        )

        # The still counts once; the paired video is an extra file, not an
        # extra asset.
        assert (exported, errors, skipped) == (1, 0, 0)
        keys = {r["apple_asset_uuid"] for r in db.records}
        assert keys == {"uuid-a", "uuid-a_live"}
        media = {r["apple_asset_uuid"]: r["media_type"] for r in db.records}
        assert media["uuid-a_live"] == "video"
    finally:
        bridge.stop()


@pytest.mark.asyncio
async def test_per_asset_failures_are_counted_and_marked_handled(tmp_path):
    staged = tmp_path / "staged"
    staged.mkdir()

    bridge = FakeBridge(
        script=[
            snapshot(
                [],
                state="done",
                completed=1,
                failures=[{"identifier": "uuid-gone",
                           "message": "Not found in Photos library"}],
            ),
        ]
    )
    try:
        engine, _ = make_engine(tmp_path)
        client = PhotoKitBridgeClient(handshake_path=bridge.handshake(tmp_path))
        assets = [FakeAsset(uuid="uuid-gone", filename="x.heic",
                            original_filename="X.HEIC")]

        exported, errors, skipped, handled = await engine._export_cloud_only_photokit(
            assets, client
        )

        assert (exported, errors, skipped) == (0, 1, 0)
        # Marked handled so the AppleScript fallback does not re-attempt an
        # asset PhotoKit already proved is missing.
        assert handled == {"uuid-gone"}
    finally:
        bridge.stop()


@pytest.mark.asyncio
async def test_bridge_dropping_midrun_leaves_the_rest_unhandled(tmp_path):
    """A dropped bridge must not silently lose the assets it never reached."""
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "A.HEIC").write_bytes(b"aaa")

    bridge = FakeBridge(
        script=[snapshot([item("uuid-a", "A.HEIC", staged / "A.HEIC")], completed=1,
                         total=2)]
    )
    try:
        engine, _ = make_engine(tmp_path)
        client = PhotoKitBridgeClient(handshake_path=bridge.handshake(tmp_path))
        assets = [
            FakeAsset(uuid="uuid-a", filename="a", original_filename="A.HEIC"),
            FakeAsset(uuid="uuid-b", filename="b", original_filename="B.HEIC"),
        ]

        # Kill the service after the first poll is served.
        async for _ in client.export([a.uuid for a in assets], staged):
            bridge.stop()
            break

        exported, errors, skipped, handled = await engine._export_cloud_only_photokit(
            assets, client
        )

        # The service is gone, so nothing was handled and the caller will fall
        # back to AppleScript for both.
        assert handled == set()
        assert (exported, errors, skipped) == (0, 0, 0)
    finally:
        try:
            bridge.stop()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_not_determined_triggers_the_system_prompt(tmp_path):
    """A machine that has never been asked must get a prompt, not a silent skip.

    Otherwise the first sync on a new Mac takes the slow AppleScript path
    forever and no permission dialog is ever shown.
    """
    bridge = FakeBridge(authorization="notDetermined")

    def grant_on_request():
        bridge.authorization = "authorized"

    try:
        client = PhotoKitBridgeClient(handshake_path=bridge.handshake(tmp_path))

        real_request = client.request_authorization

        async def request_then_grant():
            await real_request()
            grant_on_request()

        client.request_authorization = request_then_grant

        assert await client.is_available() is True
    finally:
        bridge.stop()


@pytest.mark.asyncio
async def test_unanswered_prompt_falls_back_rather_than_hanging(tmp_path):
    """If nobody answers, give up and use AppleScript instead of blocking."""
    bridge = FakeBridge(authorization="notDetermined")
    try:
        client = PhotoKitBridgeClient(handshake_path=bridge.handshake(tmp_path))
        assert await client.is_available() is False
    finally:
        bridge.stop()
