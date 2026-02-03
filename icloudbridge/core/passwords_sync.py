"""Password synchronization engine for Apple Passwords and password providers."""

import asyncio
import logging
import time
from pathlib import Path

import httpx

from ..sources.passwords.apple_csv import ApplePasswordsCSVParser
from ..sources.passwords.bitwarden_csv import BitwardenCSVParser
from ..sources.passwords.models import PasswordEntry
from ..sources.passwords.providers.base import PasswordProviderBase
from ..utils.db import PasswordsDB

logger = logging.getLogger(__name__)


class PasswordsSyncEngine:
    """
    Orchestrates password synchronization between Apple Passwords and password providers.

    Supports multiple providers (VaultWarden, Nextcloud Passwords, etc.) via the
    PasswordProviderBase interface.

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
        """Compare Apple and Bitwarden exports."""

        logger.info("Comparing Apple and Bitwarden exports")
        apple_entries = ApplePasswordsCSVParser.parse_file(apple_csv)
        bitwarden_entries = BitwardenCSVParser.parse_file(bitwarden_csv)

        apple_map = {entry.get_dedup_key(): entry for entry in apple_entries}
        bitwarden_map = {entry.get_dedup_key(): entry for entry in bitwarden_entries}

        apple_keys = set(apple_map.keys())
        bitwarden_keys = set(bitwarden_map.keys())

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
            "Comparison complete: %s in Apple only, %s in Bitwarden only, %s in both, %s conflicts",
            len(only_in_apple),
            len(only_in_bitwarden),
            len(in_both),
            len(conflicts),
        )

        return {
            "in_apple_only": only_in_apple,
            "in_bitwarden_only": only_in_bitwarden,
            "in_both": in_both,
            "conflicts": conflicts,
        }

    def _build_password_sync_plan(
        self,
        apple_map: dict,
        provider_map: dict,
        mappings: dict,
    ) -> dict:
        """
        Build a sync plan by comparing current Apple/Provider state with last synced state.

        Args:
            apple_map: dict[dedup_key] -> PasswordEntry from current Apple CSV
            provider_map: dict[dedup_key] -> PasswordEntry from current Provider
            mappings: dict[dedup_key] -> mapping record from database

        Returns:
            Dictionary with keys: create_apple, create_provider, update_apple,
            update_provider, delete_apple, delete_provider, unchanged, conflicts
        """
        plan = {
            "create_apple": [],
            "create_provider": [],
            "update_apple": [],
            "update_provider": [],
            "delete_apple": [],
            "delete_provider": [],
            "unchanged": [],
            "conflicts": [],
        }

        all_keys = set(apple_map.keys()) | set(provider_map.keys()) | set(mappings.keys())

        logger.debug(f"Sync plan: {len(apple_map)} apple entries, {len(provider_map)} provider entries, {len(mappings)} mappings")

        for key in all_keys:
            apple = apple_map.get(key)
            provider = provider_map.get(key)
            mapping = mappings.get(key)

            # Case 1: Entry exists in mapping (previously synced)
            if mapping:
                apple_exists = apple is not None
                provider_exists = provider is not None

                # Sub-case 1a: Both still exist
                if apple_exists and provider_exists:
                    apple_hash = apple.get_password_hash()
                    provider_hash = provider.get_password_hash()
                    last_apple_hash = mapping.get("last_apple_hash")
                    last_provider_hash = mapping.get("last_provider_hash")

                    apple_changed = apple_hash != last_apple_hash
                    provider_changed = provider_hash != last_provider_hash

                    if apple_changed or provider_changed:
                        logger.debug(
                            f"Change detected for '{key[0]}': "
                            f"apple_hash={apple_hash[:8]}..., last_apple_hash={last_apple_hash[:8] if last_apple_hash else 'None'}..., "
                            f"apple_changed={apple_changed}, provider_changed={provider_changed}"
                        )

                    if apple_changed and provider_changed:
                        # CONFLICT: Both modified
                        plan["conflicts"].append((key, apple, provider, mapping))
                    elif apple_changed:
                        # Apple modified → update provider
                        plan["update_provider"].append((key, apple, provider, mapping))
                    elif provider_changed:
                        # Provider modified → update Apple
                        plan["update_apple"].append((key, provider, apple, mapping))
                    else:
                        # No changes
                        plan["unchanged"].append(key)

                # Sub-case 1b: Only Apple exists (provider deleted)
                elif apple_exists and not provider_exists:
                    # Check if Apple was modified since last sync
                    # If modified → conflict (re-create in provider)
                    # If unchanged → delete from Apple
                    logger.debug(
                        f"Deletion conflict detected: '{key[0]}' exists in Apple but not in provider. "
                        f"Mapping provider_id: {mapping.get('provider_id')}"
                    )
                    plan["conflicts"].append((key, apple, None, mapping))

                # Sub-case 1c: Only Provider exists (Apple deleted)
                elif not apple_exists and provider_exists:
                    # Check if Provider was modified since last sync
                    # If modified → conflict (re-create in Apple)
                    # If unchanged → delete from provider
                    logger.debug(
                        f"Deletion conflict detected: '{key[0]}' exists in provider but not in Apple"
                    )
                    plan["conflicts"].append((key, None, provider, mapping))

                # Sub-case 1d: Both deleted - will be handled by cleanup

            # Case 2: No mapping (new entry on one or both sides)
            else:
                if apple and provider:
                    # New on both sides
                    if apple.get_password_hash() == provider.get_password_hash():
                        plan["unchanged"].append(key)
                    else:
                        # Different passwords - conflict
                        plan["conflicts"].append((key, apple, provider, None))
                elif apple:
                    # New in Apple → create in provider
                    plan["create_provider"].append(apple)
                elif provider:
                    # New in provider → create in Apple
                    plan["create_apple"].append(provider)

        logger.info(
            f"Sync plan result: create_provider={len(plan['create_provider'])}, "
            f"update_provider={len(plan['update_provider'])}, "
            f"delete_provider={len(plan['delete_provider'])}, "
            f"create_apple={len(plan['create_apple'])}, "
            f"update_apple={len(plan['update_apple'])}, "
            f"delete_apple={len(plan['delete_apple'])}, "
            f"conflicts={len(plan['conflicts'])}, "
            f"unchanged={len(plan['unchanged'])}"
        )
        return plan

    async def _resolve_conflict(
        self, apple: PasswordEntry | None, provider: PasswordEntry | None, mapping: dict
    ) -> str:
        """
        Resolve conflicts based on last modified timestamps.

        Args:
            apple: Apple password entry (None if deleted)
            provider: Provider password entry (None if deleted)
            mapping: Mapping record from database

        Returns:
            Action to take: 'update_apple', 'update_provider', 'delete_apple',
            'delete_provider', 'create_apple', 'create_provider', 'unchanged'
        """
        last_sync = mapping["last_sync_timestamp"]

        # Get timestamps from database entries
        apple_db = None
        provider_db = None

        if apple:
            apple_db = await self.db.get_entry_by_key(
                title=apple.title, url=apple.url, username=apple.username
            )
        if provider:
            provider_db = await self.db.get_entry_by_key(
                title=provider.title, url=provider.url, username=provider.username
            )

        apple_modified = apple_db["updated_at"] if apple_db else 0
        provider_modified = provider_db["updated_at"] if provider_db else 0

        # Modified vs deleted conflict
        if apple and not provider:
            # Modified in Apple, deleted in provider
            if apple_modified > last_sync:
                return "create_provider"  # Apple modified after deletion
            return "delete_apple"  # Respect provider deletion

        if provider and not apple:
            # Modified in provider, deleted in Apple
            # If no local DB entry exists for this password, it was likely created
            # in the provider and never existed in Apple - don't delete it
            if provider_db is None:
                logger.info(
                    f"Password '{provider.title}' has no local DB entry - "
                    "assuming it originated from provider, will create in Apple"
                )
                return "create_apple"
            if provider_modified > last_sync:
                return "create_apple"  # Provider modified after deletion
            return "delete_provider"  # Respect Apple deletion

        # Both modified: last-write-wins
        if apple and provider:
            if apple_modified > provider_modified:
                return "update_provider"
            return "update_apple"

        return "unchanged"

    async def sync(
        self,
        *,
        apple_csv_path: Path | None,
        provider: PasswordProviderBase,
        output_apple_csv: Path | None = None,
        simulate: bool = False,
        run_push: bool = True,
        run_pull: bool = True,
        bulk_push: bool = False,
    ) -> dict:
        """
        Run push and/or pull phases with optional simulation.

        Args:
            apple_csv_path: Path to Apple Passwords CSV export
            provider: Password provider instance (VaultwardenProvider, NextcloudPasswordsProvider, etc.)
            output_apple_csv: Output path for new Apple CSV (for pull phase)
            simulate: If True, don't actually make changes
            run_push: Whether to run push phase (Apple → Provider)
            run_pull: Whether to run pull phase (Provider → Apple)
            bulk_push: Whether to use bulk import for push phase (if supported)

        Returns:
            Dictionary with sync statistics
        """

        if not run_push and not run_pull:
            raise ValueError("At least one sync phase must be enabled")
        if run_push and apple_csv_path is None:
            raise ValueError("Apple CSV path is required for push phase")

        logger.info(
            "Starting password sync (push=%s, pull=%s, simulate=%s)",
            run_push,
            run_pull,
            simulate,
        )

        start_time = time.time()
        push_stats = None
        pull_stats = None

        if run_push and apple_csv_path is not None:
            push_stats = await self._push_phase(
                apple_csv_path=apple_csv_path,
                provider=provider,
                simulate=simulate,
                bulk_push=bulk_push,
            )

        if run_pull:
            pull_stats = await self._pull_phase(
                provider=provider,
                output_apple_csv=output_apple_csv,
                simulate=simulate,
            )

        total_time = time.time() - start_time
        logger.info("Password sync finished in %.1fs", total_time)

        return {
            "push": push_stats,
            "pull": pull_stats,
            "total_time": total_time,
            "simulate": simulate,
            "run_push": run_push,
            "run_pull": run_pull,
        }

    async def _push_phase(
        self,
        *,
        apple_csv_path: Path,
        provider: PasswordProviderBase,
        simulate: bool,
        bulk_push: bool,
    ) -> dict:
        logger.info("Running push phase (simulate=%s)", simulate)
        # Only import CSV to database during actual sync, not simulation
        if not simulate:
            import_stats = await self.import_apple_csv(apple_csv_path)
        else:
            import_stats = {"new": 0, "updated": 0, "duplicates": 0, "unchanged": 0, "errors": 0}

        apple_db_entries = await self.db.get_all_entries(source="apple")
        apple_entries = ApplePasswordsCSVParser.parse_file(apple_csv_path)
        apple_map = {entry.get_dedup_key(): entry for entry in apple_entries}

        # Deletion detection: Get mappings and provider entries
        provider_type = provider.__class__.__name__.replace("Provider", "").lower()
        mappings = await self.db.get_all_password_mappings(provider_type=provider_type)
        mapping_dict = {
            (
                m["title"].lower().strip(),
                m["url"].lower().strip() if m["url"] else None,
                m["username"].lower().strip(),
            ): m
            for m in mappings
        }

        # Get current provider entries for deletion detection
        provider_map = {}
        try:
            provider_passwords_dict = await provider.list_passwords()
            # Convert to PasswordEntry objects
            for pwd_dict in provider_passwords_dict:
                entry = PasswordEntry(
                    title=pwd_dict.get("label") or pwd_dict.get("title", ""),
                    username=pwd_dict.get("username", ""),
                    password=pwd_dict.get("password", ""),
                    url=pwd_dict.get("url"),
                    notes=pwd_dict.get("notes"),
                    otp_auth=pwd_dict.get("otp_auth"),
                    folder=pwd_dict.get("folder_label") or pwd_dict.get("folder"),
                    provider_id=pwd_dict.get("id"),
                )
                provider_map[entry.get_dedup_key()] = entry
        except Exception as exc:
            logger.warning("Failed to fetch provider entries for deletion detection: %s", exc)

        # Build sync plan to detect deletions
        sync_plan = self._build_password_sync_plan(
            apple_map=apple_map, provider_map=provider_map, mappings=mapping_dict
        )

        # Track entries with their actions for UI display
        entries_with_actions: list[dict] = []

        # Execute deletions (Apple deleted → delete from provider)
        deleted_count = 0
        updated_count = 0
        for key, provider_entry, mapping in sync_plan["delete_provider"]:
            if not simulate:
                provider_id = mapping.get("provider_id") if mapping else (provider_entry.provider_id if provider_entry else None)
                if provider_id:
                    try:
                        success = await provider.delete_password(provider_id)
                        if success:
                            await self.db.delete_password_mapping(
                                title=key[0], url=key[1], username=key[2], provider_type=provider_type
                            )
                            deleted_count += 1
                            entries_with_actions.append({
                                "title": key[0],
                                "username": key[2] or "",
                                "action": "delete",
                            })
                            logger.info(f"Deleted from provider: {key[0]}")
                        else:
                            logger.warning(f"Failed to delete from provider: {key[0]}")
                    except Exception as e:
                        logger.error(f"Error deleting {key[0]} from provider: {e}")
                else:
                    logger.warning(f"No provider_id for {key[0]}, cannot delete")
            else:
                deleted_count += 1
                entries_with_actions.append({
                    "title": key[0],
                    "username": key[2] or "",
                    "action": "delete",
                })

        # Resolve conflicts
        for key, apple, provider_entry, mapping in sync_plan["conflicts"]:
            action = await self._resolve_conflict(apple, provider_entry, mapping)
            logger.info(f"Conflict for {key[0]}: resolved as {action}")

            if action == "delete_provider":
                if not simulate:
                    try:
                        provider_id = mapping.get("provider_id") if mapping else (provider_entry.provider_id if provider_entry else None)
                        logger.debug(f"Delete provider: mapping provider_id={mapping.get('provider_id') if mapping else None}, entry provider_id={provider_entry.provider_id if provider_entry else None}, final={provider_id}")
                        if provider_id:
                            success = await provider.delete_password(provider_id)
                            if success:
                                await self.db.delete_password_mapping(
                                    title=key[0], url=key[1], username=key[2], provider_type=provider_type
                                )
                                entries_with_actions.append({
                                    "title": key[0],
                                    "username": key[2] or "",
                                    "action": "delete",
                                })
                                logger.info(f"Conflict resolved: deleted {key[0]} from provider")
                        else:
                            logger.warning(f"Cannot delete {key[0]} from provider: no provider_id in mapping")
                    except Exception as e:
                        logger.error(f"Error deleting {key[0]} from provider: {e}")
                # Count deletion in both simulation and non-simulation modes
                provider_id = mapping.get("provider_id") if mapping else (provider_entry.provider_id if provider_entry else None)
                if provider_id:
                    deleted_count += 1
                    if simulate:
                        entries_with_actions.append({
                            "title": key[0],
                            "username": key[2] or "",
                            "action": "delete",
                        })
                else:
                    logger.warning(f"[Simulation] Cannot delete {key[0]}: no provider_id")

            elif action == "create_provider" and not simulate:
                try:
                    if apple:
                        await provider.create_password(apple)
                        logger.info(f"Conflict resolved: re-created {key[0]} in provider")
                except Exception as e:
                    logger.error(f"Error creating {key[0]} in provider: {e}")

            elif action == "update_provider":
                if not simulate:
                    provider_id = mapping.get("provider_id") if mapping else (provider_entry.provider_id if provider_entry else None)
                    if provider_id and apple:
                        try:
                            success = await provider.update_password(provider_id, apple)
                            if success:
                                await self.db.upsert_password_mapping(
                                    title=apple.title,
                                    username=apple.username,
                                    provider_id=provider_id,
                                    provider_type=provider_type,
                                    last_apple_hash=apple.get_password_hash(),
                                    last_provider_hash=apple.get_password_hash(),
                                    url=apple.url,
                                )
                                updated_count += 1
                                entries_with_actions.append({
                                    "title": apple.title,
                                    "username": apple.username or "",
                                    "action": "update",
                                })
                                logger.info(f"Conflict resolved: updated {key[0]} in provider")
                            else:
                                logger.warning(f"Conflict resolution failed: could not update {key[0]} in provider")
                        except Exception as e:
                            logger.error(f"Error updating {key[0]} in provider: {e}")
                    else:
                        logger.warning(f"Cannot update {key[0]} in provider: no provider_id or apple entry")
                else:
                    updated_count += 1
                    if apple:
                        entries_with_actions.append({
                            "title": apple.title,
                            "username": apple.username or "",
                            "action": "update",
                        })

            elif action == "delete_apple" and not simulate:
                try:
                    # Can't delete from Apple automatically - just remove mapping
                    await self.db.delete_password_mapping(
                        title=key[0], url=key[1], username=key[2], provider_type=provider_type
                    )
                    logger.warning(f"Conflict resolved: {key[0]} should be deleted from Apple manually")
                except Exception as e:
                    logger.error(f"Error removing mapping for {key[0]}: {e}")

            elif action == "create_apple" and not simulate:
                logger.info(f"Conflict resolved: {key[0]} will be pulled to Apple in pull phase")

        logger.info(f"Push phase deletions: {deleted_count} entries deleted from provider")

        # Execute updates (Apple password changed → update provider)
        for key, apple_entry, provider_entry, mapping in sync_plan["update_provider"]:
            if not simulate:
                provider_id = mapping.get("provider_id") if mapping else (provider_entry.provider_id if provider_entry else None)
                if provider_id:
                    try:
                        success = await provider.update_password(provider_id, apple_entry)
                        if success:
                            # Update the mapping with new hashes
                            await self.db.upsert_password_mapping(
                                title=apple_entry.title,
                                username=apple_entry.username,
                                provider_id=provider_id,
                                provider_type=provider_type,
                                last_apple_hash=apple_entry.get_password_hash(),
                                last_provider_hash=apple_entry.get_password_hash(),
                                url=apple_entry.url,
                            )
                            updated_count += 1
                            entries_with_actions.append({
                                "title": apple_entry.title,
                                "username": apple_entry.username or "",
                                "action": "update",
                            })
                            logger.info(f"Updated password in provider: {apple_entry.title}")
                        else:
                            logger.warning(f"Failed to update password in provider: {apple_entry.title}")
                    except Exception as e:
                        logger.error(f"Error updating {apple_entry.title} in provider: {e}")
                else:
                    logger.warning(f"No provider_id for {key[0]}, cannot update")
            else:
                updated_count += 1
                entries_with_actions.append({
                    "title": apple_entry.title,
                    "username": apple_entry.username or "",
                    "action": "update",
                })

        logger.info(f"Push phase updates: {updated_count} entries updated in provider")

        entries_by_key: dict[tuple[str, str], PasswordEntry] = {}
        for db_entry in apple_db_entries:
            combined_key = (
                db_entry["title"].lower().strip(),
                db_entry["username"].lower().strip(),
            )

            key = (
                db_entry["title"].lower().strip(),
                db_entry["url"].lower().strip() if db_entry["url"] else None,
                db_entry["username"].lower().strip(),
            )
            entry = apple_map.get(key)
            if not entry:
                continue

            base_entry = entries_by_key.get(combined_key)
            if not base_entry:
                base_entry = entry
                entries_by_key[combined_key] = base_entry
            else:
                for url in entry.get_all_urls():
                    base_entry.add_url(url)
                if entry.notes:
                    base_entry.notes = entry.notes
                if entry.otp_auth:
                    base_entry.otp_auth = entry.otp_auth

            if db_entry.get("folder"):
                base_entry.folder = db_entry["folder"]

        entries_to_push = list(entries_by_key.values())

        folders_created = await self._ensure_provider_folders(
            entries_to_push,
            provider,
            create_missing=not simulate,
        )

        # Track which entries will actually be created (for UI display)
        entries_that_will_be_created = []

        # Fetch existing provider entries to determine what's new (used by simulate and bulk modes)
        existing_keys: set[tuple[str, str | None, str]] = set()
        if simulate or bulk_push:
            try:
                existing_entries = await provider.list_passwords()
                existing_keys = {
                    (
                        (entry.get("label") or entry.get("title") or "").lower().strip(),
                        (entry.get("url") or "").lower().strip() if entry.get("url") else None,
                        (entry.get("username") or "").lower().strip(),
                    )
                    for entry in existing_entries
                }
            except Exception as exc:
                logger.warning("Failed to fetch existing entries: %s", exc)

        # Determine which entries will be created (for simulate and bulk modes)
        if simulate or bulk_push:
            for entry in entries_to_push:
                entry_key = (
                    entry.title.lower().strip(),
                    entry.url.lower().strip() if entry.url else None,
                    entry.username.lower().strip(),
                )
                if entry_key not in existing_keys:
                    entries_that_will_be_created.append(entry)

        if simulate:
            push_stats = {
                "created": len(entries_that_will_be_created),
                "skipped": len(entries_to_push) - len(entries_that_will_be_created),
                "failed": 0,
                "errors": [],
            }
            logger.info(
                "Simulation: %s new, %s updates, %s already exist",
                len(entries_that_will_be_created),
                updated_count,
                len(entries_to_push) - len(entries_that_will_be_created),
            )
        elif bulk_push:
            push_stats = await provider.bulk_import(entries_that_will_be_created)
            # Note: we're only pushing entries that don't already exist
        else:
            # Create entries individually
            created = 0
            failed = 0
            errors = []
            skipped = 0
            backoff_seconds = 1.0
            max_backoff = 30.0
            min_backoff = 0.5
            success_streak = 0
            provider_name = provider.__class__.__name__.replace("Provider", "")
            max_retries = 5
            total_entries = len(entries_to_push)

            existing_provider_keys: set[tuple[str, str | None, str]] = set()
            if not simulate:
                try:
                    existing_entries = await provider.list_passwords()
                    existing_provider_keys = {
                        (
                            (entry.get("label") or entry.get("title") or "").lower().strip(),
                            (entry.get("url") or "").lower().strip() if entry.get("url") else None,
                            (entry.get("username") or "").lower().strip(),
                        )
                        for entry in existing_entries
                    }
                    if existing_provider_keys:
                        logger.info(
                            "Provider already has %s password(s); skipping duplicates during push",
                            len(existing_provider_keys),
                        )
                except Exception as exc:  # pragma: no cover - remote call
                    logger.warning("Failed to enumerate provider entries: %s", exc)

            for index, entry in enumerate(entries_to_push, start=1):
                logger.info("Syncing password %s/%s: %s", index, total_entries, entry.title)

                entry_key = (
                    entry.title.lower().strip(),
                    entry.url.lower().strip() if entry.url else None,
                    entry.username.lower().strip(),
                )
                if not simulate and entry_key in existing_provider_keys:
                    skipped += 1
                    logger.info("Skipping password already present on provider: %s", entry.title)
                    continue
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        await provider.create_password(entry)
                        created += 1
                        entries_that_will_be_created.append(entry)
                        success_streak += 1
                        if not simulate:
                            existing_provider_keys.add(entry_key)
                        logger.info("✓ Synced password: %s", entry.title)
                        if backoff_seconds > min_backoff and success_streak >= 3:
                            backoff_seconds = max(min_backoff, backoff_seconds / 2)
                            logger.info(
                                "Adaptive backoff recovered (%.1fs) after %s successful inserts",
                                backoff_seconds,
                                success_streak,
                            )
                            success_streak = 0
                        break
                    except Exception as e:  # pragma: no cover - network dependent
                        is_transient, status_code = self._is_transient_provider_error(e)

                        if is_transient:
                            if attempt <= max_retries:
                                logger.warning(
                                    "%s API throttled '%s' (status=%s). Backing off for %.1fs before retry %s/%s",
                                    provider_name,
                                    entry.title,
                                    status_code or "transport",
                                    backoff_seconds,
                                    attempt,
                                    max_retries,
                                )
                                await asyncio.sleep(backoff_seconds)
                                backoff_seconds = min(backoff_seconds * 2, max_backoff)
                                success_streak = 0
                                continue
                            logger.error(
                                "%s API still failing for '%s' after %s retries (status=%s)",
                                provider_name,
                                entry.title,
                                max_retries,
                                status_code or "transport",
                            )

                        failed += 1
                        errors.append(str(e))
                        logger.error(f"Failed to create password {entry.title}: {e}")
                        break

            push_stats = {
                "created": created,
                "skipped": skipped,
                "failed": failed,
                "errors": errors,
            }

        # Add created entries to the action list
        for entry in entries_that_will_be_created:
            entries_with_actions.append({
                "title": entry.title,
                "username": entry.username or "",
                "action": "create",
            })

        # Update mappings for successfully created entries
        # We need to fetch back from provider to get cipher IDs
        if not simulate and entries_that_will_be_created:
            logger.info("Fetching passwords from provider to update mappings with cipher IDs")
            try:
                provider_passwords_dict = await provider.list_passwords()
                provider_entries_by_key = {}
                for pwd_dict in provider_passwords_dict:
                    entry = PasswordEntry(
                        title=pwd_dict.get("label") or pwd_dict.get("title", ""),
                        username=pwd_dict.get("username", ""),
                        password=pwd_dict.get("password", ""),
                        url=pwd_dict.get("url"),
                        provider_id=pwd_dict.get("id"),
                    )
                    provider_entries_by_key[entry.get_dedup_key()] = entry

                for entry in entries_that_will_be_created:
                    # Look up the provider entry to get the cipher ID
                    provider_entry = provider_entries_by_key.get(entry.get_dedup_key())
                    provider_id = provider_entry.provider_id if provider_entry else None

                    if not provider_id:
                        logger.warning(
                            f"Could not find provider entry for '{entry.title}' after creation. "
                            f"Key: {entry.get_dedup_key()}. "
                            f"Available keys: {list(provider_entries_by_key.keys())[:5]}..."
                        )

                    try:
                        await self.db.upsert_password_mapping(
                            title=entry.title,
                            username=entry.username,
                            provider_id=provider_id,
                            provider_type=provider_type,
                            last_apple_hash=entry.get_password_hash(),
                            last_provider_hash=entry.get_password_hash(),
                            url=entry.url,
                        )
                        if provider_id:
                            logger.debug(f"Updated mapping for {entry.title} with cipher ID {provider_id}")
                        else:
                            logger.warning(f"Created mapping for {entry.title} without provider_id")
                    except Exception as e:
                        logger.warning(f"Failed to update mapping for {entry.title}: {e}")
            except Exception as e:
                logger.error(f"Failed to fetch provider passwords for mapping update: {e}")

        return {
            "import": import_stats,
            "queued": len(entries_to_push),
            "created": push_stats.get("created") if isinstance(push_stats, dict) else push_stats,
            "updated": updated_count,
            "skipped": push_stats.get("skipped") if isinstance(push_stats, dict) else 0,
            "failed": push_stats.get("failed") if isinstance(push_stats, dict) else 0,
            "errors": push_stats.get("errors") if isinstance(push_stats, dict) else [],
            "folders_created": folders_created,
            "simulate": simulate,
            "entries": entries_with_actions,
            "deleted": deleted_count,
        }

    @staticmethod
    def _is_transient_provider_error(exc: Exception) -> tuple[bool, int | None]:
        """Detect whether a provider error is likely transient (rate limiting, 5xx, etc.)."""

        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code if exc.response else None
            return status_code in PasswordsSyncEngine._TRANSIENT_STATUS_CODES, status_code

        if isinstance(exc, httpx.TransportError):
            return True, None

        response = getattr(exc, "response", None)
        if response is not None:
            status_code = getattr(response, "status_code", None)
            return status_code in PasswordsSyncEngine._TRANSIENT_STATUS_CODES, status_code

        return False, None

    async def _pull_phase(
        self,
        *,
        provider: PasswordProviderBase,
        output_apple_csv: Path | None,
        simulate: bool,
    ) -> dict:
        logger.info("Running pull phase (simulate=%s)", simulate)

        # Get passwords from provider as dictionaries
        provider_passwords_dict = await provider.list_passwords()

        # Convert to PasswordEntry objects
        provider_entries = []
        for pwd_dict in provider_passwords_dict:
            entry = PasswordEntry(
                title=pwd_dict.get("label") or pwd_dict.get("title", ""),
                username=pwd_dict.get("username", ""),
                password=pwd_dict.get("password", ""),
                url=pwd_dict.get("url"),
                notes=pwd_dict.get("notes"),
                otp_auth=pwd_dict.get("otp_auth"),
                folder=pwd_dict.get("folder_label") or pwd_dict.get("folder"),
                provider_id=pwd_dict.get("id"),
            )
            provider_entries.append(entry)

        if not simulate:
            for entry in provider_entries:
                try:
                    await self.db.upsert_entry(
                        title=entry.title,
                        username=entry.username,
                        password_hash=entry.get_password_hash(),
                        url=entry.url,
                        notes=entry.notes,
                        otp_auth=entry.otp_auth,
                        folder=entry.folder,
                        source="provider",
                    )
                except Exception as exc:  # pragma: no cover
                    logger.error("Failed to update DB with provider entry %s: %s", entry.title, exc)

        apple_db_entries = await self.db.get_all_entries(source="apple")
        apple_keys = set()
        apple_map = {}
        for db_entry in apple_db_entries:
            key = (
                db_entry["title"].lower().strip(),
                db_entry["url"].lower().strip() if db_entry["url"] else None,
                db_entry["username"].lower().strip(),
            )
            apple_keys.add(key)
            # Convert db_entry to PasswordEntry for sync plan
            apple_entry = PasswordEntry(
                title=db_entry["title"],
                username=db_entry["username"],
                password="",  # Don't have plaintext, use empty
                url=db_entry.get("url"),
                notes=db_entry.get("notes"),
                otp_auth=db_entry.get("otp_auth"),
                folder=db_entry.get("folder"),
            )
            apple_map[key] = apple_entry

        # Deletion detection: Get mappings and build sync plan
        provider_type = provider.__class__.__name__.replace("Provider", "").lower()
        mappings = await self.db.get_all_password_mappings(provider_type=provider_type)
        mapping_dict = {
            (
                m["title"].lower().strip(),
                m["url"].lower().strip() if m["url"] else None,
                m["username"].lower().strip(),
            ): m
            for m in mappings
        }

        provider_map = {entry.get_dedup_key(): entry for entry in provider_entries}

        # Build sync plan to detect deletions
        sync_plan = self._build_password_sync_plan(
            apple_map=apple_map, provider_map=provider_map, mappings=mapping_dict
        )

        # Track entries with their actions for UI display
        entries_with_actions: list[dict] = []

        # Handle deletions (Provider deleted → delete from Apple)
        # Note: We can't automatically delete from Apple Passwords (CSV-only interface)
        # So we just log warnings for user to manually delete
        deleted_count = 0
        for key, apple_entry, mapping in sync_plan["delete_apple"]:
            if not simulate:
                # Delete the mapping since password was deleted from provider
                try:
                    await self.db.delete_password_mapping(
                        title=key[0], url=key[1], username=key[2], provider_type=provider_type
                    )
                    deleted_count += 1
                    entries_with_actions.append({
                        "title": key[0],
                        "username": key[2] or "",
                        "action": "delete",
                    })
                    logger.warning(
                        f"Password '{key[0]}' deleted from provider. "
                        f"Please manually delete from Apple Passwords: {key[0]} ({key[2]})"
                    )
                except Exception as e:
                    logger.error(f"Error deleting mapping for {key[0]}: {e}")
            else:
                deleted_count += 1
                entries_with_actions.append({
                    "title": key[0],
                    "username": key[2] or "",
                    "action": "delete",
                })
                logger.info(f"[Simulation] Would mark for deletion in Apple: {key[0]}")

        # Handle conflicts in pull phase (mostly just logging)
        for key, apple, provider_entry, mapping in sync_plan["conflicts"]:
            action = await self._resolve_conflict(apple, provider_entry, mapping)
            logger.info(f"Pull phase conflict for {key[0]}: {action}")

            if action == "delete_apple":
                if not simulate:
                    # Log warning for manual deletion
                    await self.db.delete_password_mapping(
                        title=key[0], url=key[1], username=key[2], provider_type=provider_type
                    )
                    entries_with_actions.append({
                        "title": key[0],
                        "username": key[2] or "",
                        "action": "delete",
                    })
                    logger.warning(
                        f"Conflict: '{key[0]}' should be manually deleted from Apple Passwords"
                    )
                else:
                    entries_with_actions.append({
                        "title": key[0],
                        "username": key[2] or "",
                        "action": "delete",
                    })
                deleted_count += 1

        logger.info(f"Pull phase deletions: {deleted_count} entries need manual deletion from Apple")

        new_entries = [entry for entry in provider_entries if entry.get_dedup_key() not in apple_keys]

        output_path: Path | None = None
        if new_entries and not simulate:
            if output_apple_csv is None:
                raise ValueError("Output path required when exporting Apple CSV")
            ApplePasswordsCSVParser.write_file(new_entries, output_apple_csv)
            await self.db.record_sync(
                sync_type="provider_pull",
                file_path=str(output_apple_csv),
                entry_count=len(new_entries),
            )
            output_path = output_apple_csv
            logger.info("Generated Apple CSV with %s new entries", len(new_entries))
        elif not new_entries:
            logger.info("No new entries from provider")

        # Add new entries to the action list
        for entry in new_entries:
            entries_with_actions.append({
                "title": entry.title,
                "username": entry.username or "",
                "action": "create",
            })

        # Update mappings for new entries
        if not simulate and new_entries:
            for entry in new_entries:
                provider_id = entry.provider_id if hasattr(entry, "provider_id") and entry.provider_id else None
                try:
                    await self.db.upsert_password_mapping(
                        title=entry.title,
                        username=entry.username,
                        provider_id=provider_id,
                        provider_type=provider_type,
                        last_apple_hash=entry.get_password_hash(),
                        last_provider_hash=entry.get_password_hash(),
                        url=entry.url,
                    )
                except Exception as e:
                    logger.warning(f"Failed to update mapping for {entry.title}: {e}")

        return {
            "new_entries": len(new_entries),
            "download_path": str(output_path) if output_path else None,
            "simulate": simulate,
            "entries": entries_with_actions,
            "deleted": deleted_count,
        }

    async def _ensure_provider_folders(
        self,
        entries: list[PasswordEntry],
        provider: PasswordProviderBase,
        create_missing: bool = True,
    ) -> int:
        """Ensure folders referenced in entries exist in the password provider."""

        folders_needed = {entry.folder for entry in entries if entry.folder}
        if not folders_needed:
            return 0

        logger.info(
            "Ensuring provider folders for %s tagged entrie(s)", len(folders_needed)
        )

        existing = await provider.list_folders()
        # Handle both "name" and "label" keys for folder names
        folder_names = {
            folder.get("name") or folder.get("label") for folder in existing
        }
        created = 0

        if create_missing:
            for folder_name in sorted(folders_needed):
                if folder_name in folder_names:
                    continue
                logger.info("Creating provider folder: %s", folder_name)
                await provider.create_folder(folder_name)
                created += 1

        return created

    def _merge_duplicate_entries(self, entries: list[PasswordEntry]) -> list[PasswordEntry]:
        """Merge entries with same title/username into a single entry with multi-URL support."""

        merged: dict[tuple[str, str], PasswordEntry] = {}

        for entry in entries:
            key = (entry.title.lower().strip(), entry.username.lower().strip())

            if key not in merged:
                merged[key] = entry
                continue

            base = merged[key]

            for url in entry.get_all_urls():
                base.add_url(url)

            if not base.notes and entry.notes:
                base.notes = entry.notes
            if not base.otp_auth and entry.otp_auth:
                base.otp_auth = entry.otp_auth
            if not base.folder and entry.folder:
                base.folder = entry.folder

        return list(merged.values())

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
    _TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
