"""SQLite helpers for tracking imported photo/video assets."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


class PhotosDB:
    """Manage discovery/import state for photo sync."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS photo_assets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_hash TEXT UNIQUE NOT NULL,
                    source_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    album TEXT,
                    captured_at TEXT,
                    first_seen REAL NOT NULL,
                    last_imported REAL,
                    apple_local_identifier TEXT
                )
                """
            )
            await db.execute(
                """CREATE INDEX IF NOT EXISTS idx_photo_hash ON photo_assets(content_hash)"""
            )
            await db.commit()

    async def get_by_hash(self, content_hash: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM photo_assets WHERE content_hash = ?",
                (content_hash,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def record_discovery(
        self,
        *,
        content_hash: str,
        path: Path,
        size: int,
        media_type: str,
        source_name: str,
        album: str | None,
        captured_at: datetime | None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO photo_assets
                (content_hash, source_path, file_size, media_type, source_name, album, captured_at, first_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content_hash,
                    str(path),
                    size,
                    media_type,
                    source_name,
                    album,
                    captured_at.isoformat() if captured_at else None,
                    datetime.utcnow().timestamp(),
                ),
            )
            await db.commit()

    async def mark_imported(
        self,
        *,
        content_hash: str,
        album: str | None,
        apple_local_identifier: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE photo_assets
                SET last_imported = ?,
                    album = COALESCE(?, album),
                    apple_local_identifier = COALESCE(?, apple_local_identifier)
                WHERE content_hash = ?
                """,
                (
                    datetime.utcnow().timestamp(),
                    album,
                    apple_local_identifier,
                    content_hash,
                ),
            )
            await db.commit()

    async def get_stats(self, pending_since: float | None = None) -> dict[str, int]:
        """Return aggregate counts for imported and pending assets.

        Args:
            pending_since: Optional UNIX timestamp. Pending assets discovered
                before this moment are treated as baseline and excluded from
                counts so that historical discoveries don't permanently bloat
                the "pending" total once a sync has completed successfully.
        """

        imported = 0
        pending_existing = 0
        stale_ids: list[int] = []
        pending_rows: list[aiosqlite.Row] = []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            async with db.execute(
                "SELECT COUNT(*) AS total FROM photo_assets WHERE last_imported IS NOT NULL"
            ) as cursor:
                imported = (await cursor.fetchone())[0]

            async with db.execute(
                "SELECT id, source_path, first_seen FROM photo_assets WHERE last_imported IS NULL"
            ) as cursor:
                pending_rows = await cursor.fetchall()

        for row in pending_rows:
            first_seen = row["first_seen"] or 0
            if pending_since is not None and first_seen < pending_since:
                continue

            path = Path(row["source_path"])
            if path.exists():
                pending_existing += 1
            else:
                stale_ids.append(row["id"])

        if stale_ids:
            async with aiosqlite.connect(self.db_path) as db:
                await db.executemany(
                    "DELETE FROM photo_assets WHERE id = ?",
                    [(stale_id,) for stale_id in stale_ids],
                )
                await db.commit()

        return {
            "total_imported": imported,
            "pending": pending_existing,
        }
