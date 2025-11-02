"""Password synchronization engine for Apple Passwords and Bitwarden."""

import logging
from pathlib import Path

from ..sources.passwords.apple_csv import ApplePasswordsCSVParser
from ..sources.passwords.bitwarden_csv import BitwardenCSVParser
from ..sources.passwords.models import PasswordEntry
from ..utils.db import PasswordsDB

logger = logging.getLogger(__name__)


class PasswordsSyncEngine:
    """
    Orchestrates password synchronization between Apple Passwords and Bitwarden.

    Uses ephemeral processing: plaintext passwords are never stored in the database,
    only SHA-256 hashes for change detection.
    """

    def __init__(self, db: PasswordsDB):
        """
        Initialize sync engine.

        Args:
            db: PasswordsDB instance
        """
        self.db = db

    async def import_apple_csv(self, csv_path: Path) -> dict:
        """
        Import Apple Passwords CSV export.

        Args:
            csv_path: Path to Apple Passwords CSV file

        Returns:
            Statistics dictionary:
            {
                'new': 5,
                'updated': 12,
                'duplicates': 3,
                'unchanged': 2132,
                'errors': 0,
                'total_processed': 2152
            }
        """
        logger.info(f"Importing Apple Passwords CSV: {csv_path}")

        # Parse CSV
        entries = ApplePasswordsCSVParser.parse_file(csv_path)

        # Statistics
        stats = {
            "new": 0,
            "updated": 0,
            "duplicates": 0,
            "unchanged": 0,
            "errors": 0,
            "total_processed": len(entries),
        }

        # Deduplicate entries (in case CSV has duplicates)
        entries = self._deduplicate_entries(entries)
        stats["duplicates"] = stats["total_processed"] - len(entries)

        # Process each entry
        for entry in entries:
            try:
                # Check if entry exists
                existing = await self.db.get_entry_by_key(
                    entry.title, entry.url, entry.username
                )

                password_hash = entry.get_password_hash()

                if existing:
                    # Entry exists - check if password changed
                    if existing["password_hash"] != password_hash:
                        # Password changed
                        await self.db.upsert_entry(
                            title=entry.title,
                            username=entry.username,
                            password_hash=password_hash,
                            url=entry.url,
                            notes=entry.notes,
                            otp_auth=entry.otp_auth,
                            folder=entry.folder,
                            source="apple",
                        )
                        stats["updated"] += 1
                        logger.debug(f"Updated: {entry.title} / {entry.username}")
                    else:
                        # No change - just update last_synced_at
                        await self.db.upsert_entry(
                            title=entry.title,
                            username=entry.username,
                            password_hash=password_hash,
                            url=entry.url,
                            notes=entry.notes,
                            otp_auth=entry.otp_auth,
                            folder=entry.folder,
                            source="apple",
                        )
                        stats["unchanged"] += 1
                else:
                    # New entry
                    await self.db.upsert_entry(
                        title=entry.title,
                        username=entry.username,
                        password_hash=password_hash,
                        url=entry.url,
                        notes=entry.notes,
                        otp_auth=entry.otp_auth,
                        folder=entry.folder,
                        source="apple",
                    )
                    stats["new"] += 1
                    logger.debug(f"New entry: {entry.title} / {entry.username}")

            except Exception as e:
                logger.error(f"Error processing entry {entry.title}: {e}")
                stats["errors"] += 1

        # Record sync metadata
        await self.db.record_sync(
            sync_type="apple_import",
            file_path=str(csv_path),
            entry_count=len(entries),
            notes=f"New: {stats['new']}, Updated: {stats['updated']}, "
            f"Duplicates: {stats['duplicates']}, Errors: {stats['errors']}",
        )

        logger.info(
            f"Apple import complete: {stats['new']} new, {stats['updated']} updated, "
            f"{stats['duplicates']} duplicates, {stats['errors']} errors"
        )

        return stats

    async def export_bitwarden_csv(
        self,
        output_path: Path,
        apple_csv_path: Path,
        folder_mapping: dict[str, str] | None = None,
    ) -> int:
        """
        Generate Bitwarden-formatted CSV from Apple export.

        Args:
            output_path: Where to write Bitwarden CSV
            apple_csv_path: Original Apple CSV (for plaintext passwords)
            folder_mapping: Optional folder name mapping

        Returns:
            Number of entries exported
        """
        logger.info(f"Generating Bitwarden CSV: {output_path}")

        # Read Apple CSV for plaintext passwords
        apple_entries = ApplePasswordsCSVParser.parse_file(apple_csv_path)

        # Get entries from DB (for filtering/metadata)
        db_entries = await self.db.get_all_entries(source="apple")

        # Build lookup map from DB (by dedup key)
        db_map = {}
        for db_entry in db_entries:
            key = (
                db_entry["title"].lower().strip(),
                db_entry["url"].lower().strip() if db_entry["url"] else None,
                db_entry["username"].lower().strip(),
            )
            db_map[key] = db_entry

        # Filter Apple entries to only those in DB
        filtered_entries = []
        for entry in apple_entries:
            key = entry.get_dedup_key()
            if key in db_map:
                # Optionally add folder from DB
                db_entry = db_map[key]
                if db_entry.get("folder"):
                    entry.folder = db_entry["folder"]
                filtered_entries.append(entry)

        # Write Bitwarden CSV
        BitwardenCSVParser.write_file(filtered_entries, output_path, folder_mapping)

        # Record sync metadata
        await self.db.record_sync(
            sync_type="bitwarden_export",
            file_path=str(output_path),
            entry_count=len(filtered_entries),
        )

        logger.info(f"Bitwarden export complete: {len(filtered_entries)} entries")

        return len(filtered_entries)

    async def import_bitwarden_csv(self, csv_path: Path) -> dict:
        """
        Import Bitwarden export CSV.

        Args:
            csv_path: Path to Bitwarden CSV file

        Returns:
            Statistics dictionary (same format as import_apple_csv)
        """
        logger.info(f"Importing Bitwarden CSV: {csv_path}")

        # Parse CSV
        entries = BitwardenCSVParser.parse_file(csv_path)

        # Statistics
        stats = {
            "new": 0,
            "updated": 0,
            "duplicates": 0,
            "unchanged": 0,
            "errors": 0,
            "total_processed": len(entries),
        }

        # Deduplicate
        entries = self._deduplicate_entries(entries)
        stats["duplicates"] = stats["total_processed"] - len(entries)

        # Process each entry
        for entry in entries:
            try:
                existing = await self.db.get_entry_by_key(
                    entry.title, entry.url, entry.username
                )

                password_hash = entry.get_password_hash()

                if existing:
                    if existing["password_hash"] != password_hash:
                        await self.db.upsert_entry(
                            title=entry.title,
                            username=entry.username,
                            password_hash=password_hash,
                            url=entry.url,
                            notes=entry.notes,
                            otp_auth=entry.otp_auth,
                            folder=entry.folder,
                            source="bitwarden",
                        )
                        stats["updated"] += 1
                    else:
                        await self.db.upsert_entry(
                            title=entry.title,
                            username=entry.username,
                            password_hash=password_hash,
                            url=entry.url,
                            notes=entry.notes,
                            otp_auth=entry.otp_auth,
                            folder=entry.folder,
                            source="bitwarden",
                        )
                        stats["unchanged"] += 1
                else:
                    await self.db.upsert_entry(
                        title=entry.title,
                        username=entry.username,
                        password_hash=password_hash,
                        url=entry.url,
                        notes=entry.notes,
                        otp_auth=entry.otp_auth,
                        folder=entry.folder,
                        source="bitwarden",
                    )
                    stats["new"] += 1

            except Exception as e:
                logger.error(f"Error processing entry {entry.title}: {e}")
                stats["errors"] += 1

        # Record sync
        await self.db.record_sync(
            sync_type="bitwarden_import",
            file_path=str(csv_path),
            entry_count=len(entries),
        )

        logger.info(
            f"Bitwarden import complete: {stats['new']} new, {stats['updated']} updated"
        )

        return stats

    async def export_apple_csv(
        self, output_path: Path, bitwarden_csv_path: Path
    ) -> int:
        """
        Generate Apple Passwords CSV from Bitwarden export.

        Only exports entries that exist in Bitwarden but not in Apple.

        Args:
            output_path: Where to write Apple CSV
            bitwarden_csv_path: Original Bitwarden CSV (for plaintext passwords)

        Returns:
            Number of entries exported
        """
        logger.info(f"Generating Apple Passwords CSV: {output_path}")

        # Read Bitwarden CSV
        bitwarden_entries = BitwardenCSVParser.parse_file(bitwarden_csv_path)

        # Get Apple entries from DB
        apple_db_entries = await self.db.get_all_entries(source="apple")

        # Build set of Apple dedup keys
        apple_keys = set()
        for db_entry in apple_db_entries:
            key = (
                db_entry["title"].lower().strip(),
                db_entry["url"].lower().strip() if db_entry["url"] else None,
                db_entry["username"].lower().strip(),
            )
            apple_keys.add(key)

        # Filter to entries only in Bitwarden
        missing_entries = []
        for entry in bitwarden_entries:
            key = entry.get_dedup_key()
            if key not in apple_keys:
                missing_entries.append(entry)

        if missing_entries:
            # Write Apple CSV
            ApplePasswordsCSVParser.write_file(missing_entries, output_path)

            # Record sync
            await self.db.record_sync(
                sync_type="apple_export",
                file_path=str(output_path),
                entry_count=len(missing_entries),
            )

        logger.info(f"Apple export complete: {len(missing_entries)} entries")

        return len(missing_entries)

    async def compare_sources(
        self, apple_csv: Path, bitwarden_csv: Path
    ) -> dict:
        """
        Compare Apple and Bitwarden exports.

        Args:
            apple_csv: Path to Apple Passwords CSV
            bitwarden_csv: Path to Bitwarden CSV

        Returns:
            {
                'in_apple_only': [PasswordEntry, ...],
                'in_bitwarden_only': [PasswordEntry, ...],
                'in_both': [PasswordEntry, ...],
                'conflicts': [
                    {'entry': PasswordEntry, 'apple_hash': str, 'bitwarden_hash': str},
                    ...
                ]
            }
        """
        logger.info("Comparing Apple and Bitwarden exports")

        # Parse both CSVs
        apple_entries = ApplePasswordsCSVParser.parse_file(apple_csv)
        bitwarden_entries = BitwardenCSVParser.parse_file(bitwarden_csv)

        # Build maps by dedup key
        apple_map = {entry.get_dedup_key(): entry for entry in apple_entries}
        bitwarden_map = {entry.get_dedup_key(): entry for entry in bitwarden_entries}

        apple_keys = set(apple_map.keys())
        bitwarden_keys = set(bitwarden_map.keys())

        # Find differences
        only_in_apple = [apple_map[k] for k in apple_keys - bitwarden_keys]
        only_in_bitwarden = [bitwarden_map[k] for k in bitwarden_keys - apple_keys]
        in_both_keys = apple_keys & bitwarden_keys

        in_both = []
        conflicts = []

        for key in in_both_keys:
            apple_entry = apple_map[key]
            bitwarden_entry = bitwarden_map[key]

            apple_hash = apple_entry.get_password_hash()
            bitwarden_hash = bitwarden_entry.get_password_hash()

            if apple_hash != bitwarden_hash:
                # Password mismatch
                conflicts.append(
                    {
                        "entry": apple_entry,
                        "apple_hash": apple_hash,
                        "bitwarden_hash": bitwarden_hash,
                    }
                )
            else:
                in_both.append(apple_entry)

        logger.info(
            f"Comparison complete: {len(only_in_apple)} in Apple only, "
            f"{len(only_in_bitwarden)} in Bitwarden only, "
            f"{len(in_both)} in both, {len(conflicts)} conflicts"
        )

        return {
            "in_apple_only": only_in_apple,
            "in_bitwarden_only": only_in_bitwarden,
            "in_both": in_both,
            "conflicts": conflicts,
        }

    async def sync(
        self,
        apple_csv_path: Path,
        vaultwarden_client: "VaultwardenAPIClient",  # type: ignore
        output_apple_csv: Path | None = None,
    ) -> dict:
        """
        Full auto-sync: Apple → VaultWarden (push) and VaultWarden → Apple (pull).

        Args:
            apple_csv_path: Path to Apple Passwords CSV export
            vaultwarden_client: Authenticated VaultwardenAPIClient
            output_apple_csv: Path for generated Apple CSV with new VaultWarden entries
                            (defaults to data_dir/apple-import.csv)

        Returns:
            Statistics dictionary:
            {
                'push': {'created': 5, 'updated': 12, 'skipped': 2000, ...},
                'pull': {'new_entries': 3, 'output_file': 'path/to/csv'},
                'total_time': 5.2
            }
        """
        import time

        logger.info("Starting full password sync")
        start_time = time.time()

        stats = {
            "push": {},
            "pull": {},
            "total_time": 0,
        }

        # ========================================
        # Phase 1: Apple → VaultWarden (Push)
        # ========================================
        logger.info("Phase 1: Pushing Apple passwords to VaultWarden")

        # Import Apple CSV to database
        import_stats = await self.import_apple_csv(apple_csv_path)

        # Get all Apple entries from database
        apple_db_entries = await self.db.get_all_entries(source="apple")

        # Parse Apple CSV for plaintext passwords
        from ..sources.passwords.apple_csv import ApplePasswordsCSVParser

        apple_entries = ApplePasswordsCSVParser.parse_file(apple_csv_path)

        # Build map of dedup_key → PasswordEntry (with plaintext passwords)
        apple_map = {}
        for entry in apple_entries:
            apple_map[entry.get_dedup_key()] = entry

        # Match database entries with plaintext passwords
        entries_to_push = []
        for db_entry in apple_db_entries:
            key = (
                db_entry["title"].lower().strip(),
                db_entry["url"].lower().strip() if db_entry["url"] else None,
                db_entry["username"].lower().strip(),
            )
            if key in apple_map:
                entries_to_push.append(apple_map[key])

        # Push to VaultWarden
        push_stats = await vaultwarden_client.push_passwords(entries_to_push)
        stats["push"] = push_stats

        logger.info(
            f"Push complete: {push_stats['created']} created, "
            f"{push_stats['updated']} updated, {push_stats['skipped']} skipped"
        )

        # ========================================
        # Phase 2: VaultWarden → Apple (Pull)
        # ========================================
        logger.info("Phase 2: Pulling new VaultWarden passwords")

        # Fetch all passwords from VaultWarden
        vw_entries = await vaultwarden_client.pull_passwords()

        # Import VaultWarden entries to database (updates DB state)
        # This is needed so we can track what's in VaultWarden
        for entry in vw_entries:
            try:
                await self.db.upsert_entry(
                    title=entry.title,
                    username=entry.username,
                    password_hash=entry.get_password_hash(),
                    url=entry.url,
                    notes=entry.notes,
                    otp_auth=entry.otp_auth,
                    folder=entry.folder,
                    source="vaultwarden",
                )
            except Exception as e:
                logger.error(f"Failed to update DB with VaultWarden entry {entry.title}: {e}")

        # Get Apple entries dedup keys
        apple_keys = set()
        for db_entry in apple_db_entries:
            key = (
                db_entry["title"].lower().strip(),
                db_entry["url"].lower().strip() if db_entry["url"] else None,
                db_entry["username"].lower().strip(),
            )
            apple_keys.add(key)

        # Find entries only in VaultWarden (not in Apple)
        new_entries = []
        for entry in vw_entries:
            key = entry.get_dedup_key()
            if key not in apple_keys:
                new_entries.append(entry)

        # Generate Apple CSV if there are new entries
        output_path = None
        if new_entries:
            if output_apple_csv is None:
                # Default output path
                from ..core.config import AppConfig

                cfg = AppConfig()
                cfg.ensure_data_dir()
                output_path = cfg.general.data_dir / "apple-import.csv"
            else:
                output_path = output_apple_csv

            ApplePasswordsCSVParser.write_file(new_entries, output_path)

            # Record sync
            await self.db.record_sync(
                sync_type="vaultwarden_pull",
                file_path=str(output_path),
                entry_count=len(new_entries),
            )

            logger.info(f"Generated Apple CSV with {len(new_entries)} new entries: {output_path}")
        else:
            logger.info("No new entries from VaultWarden")

        stats["pull"] = {
            "new_entries": len(new_entries),
            "output_file": str(output_path) if output_path else None,
        }

        # Total time
        stats["total_time"] = time.time() - start_time

        logger.info(f"Full sync complete in {stats['total_time']:.1f}s")

        return stats

    def _deduplicate_entries(
        self, entries: list[PasswordEntry]
    ) -> list[PasswordEntry]:
        """
        Deduplicate entries, keeping the best one.

        Priority:
        1. Entry with most fields populated
        2. First entry encountered

        Args:
            entries: List of PasswordEntry objects

        Returns:
            Deduplicated list
        """
        seen = {}
        for entry in entries:
            key = entry.get_dedup_key()
            if key in seen:
                existing = seen[key]
                if self._is_better_entry(entry, existing):
                    seen[key] = entry
                    logger.debug(f"Replaced duplicate with better entry: {entry.title}")
            else:
                seen[key] = entry

        return list(seen.values())

    def _is_better_entry(
        self, entry1: PasswordEntry, entry2: PasswordEntry
    ) -> bool:
        """
        Determine which entry is "better" for deduplication.

        Args:
            entry1: First entry
            entry2: Second entry

        Returns:
            True if entry1 is better than entry2
        """
        # Count non-None/non-empty fields
        def count_fields(entry: PasswordEntry) -> int:
            return sum(
                [
                    bool(entry.url),
                    bool(entry.notes),
                    bool(entry.otp_auth),
                    bool(entry.folder),
                ]
            )

        return count_fields(entry1) > count_fields(entry2)
