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
            # Migration: add mtime column for fast-path deduplication (skip hashing unchanged files)
            needs_mtime_backfill = False
            try:
                await db.execute("ALTER TABLE photo_assets ADD COLUMN mtime REAL")
                needs_mtime_backfill = True  # Column was just added, backfill needed
            except Exception:
                pass  # Column already exists
            # Index for fast lookup by path+size (used before hashing)
            await db.execute(
                """CREATE INDEX IF NOT EXISTS idx_photo_path_size ON photo_assets(source_path, file_size)"""
            )
            # Migrations tracking table
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS migrations (
                    name TEXT PRIMARY KEY,
                    applied_at REAL NOT NULL
                )
                """
            )

            # Migration: add origin column for tracking where photo came from
            try:
                await db.execute(
                    "ALTER TABLE photo_assets ADD COLUMN origin TEXT DEFAULT 'nextcloud'"
                )
            except Exception:
                pass  # Column already exists

            # Migration: add sync_direction column
            try:
                await db.execute(
                    "ALTER TABLE photo_assets ADD COLUMN sync_direction TEXT DEFAULT 'import'"
                )
            except Exception:
                pass  # Column already exists

            # Migration: add nextcloud_path column for export tracking
            try:
                await db.execute("ALTER TABLE photo_assets ADD COLUMN nextcloud_path TEXT")
            except Exception:
                pass  # Column already exists

            # Migration: add nextcloud_etag column for change detection
            try:
                await db.execute("ALTER TABLE photo_assets ADD COLUMN nextcloud_etag TEXT")
            except Exception:
                pass  # Column already exists

            # Table for tracking exports from Apple Photos to NextCloud
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS photo_exports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    content_hash TEXT UNIQUE NOT NULL,
                    apple_asset_uuid TEXT NOT NULL,
                    nextcloud_path TEXT NOT NULL,
                    nextcloud_etag TEXT,
                    file_size INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    captured_at TEXT,
                    exported_at REAL NOT NULL
                )
                """
            )
            await db.execute(
                """CREATE INDEX IF NOT EXISTS idx_export_hash ON photo_exports(content_hash)"""
            )
            await db.execute(
                """CREATE INDEX IF NOT EXISTS idx_export_uuid ON photo_exports(apple_asset_uuid)"""
            )

            # Singleton table for export state (baseline date, last export time)
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS photo_export_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    baseline_date REAL,
                    last_export REAL
                )
                """
            )

            await db.commit()

            # Backfill mtime from filesystem for existing records (one-time migration)
            if needs_mtime_backfill:
                await self._backfill_mtime(db)

    async def _backfill_mtime(self, db: aiosqlite.Connection) -> None:
        """Populate mtime column from filesystem for all existing records."""
        logger.info("Backfilling mtime for existing photo records...")
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, source_path FROM photo_assets WHERE mtime IS NULL"
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            logger.info("No records need mtime backfill")
            return

        updated = 0
        for row in rows:
            path = Path(row["source_path"])
            if path.exists():
                try:
                    mtime = path.stat().st_mtime
                    await db.execute(
                        "UPDATE photo_assets SET mtime = ? WHERE id = ?",
                        (mtime, row["id"]),
                    )
                    updated += 1
                except OSError:
                    pass  # File inaccessible, skip

        await db.commit()
        logger.info("Backfilled mtime for %d of %d records", updated, len(rows))

    async def has_migration(self, name: str) -> bool:
        """Check if a migration has been applied."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM migrations WHERE name = ?", (name,)
            ) as cursor:
                return await cursor.fetchone() is not None

    async def set_migration(self, name: str) -> None:
        """Mark a migration as applied."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO migrations (name, applied_at) VALUES (?, ?)",
                (name, datetime.utcnow().timestamp()),
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

    async def get_by_path_and_size(self, path: Path, size: int) -> dict | None:
        """Fast lookup by path and size for deduplication before hashing."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM photo_assets WHERE source_path = ? AND file_size = ?",
                (str(path), size),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def update_mtime(self, content_hash: str, mtime: datetime) -> None:
        """Backfill mtime for existing records (migration from older schema)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE photo_assets SET mtime = ? WHERE content_hash = ? AND mtime IS NULL",
                (mtime.timestamp(), content_hash),
            )
            await db.commit()

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
        mtime: datetime | None = None,
        origin: str = "nextcloud",
        sync_direction: str = "import",
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR IGNORE INTO photo_assets
                (content_hash, source_path, file_size, media_type, source_name, album,
                 captured_at, first_seen, mtime, origin, sync_direction)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    mtime.timestamp() if mtime else None,
                    origin,
                    sync_direction,
                ),
            )
            await db.commit()

    async def mark_all_imported(self) -> None:
        """Mark all records with NULL last_imported as imported.

        Used for migration: initial scan recorded files but didn't mark them imported.
        """
        async with aiosqlite.connect(self.db_path) as db:
            result = await db.execute(
                """
                UPDATE photo_assets
                SET last_imported = first_seen
                WHERE last_imported IS NULL
                """
            )
            await db.commit()
            logger.info("Marked %d records as imported", result.rowcount)

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

    # Export tracking methods

    async def get_export_by_hash(self, content_hash: str) -> dict | None:
        """Check if a photo has been exported by its content hash."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM photo_exports WHERE content_hash = ?",
                (content_hash,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_export_by_uuid(self, apple_asset_uuid: str) -> dict | None:
        """Check if a photo has been exported by its Apple Photos UUID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM photo_exports WHERE apple_asset_uuid = ?",
                (apple_asset_uuid,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def record_export(
        self,
        *,
        content_hash: str,
        apple_asset_uuid: str,
        nextcloud_path: str,
        nextcloud_etag: str | None,
        file_size: int,
        media_type: str,
        captured_at: datetime | None,
    ) -> None:
        """Record a photo export to NextCloud."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO photo_exports
                (content_hash, apple_asset_uuid, nextcloud_path, nextcloud_etag,
                 file_size, media_type, captured_at, exported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content_hash,
                    apple_asset_uuid,
                    nextcloud_path,
                    nextcloud_etag,
                    file_size,
                    media_type,
                    captured_at.isoformat() if captured_at else None,
                    datetime.utcnow().timestamp(),
                ),
            )
            await db.commit()

    async def get_export_state(self) -> dict | None:
        """Get the export state (baseline date, last export time)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM photo_export_state WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def set_export_baseline(self, baseline_date: datetime | None = None) -> None:
        """Set the export baseline date. Photos before this date won't be exported."""
        if baseline_date is None:
            baseline_date = datetime.utcnow()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO photo_export_state (id, baseline_date, last_export)
                VALUES (1, ?, NULL)
                ON CONFLICT(id) DO UPDATE SET baseline_date = excluded.baseline_date
                """,
                (baseline_date.timestamp(),),
            )
            await db.commit()
            logger.info("Set export baseline to %s", baseline_date.isoformat())

    async def update_last_export(self) -> None:
        """Update the last export timestamp."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO photo_export_state (id, baseline_date, last_export)
                VALUES (1, NULL, ?)
                ON CONFLICT(id) DO UPDATE SET last_export = excluded.last_export
                """,
                (datetime.utcnow().timestamp(),),
            )
            await db.commit()

    async def get_export_stats(self) -> dict[str, int]:
        """Get export statistics."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM photo_exports"
            ) as cursor:
                total_exported = (await cursor.fetchone())[0]

        return {
            "total_exported": total_exported,
        }
