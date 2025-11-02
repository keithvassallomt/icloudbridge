"""Database utilities for tracking note synchronization state."""

import logging
from datetime import datetime
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)


class NotesDB:
    """
    Manages SQLite database for tracking note synchronization state.

    Stores mappings between local Apple Notes (by UUID) and remote markdown files (by path).
    This allows iCloudBridge to track which notes have been synced and when.
    """

    def __init__(self, db_path: Path):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """
        Initialize database schema if it doesn't exist.

        Creates the note_mapping table for tracking local-to-remote associations.
        """
        # Ensure database directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS note_mapping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_uuid TEXT NOT NULL,
                    local_name TEXT NOT NULL,
                    local_folder_uuid TEXT NOT NULL,
                    remote_path TEXT NOT NULL,
                    last_sync_timestamp REAL NOT NULL,
                    UNIQUE(local_uuid, remote_path)
                )
                """
            )

            # Create index for faster lookups
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_uuid
                ON note_mapping(local_uuid)
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_remote_path
                ON note_mapping(remote_path)
                """
            )

            await db.commit()
            logger.debug(f"Database initialized at {self.db_path}")

    async def get_mapping(self, local_uuid: str) -> dict | None:
        """
        Get the remote mapping for a local note UUID.

        Args:
            local_uuid: UUID of the local Apple Note

        Returns:
            Dictionary with mapping details, or None if not found
            Keys: id, local_uuid, local_name, local_folder_uuid,
                  remote_path, last_sync_timestamp
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM note_mapping
                WHERE local_uuid = ?
                """,
                (local_uuid,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_mapping_by_remote_path(self, remote_path: str) -> dict | None:
        """
        Get the local mapping for a remote note path.

        Args:
            remote_path: Path to the remote markdown file

        Returns:
            Dictionary with mapping details, or None if not found
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM note_mapping
                WHERE remote_path = ?
                """,
                (str(remote_path),),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def upsert_mapping(
        self,
        local_uuid: str,
        local_name: str,
        local_folder_uuid: str,
        remote_path: Path,
        timestamp: float,
    ) -> None:
        """
        Create or update a note mapping.

        Args:
            local_uuid: UUID of the local Apple Note
            local_name: Name of the note
            local_folder_uuid: UUID of the folder containing the note
            remote_path: Path to the remote markdown file
            timestamp: Last sync timestamp (Unix timestamp)
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO note_mapping
                (local_uuid, local_name, local_folder_uuid, remote_path, last_sync_timestamp)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(local_uuid, remote_path) DO UPDATE SET
                    local_name = excluded.local_name,
                    local_folder_uuid = excluded.local_folder_uuid,
                    last_sync_timestamp = excluded.last_sync_timestamp
                """,
                (local_uuid, local_name, local_folder_uuid, str(remote_path), timestamp),
            )
            await db.commit()
            logger.debug(f"Upserted mapping: {local_uuid} -> {remote_path}")

    async def delete_mapping(self, local_uuid: str) -> None:
        """
        Delete a note mapping.

        Args:
            local_uuid: UUID of the local Apple Note
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                DELETE FROM note_mapping
                WHERE local_uuid = ?
                """,
                (local_uuid,),
            )
            await db.commit()
            logger.debug(f"Deleted mapping for: {local_uuid}")

    async def delete_mapping_by_remote_path(self, remote_path: str) -> None:
        """
        Delete a note mapping by remote path.

        Args:
            remote_path: Path to the remote markdown file
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                DELETE FROM note_mapping
                WHERE remote_path = ?
                """,
                (str(remote_path),),
            )
            await db.commit()
            logger.debug(f"Deleted mapping for: {remote_path}")

    async def get_all_mappings(self) -> list[dict]:
        """
        Get all note mappings.

        Returns:
            List of dictionaries, each containing mapping details
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM note_mapping") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def clear_all_mappings(self) -> None:
        """
        Clear all note mappings from the database.

        This does NOT delete any notes - it only clears the sync tracking.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM note_mapping")
            await db.commit()
            logger.info("All note mappings cleared from database")

    async def get_mappings_for_folder(self, folder_uuid: str) -> list[dict]:
        """
        Get all mappings for a specific folder.

        Args:
            folder_uuid: UUID of the folder

        Returns:
            List of dictionaries containing mapping details
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM note_mapping
                WHERE local_folder_uuid = ?
                """,
                (folder_uuid,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def cleanup_orphaned_mappings(
        self, existing_local_uuids: set[str], existing_remote_paths: set[str]
    ) -> int:
        """
        Remove mappings for notes that no longer exist locally or remotely.

        Args:
            existing_local_uuids: Set of UUIDs that currently exist locally
            existing_remote_paths: Set of paths that currently exist remotely

        Returns:
            Number of orphaned mappings cleaned up
        """
        count = 0
        mappings = await self.get_all_mappings()

        async with aiosqlite.connect(self.db_path) as db:
            for mapping in mappings:
                local_uuid = mapping["local_uuid"]
                remote_path = mapping["remote_path"]

                # If note doesn't exist on either side, remove mapping
                if local_uuid not in existing_local_uuids and remote_path not in existing_remote_paths:
                    await db.execute(
                        "DELETE FROM note_mapping WHERE id = ?",
                        (mapping["id"],),
                    )
                    count += 1
                    logger.debug(f"Cleaned up orphaned mapping: {local_uuid} -> {remote_path}")

            await db.commit()

        if count > 0:
            logger.info(f"Cleaned up {count} orphaned note mappings")

        return count

    async def close(self) -> None:
        """Close database connection if open."""
        if self._connection:
            await self._connection.close()
            self._connection = None


class RemindersDB:
    """
    Manages SQLite database for tracking reminder synchronization state.

    Stores mappings between local Apple Reminders (by UUID) and remote CalDAV TODOs (by UID/URL).
    This allows iCloudBridge to track which reminders have been synced and when.
    """

    def __init__(self, db_path: Path):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """
        Initialize database schema if it doesn't exist.

        Creates the reminder_mapping table for tracking local-to-remote associations.
        """
        # Ensure database directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS reminder_mapping (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    local_uuid TEXT NOT NULL,
                    remote_uid TEXT NOT NULL,
                    local_title TEXT NOT NULL,
                    remote_caldav_url TEXT NOT NULL,
                    last_sync_timestamp REAL NOT NULL,
                    UNIQUE(local_uuid),
                    UNIQUE(remote_uid)
                )
                """
            )

            # Create indexes for faster lookups
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminder_local_uuid
                ON reminder_mapping(local_uuid)
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminder_remote_uid
                ON reminder_mapping(remote_uid)
                """
            )

            await db.commit()
            logger.debug(f"Reminders database initialized at {self.db_path}")

    async def add_mapping(
        self,
        local_uuid: str,
        remote_uid: str,
        local_title: str,
        remote_caldav_url: str,
        last_sync: datetime,
    ) -> None:
        """
        Add or update a mapping between local reminder and remote TODO.

        Args:
            local_uuid: UUID of the local Apple Reminder
            remote_uid: UID of the remote CalDAV TODO
            local_title: Title of the local reminder
            remote_caldav_url: CalDAV URL of the remote TODO
            last_sync: Timestamp of last sync
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO reminder_mapping
                (local_uuid, remote_uid, local_title, remote_caldav_url, last_sync_timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (local_uuid, remote_uid, local_title, remote_caldav_url, last_sync.timestamp()),
            )
            await db.commit()
            logger.debug(f"Added/updated reminder mapping: {local_uuid} <-> {remote_uid}")

    async def get_mapping(self, local_uuid: str) -> dict | None:
        """
        Get the remote mapping for a local reminder UUID.

        Args:
            local_uuid: UUID of the local Apple Reminder

        Returns:
            Dictionary with mapping details, or None if not found
            Keys: id, local_uuid, remote_uid, local_title,
                  remote_caldav_url, last_sync_timestamp
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM reminder_mapping
                WHERE local_uuid = ?
                """,
                (local_uuid,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_mapping_by_remote_uid(self, remote_uid: str) -> dict | None:
        """
        Get the local mapping for a remote TODO UID.

        Args:
            remote_uid: UID of the remote CalDAV TODO

        Returns:
            Dictionary with mapping details, or None if not found
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM reminder_mapping
                WHERE remote_uid = ?
                """,
                (remote_uid,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_all_mappings(self) -> list[dict]:
        """
        Get all reminder mappings from the database.

        Returns:
            List of dictionaries containing mapping details
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM reminder_mapping") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def update_mapping(
        self,
        local_uuid: str,
        remote_uid: str,
        remote_caldav_url: str,
        last_sync: datetime,
    ) -> None:
        """
        Update an existing mapping's timestamp and remote URL.

        Args:
            local_uuid: UUID of the local Apple Reminder
            remote_uid: UID of the remote CalDAV TODO
            remote_caldav_url: CalDAV URL of the remote TODO
            last_sync: New timestamp for last sync
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE reminder_mapping
                SET remote_uid = ?, remote_caldav_url = ?, last_sync_timestamp = ?
                WHERE local_uuid = ?
                """,
                (remote_uid, remote_caldav_url, last_sync.timestamp(), local_uuid),
            )
            await db.commit()
            logger.debug(f"Updated reminder mapping: {local_uuid} <-> {remote_uid}")

    async def delete_mapping(self, local_uuid: str | None = None, remote_uid: str | None = None) -> None:
        """
        Delete a mapping by local UUID or remote UID.

        Args:
            local_uuid: UUID of the local Apple Reminder (optional)
            remote_uid: UID of the remote CalDAV TODO (optional)
        """
        if not local_uuid and not remote_uid:
            raise ValueError("Must provide either local_uuid or remote_uid")

        async with aiosqlite.connect(self.db_path) as db:
            if local_uuid:
                await db.execute(
                    "DELETE FROM reminder_mapping WHERE local_uuid = ?",
                    (local_uuid,),
                )
            else:
                await db.execute(
                    "DELETE FROM reminder_mapping WHERE remote_uid = ?",
                    (remote_uid,),
                )
            await db.commit()
            logger.debug(f"Deleted reminder mapping: local={local_uuid}, remote={remote_uid}")

    async def clear_all_mappings(self) -> None:
        """
        Clear all reminder mappings from the database.

        This does NOT delete any reminders - it only clears the sync tracking.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM reminder_mapping")
            await db.commit()
            logger.info("All reminder mappings cleared from database")

    async def close(self) -> None:
        """Close database connection if open."""
        if self._connection:
            await self._connection.close()
            self._connection = None


class PasswordsDB:
    """
    Manages SQLite database for tracking password synchronization state.

    Stores metadata and password hashes (NOT plaintext passwords) for tracking
    sync state between Apple Passwords and Bitwarden. Uses ephemeral processing
    model where plaintext passwords are never stored in the database.
    """

    def __init__(self, db_path: Path):
        """
        Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._connection: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """
        Initialize database schema if it doesn't exist.

        Creates tables for tracking password entries and sync metadata.
        """
        # Ensure database directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.db_path) as db:
            # Password entries table
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS password_entry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    url TEXT,
                    username TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    notes TEXT,
                    otp_auth TEXT,
                    folder TEXT,
                    source TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_synced_at REAL,
                    UNIQUE(title, url, username)
                )
                """
            )

            # Sync metadata table
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_type TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    file_path TEXT,
                    entry_count INTEGER,
                    notes TEXT
                )
                """
            )

            # Create indexes for faster lookups
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_password_title
                ON password_entry(title)
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_password_url
                ON password_entry(url)
                """
            )

            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_password_source
                ON password_entry(source)
                """
            )

            await db.commit()
            logger.debug(f"Passwords database initialized at {self.db_path}")

    async def upsert_entry(
        self,
        title: str,
        username: str,
        password_hash: str,
        url: str | None = None,
        notes: str | None = None,
        otp_auth: str | None = None,
        folder: str | None = None,
        source: str = "apple",
    ) -> int:
        """
        Insert or update a password entry.

        Args:
            title: Entry title/name
            username: Username/email
            password_hash: SHA-256 hash of the password
            url: Associated URL
            notes: Additional notes
            otp_auth: OTP/2FA secret
            folder: Folder/collection name
            source: Source system ('apple' or 'bitwarden')

        Returns:
            Row ID of the inserted/updated entry
        """
        now = datetime.now().timestamp()

        async with aiosqlite.connect(self.db_path) as db:
            # Check if entry exists
            async with db.execute(
                """
                SELECT id, password_hash FROM password_entry
                WHERE title = ? AND url IS ? AND username = ?
                """,
                (title, url, username),
            ) as cursor:
                existing = await cursor.fetchone()

            if existing:
                entry_id, old_hash = existing
                # Only update if password hash changed
                if old_hash != password_hash:
                    await db.execute(
                        """
                        UPDATE password_entry
                        SET password_hash = ?, notes = ?, otp_auth = ?,
                            folder = ?, updated_at = ?, last_synced_at = ?
                        WHERE id = ?
                        """,
                        (password_hash, notes, otp_auth, folder, now, now, entry_id),
                    )
                    logger.debug(f"Updated password entry: {title}")
                else:
                    # Just update last_synced_at
                    await db.execute(
                        """
                        UPDATE password_entry
                        SET last_synced_at = ?
                        WHERE id = ?
                        """,
                        (now, entry_id),
                    )
                await db.commit()
                return entry_id
            else:
                # Insert new entry
                async with db.execute(
                    """
                    INSERT INTO password_entry
                    (title, url, username, password_hash, notes, otp_auth,
                     folder, source, created_at, updated_at, last_synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        url,
                        username,
                        password_hash,
                        notes,
                        otp_auth,
                        folder,
                        source,
                        now,
                        now,
                        now,
                    ),
                ) as cursor:
                    entry_id = cursor.lastrowid

                await db.commit()
                logger.debug(f"Inserted new password entry: {title}")
                return entry_id

    async def get_all_entries(self, source: str | None = None) -> list[dict]:
        """
        Get all password entries.

        Args:
            source: Filter by source ('apple' or 'bitwarden'), or None for all

        Returns:
            List of password entry dictionaries
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            if source:
                query = "SELECT * FROM password_entry WHERE source = ? ORDER BY title"
                params = (source,)
            else:
                query = "SELECT * FROM password_entry ORDER BY title"
                params = ()

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_entry_by_key(
        self, title: str, url: str | None, username: str
    ) -> dict | None:
        """
        Get a password entry by its unique key.

        Args:
            title: Entry title
            url: Entry URL (can be None)
            username: Username

        Returns:
            Password entry dictionary or None if not found
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM password_entry
                WHERE title = ? AND url IS ? AND username = ?
                """,
                (title, url, username),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def record_sync(
        self,
        sync_type: str,
        file_path: str | None = None,
        entry_count: int = 0,
        notes: str | None = None,
    ) -> None:
        """
        Record a sync operation in metadata table.

        Args:
            sync_type: Type of sync ('apple_import', 'bitwarden_import', 'bitwarden_export', etc.)
            file_path: Path to the CSV file involved
            entry_count: Number of entries processed
            notes: Additional notes about the sync
        """
        now = datetime.now().timestamp()

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sync_metadata
                (sync_type, timestamp, file_path, entry_count, notes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sync_type, now, file_path, entry_count, notes),
            )
            await db.commit()
            logger.info(f"Recorded sync: {sync_type} ({entry_count} entries)")

    async def get_last_sync(self, sync_type: str) -> dict | None:
        """
        Get the most recent sync of a given type.

        Args:
            sync_type: Type of sync to query

        Returns:
            Sync metadata dictionary or None if no sync found
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM sync_metadata
                WHERE sync_type = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (sync_type,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_stats(self) -> dict:
        """
        Get statistics about password entries.

        Returns:
            Dictionary with entry counts by source
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Total entries
            async with db.execute(
                "SELECT COUNT(*) FROM password_entry"
            ) as cursor:
                total = (await cursor.fetchone())[0]

            # By source
            async with db.execute(
                """
                SELECT source, COUNT(*) as count
                FROM password_entry
                GROUP BY source
                """
            ) as cursor:
                by_source = {row[0]: row[1] for row in await cursor.fetchall()}

            return {"total": total, "by_source": by_source}

    async def clear_all_entries(self) -> None:
        """Clear all password entries from the database."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM password_entry")
            await db.commit()
            logger.info("All password entries cleared from database")

    async def close(self) -> None:
        """Close database connection if open."""
        if self._connection:
            await self._connection.close()
            self._connection = None
