"""Password synchronization engine for Apple Passwords and password providers."""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..sources.passwords.apple_csv import ApplePasswordsCSVParser
from ..sources.passwords.bitwarden_csv import BitwardenCSVParser
from ..sources.passwords.diagnostics import InvalidInputError
from ..sources.passwords.models import PasswordEntry
from ..sources.passwords.providers.base import PasswordProviderBase
from ..sources.passwords.providers.vaultwarden import VaultwardenProvider
from ..sources.passwords.retry import retry_addition
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
                existing = await self.db.get_entry_by_key(entry.title, entry.url, entry.username)

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
                            notes=None,
                            otp_auth=None,
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
                            notes=None,
                            otp_auth=None,
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
                        notes=None,
                        otp_auth=None,
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
                existing = await self.db.get_entry_by_key(entry.title, entry.url, entry.username)

                password_hash = entry.get_password_hash()

                if existing:
                    if existing["password_hash"] != password_hash:
                        await self.db.upsert_entry(
                            title=entry.title,
                            username=entry.username,
                            password_hash=password_hash,
                            url=entry.url,
                            notes=None,
                            otp_auth=None,
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
                            notes=None,
                            otp_auth=None,
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
                        notes=None,
                        otp_auth=None,
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

        logger.info(f"Bitwarden import complete: {stats['new']} new, {stats['updated']} updated")

        return stats

    async def export_apple_csv(self, output_path: Path, bitwarden_csv_path: Path) -> int:
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

    async def compare_sources(self, apple_csv: Path, bitwarden_csv: Path) -> dict:
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

    @staticmethod
    def _otp(value):
        """Normalise a TOTP value to (secret, algorithm, digits, period).

        A bare secret carries no parameters (older Nextcloud entries, Bitwarden
        secrets typed by hand), so those come back as None and match any value.
        """
        if not value:
            return None
        if not value.startswith("otpauth://"):
            return (value.replace(" ", "").upper(), None, None, None)
        parsed = urlparse(value)
        fields = parse_qs(parsed.query)
        if parsed.netloc != "totp" or not fields.get("secret"):
            return value
        return (
            fields["secret"][0].replace(" ", "").upper(),
            fields.get("algorithm", ["SHA1"])[0].upper(),
            fields.get("digits", ["6"])[0],
            fields.get("period", ["30"])[0],
        )

    @staticmethod
    def _same_otp(first, second) -> bool:
        a, b = PasswordsSyncEngine._otp(first), PasswordsSyncEngine._otp(second)
        if not isinstance(a, tuple) or not isinstance(b, tuple):
            return a == b
        return all(x is None or y is None or x == y for x, y in zip(a, b, strict=True)) and a[0] == b[0]

    @staticmethod
    def _notes(value: str | None) -> str:
        # Apple and the providers differ in line endings and surrounding whitespace.
        return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    @staticmethod
    def _content(entry: PasswordEntry) -> tuple:
        # Metadata is compared in memory only; OTP and notes must not enter the DB.
        return (
            entry.password,
            PasswordsSyncEngine._notes(entry.notes),
            tuple(sorted({entry.normalize_url(url) for url in entry.get_all_urls()})),
        )

    @classmethod
    def _same(cls, apple: PasswordEntry, remote: PasswordEntry) -> bool:
        return (
            apple.title.strip() == remote.title.strip()
            and apple.username.strip() == remote.username.strip()
            and cls._content(apple) == cls._content(remote)
            and cls._same_otp(apple.otp_auth, remote.otp_auth)
        )

    @classmethod
    def _fingerprint(cls, entry: PasswordEntry) -> str:
        """Hash of everything that syncs for one side, to tell which side changed."""
        data = [
            entry.title.strip(),
            entry.username.strip(),
            *cls._content(entry),
            cls._otp(entry.otp_auth),
        ]
        return hashlib.sha256(json.dumps(data).encode("utf-8")).hexdigest()

    @classmethod
    def _changed(cls, entry: PasswordEntry, mapping: dict, side: str) -> bool | None:
        """Whether one side changed since the last sync; None when nothing was recorded."""
        recorded = mapping.get(f"last_{side}_content")
        if recorded:
            return cls._fingerprint(entry) != recorded
        # Mappings saved before fingerprints existed only know the password.
        legacy = mapping.get(f"last_{side}_hash")
        if not legacy:
            return None
        return entry.get_password_hash() != legacy

    @classmethod
    def _merge_into_provider(cls, remote: PasswordEntry, apple: PasswordEntry) -> PasswordEntry:
        """The provider item with Apple's changes applied, keeping what Apple can't represent.

        Apple keeps only a website's host, so the provider's own URLs stay unless the
        websites actually differ; likewise for notes and verification codes.
        """
        merged = replace(
            remote,
            title=apple.title,
            username=apple.username,
            password=apple.password,
            extra_urls=list(remote.extra_urls),
        )
        if cls._notes(apple.notes) != cls._notes(remote.notes):
            merged.notes = apple.notes
        if cls._content(apple)[2] != cls._content(remote)[2]:
            merged.url = apple.url
            merged.extra_urls = list(apple.extra_urls)
        if not cls._same_otp(apple.otp_auth, remote.otp_auth):
            merged.otp_auth = apple.otp_auth
        return merged

    @staticmethod
    def _apple_sites(entry: PasswordEntry) -> set:
        # Apple keeps one login per website (host) and username, usernames compared
        # case-sensitively, and refuses to import a second one.
        username = entry.username.strip()
        return {(entry.normalize_url(url), username) for url in entry.get_all_urls()}

    def _plan(
        self,
        apple_entries: list[PasswordEntry],
        remote_entries: list[PasswordEntry],
        baseline: dict,
        *,
        review_changes: bool,
        review_deletions: bool,
    ) -> dict:
        """Decide what to add, update and delete on each side.

        A change or deletion on one side since the last sync is copied to the other
        side, unless the matching review option is on. Anything whose direction can't
        be known (changed on both sides, never synced but different, ambiguous) is held.
        """
        apple_map, remote_map = {}, {}
        duplicates = []
        for entries, side_map in ((apple_entries, apple_map), (remote_entries, remote_map)):
            for e in entries:
                key = e.get_dedup_key()
                if key in side_map:
                    duplicates.append(e)
                side_map[key] = e
        plan = {
            name: []
            for name in (
                "create_provider",
                "update_provider",
                "delete_provider",
                "export_apple",
                "replace_apple",
                "delete_apple",
                "unchanged",
                "reviews",
            )
        }
        handled: set = set()
        # Exported changes and the Apple logins they replace. Apple refuses to import
        # over a login it already has, so the old one must be deleted (or edited by hand).
        replacing: dict = {}

        def review(entry: PasswordEntry, reason: str) -> None:
            handled.add(entry.get_dedup_key())
            plan["reviews"].append(
                {
                    "title": entry.title,
                    "username": entry.username,
                    "action": "review",
                    "reason": reason,
                }
            )

        def export(entry: PasswordEntry, replaces: PasswordEntry | None = None) -> None:
            plan["export_apple"].append(entry)
            if replaces is not None:
                replacing[entry.get_dedup_key()] = replaces

        # Two logins with the same title, website and username on one side can't be paired.
        for e in duplicates:
            dup_key = e.get_dedup_key()
            for side_map in (apple_map, remote_map):
                if dup_key in side_map:
                    review(side_map[dup_key], "ambiguous_identity")

        # Logins under the same key on both sides.
        for key in apple_map.keys() & remote_map.keys():
            if key in handled:
                continue
            a, r = apple_map[key], remote_map[key]
            handled.add(key)
            if self._same(a, r):
                plan["unchanged"].append(key)
                continue
            m = baseline.get(key)
            if m is None:
                review(a, "no_baseline")
                continue
            ac, rc = self._changed(a, m, "apple"), self._changed(r, m, "provider")
            if ac is None or rc is None:
                review(a, "no_baseline")
            elif ac and rc:
                review(a, "changed_on_both_sides")
            elif ac:
                if review_changes:
                    review(a, "changed_in_apple")
                else:
                    plan["update_provider"].append((r, self._merge_into_provider(r, a)))
            elif rc:
                if review_changes:
                    review(r, "changed_in_provider")
                else:
                    export(r, replaces=a)
            else:
                # Synced before fingerprints were recorded, and only details differ.
                review(a, "metadata_difference")

        # Logins synced before whose key is gone from a side: renamed, moved or deleted.
        by_id = {e.provider_id: e for e in remote_entries if e.provider_id}
        apple_by_title: dict = {}
        apple_by_site: dict = {}
        for e in apple_entries:
            k = e.get_dedup_key()
            apple_by_title.setdefault((k[0], k[2]), []).append(e)
            for url in e.get_all_urls():
                apple_by_site.setdefault((e.normalize_url(url), k[2]), []).append(e)

        def unclaimed(e: PasswordEntry) -> bool:
            k = e.get_dedup_key()
            return k not in handled and k not in baseline

        for key, m in baseline.items():
            if key in handled:
                continue
            a, r = apple_map.get(key), remote_map.get(key)
            handled.add(key)
            r_moved = None
            if r is None:
                same_item = by_id.get(m.get("provider_id"))
                if same_item is not None and unclaimed(same_item):
                    r_moved = same_item
            a_moved = None
            if a is None:
                similar = {
                    e.get_dedup_key(): e
                    for e in apple_by_title.get((key[0], key[2]), [])
                    + apple_by_site.get((key[1], key[2]), [])
                    if unclaimed(e)
                }
                if len(similar) > 1:
                    for e in similar.values():
                        review(e, "possible_renamed_identity")
                    if r is not None or r_moved is not None:
                        review(r or r_moved, "possible_renamed_identity")
                    continue
                if similar:
                    a_moved = next(iter(similar.values()))
            for moved in (a_moved, r_moved):
                if moved is not None:
                    handled.add(moved.get_dedup_key())
            a_now, r_now = a or a_moved, r or r_moved
            if a_now is None and r_now is None:
                continue  # gone from both sides; the tracking is retired below
            ac = (a_moved is not None) or (a is not None and self._changed(a, m, "apple") is not False)
            rc = (r_moved is not None) or (r is not None and self._changed(r, m, "provider") is not False)
            if a_now is not None and r_now is not None:
                if ac and rc:
                    review(a_now, "changed_on_both_sides")
                    review(r_now, "changed_on_both_sides")
                elif ac:
                    if review_changes:
                        review(a_now, "renamed_in_apple" if a_moved else "changed_in_apple")
                    else:
                        plan["update_provider"].append((r_now, self._merge_into_provider(r_now, a_now)))
                elif rc:
                    if review_changes:
                        review(r_now, "renamed_in_provider" if r_moved else "changed_in_provider")
                    else:
                        export(r_now, replaces=a_now)
                else:
                    review(a_now, "metadata_difference")
            elif a_now is not None:  # deleted in the provider
                if ac:
                    review(a_now, "changed_in_apple_deleted_in_provider")
                elif review_deletions:
                    review(a_now, "deleted_in_provider")
                else:
                    plan["delete_apple"].append(a_now)
            else:  # deleted in Apple
                if rc:
                    review(r_now, "changed_in_provider_deleted_in_apple")
                elif review_deletions:
                    review(r_now, "deleted_in_apple")
                else:
                    plan["delete_provider"].append(r_now)

        # Never-synced logins on one side only. A login on the other side with the same
        # website and username may be this one under another name, so hold both.
        url_indexes = []
        for entries in (apple_entries, remote_entries):
            index: dict = {}
            for e in entries:
                k = e.get_dedup_key()
                if k in handled or not k[2]:
                    continue
                for url in e.get_all_urls():
                    index.setdefault((e.normalize_url(url), k[2]), set()).add(k)
            url_indexes.append(index)
        possible = []
        for entries, own, opposite in (
            (apple_entries, apple_map, url_indexes[1]),
            (remote_entries, remote_map, url_indexes[0]),
        ):
            for e in entries:
                k = e.get_dedup_key()
                if k in handled:
                    continue
                candidates = set()
                for url in e.get_all_urls():
                    candidates.update(opposite.get((e.normalize_url(url), k[2]), set()))
                if candidates - {k} - own.keys():
                    possible.append(e)
        for e in possible:
            review(e, "possible_renamed_identity")
        for e in apple_entries:
            if e.get_dedup_key() not in handled:
                handled.add(e.get_dedup_key())
                plan["create_provider"].append(e)
        for e in remote_entries:
            if e.get_dedup_key() not in handled:
                handled.add(e.get_dedup_key())
                export(e)

        # An export must not replace a different Apple login, or another exported one.
        deleting = {e.get_dedup_key() for e in plan["delete_apple"]}
        deleting |= {e.get_dedup_key() for e in replacing.values()}
        apple_sites = {
            site
            for a in apple_entries
            if a.get_dedup_key() not in deleting
            for site in self._apple_sites(a)
        }
        export_counts: dict = {}
        for e in plan["export_apple"]:
            for site in self._apple_sites(e):
                export_counts[site] = export_counts.get(site, 0) + 1
        exports = []
        for e in plan["export_apple"]:
            if any(s in apple_sites or export_counts[s] > 1 for s in self._apple_sites(e)):
                review(e, "apple_one_login_per_site")
            else:
                exports.append(e)
                if e.get_dedup_key() in replacing:
                    plan["replace_apple"].append((e, replacing[e.get_dedup_key()]))
        plan["export_apple"] = exports

        # A wrong or partial export must not empty the other side: hold mass deletions.
        for name in ("delete_provider", "delete_apple"):
            if len(plan[name]) >= 10 and len(plan[name]) > len(baseline) / 2:
                for e in plan[name]:
                    review(e, "too_many_deletions")
                plan[name] = []

        plan["apple_map"], plan["remote_map"] = apple_map, remote_map
        return plan

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
        review_changes: bool = False,
        review_deletions: bool = False,
    ) -> dict:
        """Two-way sync of one fresh Apple export with the provider.

        Changes and deletions on one side are copied to the other unless
        ``review_changes`` / ``review_deletions`` hold them for review. Apple can only
        be written by importing a CSV, so changes for Apple go into the export and
        deletions are listed for the user to make. Simulation performs reads only.
        Last-sync baselines are saved only for pairs actually observed equal.
        """
        if not run_push and not run_pull:
            raise InvalidInputError("At least one sync phase must be enabled.")
        if run_push and apple_csv_path is None:
            raise InvalidInputError(
                "An Apple Passwords CSV export is required to add logins to the provider."
            )
        start = time.time()
        apple_entries = (
            await asyncio.to_thread(ApplePasswordsCSVParser.parse_file, apple_csv_path)
            if apple_csv_path
            else []
        )
        # Fail closed: never interpret a failed remote read as an empty vault.
        remote_entries = [
            PasswordEntry(
                title=item.get("label") or item.get("title", ""),
                username=item.get("username", ""),
                password=item.get("password", ""),
                url=item.get("url"),
                notes=item.get("notes"),
                otp_auth=item.get("otp_auth"),
                extra_urls=item.get("extra_urls") or [],
                provider_id=item.get("id"),
                folder=item.get("folder_label") or item.get("folder"),
            )
            for item in await provider.list_passwords()
        ]
        provider_type = provider.__class__.__name__.replace("Provider", "").lower()
        mappings = await self.db.get_all_password_mappings(provider_type=provider_type)
        baseline = {
            (
                m["title"].lower().strip(),
                PasswordEntry.normalize_url(m["url"]),
                m["username"].lower().strip(),
            ): m
            for m in mappings
            if m.get("confirmed", False)
        }

        if apple_csv_path is None:
            # Without a fresh Apple snapshot nothing about Apple's side is known.
            empty = {name: [] for name in ("create_provider", "update_provider", "delete_provider",
                                           "export_apple", "replace_apple", "delete_apple", "unchanged")}
            plan = {
                **empty,
                "reviews": [{"action": "review", "reason": "fresh_apple_export_required"}],
                "apple_map": {},
                "remote_map": {},
            }
        else:
            plan = await asyncio.to_thread(
                lambda: self._plan(
                    apple_entries,
                    remote_entries,
                    baseline,
                    review_changes=review_changes,
                    review_deletions=review_deletions,
                )
            )
        reviews = plan["reviews"]
        apple_map, remote_map = plan["apple_map"], plan["remote_map"]
        additions = plan["create_provider"] if run_push else []
        updates = plan["update_provider"] if run_push else []
        removals = plan["delete_provider"] if run_push else []
        exports = plan["export_apple"] if run_pull else []
        replacements = plan["replace_apple"] if run_pull else []
        apple_deletions = plan["delete_apple"] if run_pull else []
        changed_exports = {new.get_dedup_key() for new, _old in replacements}
        if exports and not simulate and output_apple_csv is None:
            raise InvalidInputError("An output path is required when exporting logins for Apple.")

        created = updated = deleted = failed = 0
        folders_created = 0
        if not simulate:
            # Validate/create the export before attempting any remote mutation.
            if exports:
                await asyncio.to_thread(
                    ApplePasswordsCSVParser.write_file, exports, output_apple_csv
                )
            folders_created = await self._ensure_provider_folders(additions, provider)
            if additions and isinstance(provider, VaultwardenProvider):
                stats = await provider.create_passwords(additions)
                created, failed = stats.get("created", 0), stats.get("failed", 0)
            else:
                for entry in additions:
                    try:

                        async def exists(entry=entry):
                            return any(
                                PasswordEntry(
                                    title=item.get("label") or item.get("title", ""),
                                    username=item.get("username", ""),
                                    password=item.get("password", ""),
                                    url=item.get("url"),
                                ).get_dedup_key()
                                == entry.get_dedup_key()
                                for item in await provider.list_passwords()
                            )

                        if await retry_addition(
                            lambda entry=entry: provider.create_password(entry), exists
                        ):
                            created += 1
                    except Exception:
                        failed += 1
                        logger.warning(
                            "Password addition failed; refresh snapshots before retrying"
                        )
            for current, merged in updates:
                try:
                    if await provider.update_password(current.provider_id, merged):
                        updated += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                    logger.warning("Password update failed; refresh snapshots before retrying")
            for current in removals:
                try:
                    if await provider.delete_password(current.provider_id):
                        deleted += 1
                    else:
                        failed += 1
                except Exception:
                    failed += 1
                    logger.warning("Password deletion failed; refresh snapshots before retrying")

            if apple_csv_path is not None:
                confirmed = []
                for key in plan["unchanged"]:
                    a, r = apple_map[key], remote_map[key]
                    confirmed.append(
                        {
                            "title": a.title,
                            "username": a.username,
                            "url": a.key_url(),
                            "provider_id": r.provider_id,
                            "provider_type": provider_type,
                            "last_apple_hash": a.get_password_hash(),
                            "last_provider_hash": r.get_password_hash(),
                            "last_apple_content": self._fingerprint(a),
                            "last_provider_content": self._fingerprint(r),
                        }
                    )
                # Retire tracking for logins gone from both snapshots, and tracking
                # superseded by an observed equal pair with Apple's host-only URL.
                unchanged = set(plan["unchanged"])
                retired = []
                for m in mappings:
                    key = (
                        m["title"].lower().strip(),
                        PasswordEntry.normalize_url(m["url"]),
                        m["username"].lower().strip(),
                    )
                    alias_replaced = key in unchanged and (m.get("url") or "") != (
                        apple_map[key].key_url() or ""
                    )
                    if alias_replaced or (
                        m.get("confirmed") and key not in apple_map and key not in remote_map
                    ):
                        retired.append(m)
                await self.db.confirm_password_snapshot(confirmed, retired, apple_entries)
                await self.db.record_sync(
                    sync_type="apple_import",
                    file_path=str(apple_csv_path),
                    entry_count=len(apple_entries),
                )

        def rows(entries, simulated: str, done: str) -> list[dict]:
            return [
                {"title": e.title, "username": e.username, "action": simulated if simulate else done}
                for e in entries
            ]

        policy = {"review_changes": review_changes, "review_deletions": review_deletions}
        push = {
            "created": created,
            "planned_created": len(additions),
            "updated": updated,
            "planned_updated": len(updates),
            "deleted": deleted,
            "planned_deleted": len(removals),
            "failed": failed,
            "errors": [],
            "simulate": simulate,
            "queued": len(additions) + len(updates) + len(removals),
            "skipped": len(plan["unchanged"]),
            "folders_created": folders_created,
            "conflicts": reviews,
            "entries": rows(additions, "would_create", "addition_attempted")
            + rows([merged for _, merged in updates], "would_update", "update")
            + rows(removals, "would_delete", "delete")
            + reviews,
        }
        pull = {
            "new_entries": 0 if simulate else len(exports) - len(replacements),
            "planned_new_entries": len(exports) - len(replacements),
            "updated": 0 if simulate else len(replacements),
            "planned_updated": len(replacements),
            "deleted": 0 if simulate else len(apple_deletions),
            "planned_deleted": len(apple_deletions),
            "simulate": simulate,
            "conflicts": reviews,
            "download_path": str(output_apple_csv) if exports and not simulate else None,
            "entries": rows(
                [e for e in exports if e.get_dedup_key() not in changed_exports],
                "would_export",
                "export_for_manual_import",
            )
            + [
                {
                    "title": new.title,
                    "username": new.username,
                    "action": "would_replace_in_apple" if simulate else "replace_in_apple",
                    # Apple won't import over this login: delete it first, or edit it by hand.
                    "replaces": old.title,
                }
                for new, old in replacements
            ]
            + rows(apple_deletions, "would_delete_in_apple", "delete_in_apple")
            + reviews,
        }
        return {
            "push": push if run_push else None,
            "pull": pull if run_pull else None,
            "total_time": time.time() - start,
            "simulate": simulate,
            "run_push": run_push,
            "run_pull": run_pull,
            "policy": policy,
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

        logger.info("Ensuring provider folders for %s tagged entrie(s)", len(folders_needed))

        existing = await provider.list_folders()
        # Handle both "name" and "label" keys for folder names
        folder_names = {folder.get("name") or folder.get("label") for folder in existing}
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

    def _deduplicate_entries(self, entries: list[PasswordEntry]) -> list[PasswordEntry]:
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

    def _is_better_entry(self, entry1: PasswordEntry, entry2: PasswordEntry) -> bool:
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

