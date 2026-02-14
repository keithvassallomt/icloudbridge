"""Read-only access to Apple Photos library database."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from icloudbridge.sources.photos.constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

logger = logging.getLogger(__name__)

# Apple's Core Data epoch: 2001-01-01 00:00:00 UTC
CORE_DATA_EPOCH = datetime(2001, 1, 1).timestamp()


@dataclass(slots=True)
class PhotoAsset:
    """Represents a photo/video asset from Apple Photos library."""

    uuid: str
    filename: str
    original_filename: str | None
    file_path: Path | None
    media_type: str  # "image" or "video"
    file_size: int
    created_date: datetime
    modified_date: datetime | None
    is_favorite: bool
    is_hidden: bool
    is_in_trash: bool
    album_names: list[str]


@dataclass(slots=True)
class AlbumInfo:
    """Represents an album in Apple Photos."""

    uuid: str
    name: str
    asset_count: int


class PhotosLibraryReader:
    """Read-only access to Apple Photos library.

    Uses direct SQLite access to Photos.sqlite for metadata enumeration.
    This is an undocumented format that may change with macOS updates.
    """

    def __init__(self, library_path: Path | None = None):
        self.library_path = library_path or self._default_library_path()
        self.db_path = self.library_path / "database" / "Photos.sqlite"
        self._temp_db_path: Path | None = None
        self._has_filesize_column: bool | None = None  # Cached schema check

    @staticmethod
    def _default_library_path() -> Path:
        """Get the default Photos library path."""
        # Standard location
        default = Path.home() / "Pictures" / "Photos Library.photoslibrary"
        if default.exists():
            return default

        # Try to find it via plist
        plist_path = (
            Path.home()
            / "Library"
            / "Containers"
            / "com.apple.photolibraryd"
            / "Data"
            / "Library"
            / "Preferences"
            / "com.apple.photolibraryd.plist"
        )
        if plist_path.exists():
            # Could parse plist here, but for now just return default
            pass

        return default

    def _get_originals_path(self) -> Path:
        """Get the path to original photo files."""
        return self.library_path / "originals"

    async def _ensure_db_copy(self) -> Path:
        """Copy the database to a temp location to avoid locking issues.

        Photos.app may have the database locked. We copy it to read safely.
        """
        if self._temp_db_path and self._temp_db_path.exists():
            return self._temp_db_path

        if not self.db_path.exists():
            raise FileNotFoundError(f"Photos database not found: {self.db_path}")

        # Copy database files to temp directory
        temp_dir = Path(tempfile.mkdtemp(prefix="icloudbridge_photos_"))
        self._temp_db_path = temp_dir / "Photos.sqlite"

        # Copy main database and WAL files
        await asyncio.to_thread(shutil.copy2, self.db_path, self._temp_db_path)

        wal_path = self.db_path.with_suffix(".sqlite-wal")
        if wal_path.exists():
            await asyncio.to_thread(
                shutil.copy2, wal_path, self._temp_db_path.with_suffix(".sqlite-wal")
            )

        shm_path = self.db_path.with_suffix(".sqlite-shm")
        if shm_path.exists():
            await asyncio.to_thread(
                shutil.copy2, shm_path, self._temp_db_path.with_suffix(".sqlite-shm")
            )

        logger.debug("Copied Photos database to %s", self._temp_db_path)
        return self._temp_db_path

    def cleanup(self) -> None:
        """Clean up temporary database copy."""
        if self._temp_db_path and self._temp_db_path.parent.exists():
            shutil.rmtree(self._temp_db_path.parent, ignore_errors=True)
            self._temp_db_path = None

    def detect_schema_version(self) -> str | None:
        """Detect the Photos.sqlite schema version.

        Returns a version string or None if unable to detect.
        """
        if not self.db_path.exists():
            return None

        try:
            import sqlite3

            conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            cursor = conn.execute(
                "SELECT Z_VERSION, Z_MINOR, Z_RELEASE FROM Z_METADATA LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return f"{row[0]}.{row[1]}.{row[2]}"
        except Exception as e:
            logger.warning("Failed to detect Photos schema version: %s", e)

        return None

    async def _check_filesize_column(self, db: aiosqlite.Connection) -> bool:
        """Check if ZASSET table has ZFILESIZE column.

        Some macOS versions don't include this column in the Photos database.
        """
        if self._has_filesize_column is not None:
            return self._has_filesize_column

        try:
            async with db.execute("PRAGMA table_info(ZASSET)") as cursor:
                async for row in cursor:
                    # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
                    if row[1] == "ZFILESIZE":
                        self._has_filesize_column = True
                        return True
            self._has_filesize_column = False
            logger.debug("ZASSET table does not have ZFILESIZE column")
            return False
        except Exception as e:
            logger.warning("Failed to check ZFILESIZE column: %s", e)
            self._has_filesize_column = False
            return False

    async def enumerate_assets(
        self,
        since_date: datetime | None = None,
        album_filter: str | None = None,
        include_hidden: bool = False,
        include_trash: bool = False,
        limit: int | None = None,
    ) -> AsyncIterator[PhotoAsset]:
        """Enumerate photos from the library.

        Args:
            since_date: Only return photos created after this date
            album_filter: Filter to specific album name
            include_hidden: Include hidden photos
            include_trash: Include photos in trash
            limit: Maximum number of results

        Yields:
            PhotoAsset objects with metadata
        """
        db_path = await self._ensure_db_copy()

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            # Check if ZFILESIZE column exists (varies by macOS version)
            has_filesize = await self._check_filesize_column(db)
            filesize_select = "a.ZFILESIZE as file_size," if has_filesize else "0 as file_size,"

            # Build query with filters
            query = f"""
                SELECT
                    a.ZUUID as uuid,
                    a.ZFILENAME as filename,
                    a.ZDIRECTORY as directory,
                    a.ZKIND as kind,
                    {filesize_select}
                    a.ZDATECREATED as date_created,
                    a.ZMODIFICATIONDATE as date_modified,
                    a.ZFAVORITE as is_favorite,
                    a.ZHIDDEN as is_hidden,
                    a.ZTRASHEDSTATE as trash_state,
                    attr.ZORIGINALFILENAME as original_filename
                FROM ZASSET a
                LEFT JOIN ZADDITIONALASSETATTRIBUTES attr ON a.Z_PK = attr.ZASSET
                WHERE 1=1
            """
            params: list = []

            if since_date:
                # Convert to Core Data timestamp
                core_data_ts = since_date.timestamp() - CORE_DATA_EPOCH
                query += " AND a.ZDATECREATED > ?"
                params.append(core_data_ts)

            if not include_hidden:
                query += " AND (a.ZHIDDEN = 0 OR a.ZHIDDEN IS NULL)"

            if not include_trash:
                query += " AND (a.ZTRASHEDSTATE = 0 OR a.ZTRASHEDSTATE IS NULL)"

            query += " ORDER BY a.ZDATECREATED DESC"

            if limit:
                query += f" LIMIT {limit}"

            # Get album memberships for filtering/enrichment
            album_map: dict[str, list[str]] = {}
            if album_filter:
                # Filter by album
                album_query = """
                    SELECT
                        a.ZUUID as asset_uuid,
                        alb.ZTITLE as album_name
                    FROM ZASSET a
                    JOIN Z_26ASSETS za ON a.Z_PK = za.Z_34ASSETS
                    JOIN ZGENERICALBUM alb ON za.Z_26ALBUMS = alb.Z_PK
                    WHERE alb.ZTITLE = ?
                """
                async with db.execute(album_query, (album_filter,)) as cursor:
                    async for row in cursor:
                        asset_uuid = row["asset_uuid"]
                        if asset_uuid not in album_map:
                            album_map[asset_uuid] = []
                        album_map[asset_uuid].append(row["album_name"])

            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    uuid = row["uuid"]

                    # Skip if album filter and not in album
                    if album_filter and uuid not in album_map:
                        continue

                    # Determine media type
                    kind = row["kind"] or 0
                    # ZKIND: 0 = image, 1 = video
                    media_type = "video" if kind == 1 else "image"

                    # Get file path
                    file_path = self._resolve_file_path(
                        uuid, row["directory"], row["filename"]
                    )

                    # Convert Core Data timestamps
                    created_ts = row["date_created"]
                    created_date = (
                        datetime.fromtimestamp(created_ts + CORE_DATA_EPOCH)
                        if created_ts
                        else datetime.now()
                    )

                    modified_ts = row["date_modified"]
                    modified_date = (
                        datetime.fromtimestamp(modified_ts + CORE_DATA_EPOCH)
                        if modified_ts
                        else None
                    )

                    # Get album names for this asset
                    albums = album_map.get(uuid, [])

                    yield PhotoAsset(
                        uuid=uuid,
                        filename=row["filename"] or "",
                        original_filename=row["original_filename"],
                        file_path=file_path,
                        media_type=media_type,
                        file_size=row["file_size"] or 0,
                        created_date=created_date,
                        modified_date=modified_date,
                        is_favorite=bool(row["is_favorite"]),
                        is_hidden=bool(row["is_hidden"]),
                        is_in_trash=bool(row["trash_state"]),
                        album_names=albums,
                    )

    def _resolve_file_path(
        self, uuid: str, directory: str | None, filename: str | None
    ) -> Path | None:
        """Resolve the actual file path for an asset."""
        if not filename:
            return None

        originals = self._get_originals_path()

        # Try directory-based path first
        if directory:
            path = originals / directory / filename
            if path.exists():
                return path

        # Try UUID-based path (common structure)
        # Files are often in originals/X/UUID/filename where X is first char of UUID
        if uuid:
            first_char = uuid[0].upper()
            path = originals / first_char / uuid / filename
            if path.exists():
                return path

        # Try glob search as last resort
        pattern = f"**/{filename}"
        matches = list(originals.glob(pattern))
        if matches:
            return matches[0]

        return None

    async def get_asset_file_path(self, asset_uuid: str) -> Path | None:
        """Get the actual file path for an asset by UUID."""
        db_path = await self._ensure_db_copy()

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT ZDIRECTORY, ZFILENAME FROM ZASSET WHERE ZUUID = ?",
                (asset_uuid,),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._resolve_file_path(
                        asset_uuid, row["ZDIRECTORY"], row["ZFILENAME"]
                    )
        return None

    async def list_albums(self) -> list[AlbumInfo]:
        """List all user albums in the library."""
        db_path = await self._ensure_db_copy()
        albums: list[AlbumInfo] = []

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row

            # ZKIND 2 = user album (not smart album, folder, etc.)
            query = """
                SELECT
                    a.ZUUID as uuid,
                    a.ZTITLE as name,
                    (SELECT COUNT(*) FROM Z_26ASSETS WHERE Z_26ALBUMS = a.Z_PK) as asset_count
                FROM ZGENERICALBUM a
                WHERE a.ZKIND = 2 AND a.ZTITLE IS NOT NULL AND a.ZTRASHEDSTATE = 0
                ORDER BY a.ZTITLE
            """

            async with db.execute(query) as cursor:
                async for row in cursor:
                    albums.append(
                        AlbumInfo(
                            uuid=row["uuid"],
                            name=row["name"],
                            asset_count=row["asset_count"],
                        )
                    )

        return albums

    async def get_library_stats(self) -> dict:
        """Get statistics about the Photos library."""
        db_path = await self._ensure_db_copy()

        async with aiosqlite.connect(db_path) as db:
            # Total assets
            cursor = await db.execute(
                "SELECT COUNT(*) FROM ZASSET WHERE ZTRASHEDSTATE = 0 OR ZTRASHEDSTATE IS NULL"
            )
            total = (await cursor.fetchone())[0]

            # Images vs videos
            cursor = await db.execute(
                "SELECT COUNT(*) FROM ZASSET WHERE ZKIND = 0 AND (ZTRASHEDSTATE = 0 OR ZTRASHEDSTATE IS NULL)"
            )
            images = (await cursor.fetchone())[0]

            cursor = await db.execute(
                "SELECT COUNT(*) FROM ZASSET WHERE ZKIND = 1 AND (ZTRASHEDSTATE = 0 OR ZTRASHEDSTATE IS NULL)"
            )
            videos = (await cursor.fetchone())[0]

            # Favorites
            cursor = await db.execute(
                "SELECT COUNT(*) FROM ZASSET WHERE ZFAVORITE = 1 AND (ZTRASHEDSTATE = 0 OR ZTRASHEDSTATE IS NULL)"
            )
            favorites = (await cursor.fetchone())[0]

            # Albums
            cursor = await db.execute(
                "SELECT COUNT(*) FROM ZGENERICALBUM WHERE ZKIND = 2 AND ZTRASHEDSTATE = 0"
            )
            albums = (await cursor.fetchone())[0]

        return {
            "total_assets": total,
            "images": images,
            "videos": videos,
            "favorites": favorites,
            "albums": albums,
        }

    async def compute_asset_hash(self, asset: PhotoAsset) -> str | None:
        """Compute SHA256 hash of an asset's file."""
        if not asset.file_path or not asset.file_path.exists():
            return None

        def _hash_file(path: Path) -> str:
            h = hashlib.sha256()
            with path.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
            return h.hexdigest()

        return await asyncio.to_thread(_hash_file, asset.file_path)
