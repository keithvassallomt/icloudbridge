"""Password synchronization engine for Apple Passwords and password providers."""

import asyncio
import logging
import time
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
        return cls._content(apple) == cls._content(remote) and cls._same_otp(
            apple.otp_auth, remote.otp_auth
        )

    def _build_password_sync_plan(
        self, apple_map: dict, provider_map: dict, mappings: dict
    ) -> dict:
        """Additive plan. Existing/missing mapped logins always require manual review.

        The existing schema records password hashes, not CSV modification times.
        Metadata differences are held for review because no metadata baseline exists.
        """
        plan = {
            name: []
            for name in (
                "create_apple",
                "create_provider",
                "update_apple",
                "update_provider",
                "delete_apple",
                "delete_provider",
                "unchanged",
                "conflicts",
            )
        }
        for key in sorted(set(apple_map) | set(provider_map) | set(mappings), key=repr):
            apple, remote, baseline = apple_map.get(key), provider_map.get(key), mappings.get(key)
            if apple is not None and remote is not None:
                if self._same(apple, remote):
                    plan["unchanged"].append(key)
                else:
                    plan["conflicts"].append((key, apple, remote, baseline))
            elif baseline:
                if apple is not None or remote is not None:
                    plan["conflicts"].append((key, apple, remote, baseline))
            elif apple is not None:
                plan["create_provider"].append(apple)
            elif remote is not None:
                plan["create_apple"].append(remote)
        return plan

    @staticmethod
    def _review_reason(apple, remote, baseline) -> str:
        if apple is None or remote is None:
            return "missing_side_review"
        if apple.get_password_hash() == remote.get_password_hash():
            return "metadata_difference"
        if (
            not baseline
            or not baseline.get("last_apple_hash")
            or not baseline.get("last_provider_hash")
        ):
            return "no_baseline"
        if baseline["last_apple_hash"] != baseline["last_provider_hash"]:
            return "divergent_baseline"
        apple_changed = apple.get_password_hash() != baseline["last_apple_hash"]
        remote_changed = remote.get_password_hash() != baseline["last_provider_hash"]
        if apple_changed and remote_changed:
            return "both_changed"
        if apple_changed:
            return "apple_changed"
        if remote_changed:
            return "provider_changed"
        return "divergent_baseline"

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
        """Reconcile one fresh snapshot, adding only unambiguous, unmapped logins.

        Simulation performs reads only. Exporting an Apple CSV is not confirmation
        of import: save baselines only for pairs actually observed in both snapshots.
        Vaultwarden batches share a vault read but retain individual create outcomes.
        """
        if not run_push and not run_pull:
            raise InvalidInputError("At least one sync phase must be enabled.")
        if run_push and apple_csv_path is None:
            raise InvalidInputError("An Apple Passwords CSV export is required to add logins to the provider.")
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
        apple_map = {e.get_dedup_key(): e for e in apple_entries}
        remote_map = {e.get_dedup_key(): e for e in remote_entries}
        plan = await asyncio.to_thread(
            self._build_password_sync_plan, apple_map, remote_map, baseline
        )
        reviews = [
            {
                "title": (a or r).title,
                "username": (a or r).username,
                "action": "review",
                "reason": self._review_reason(a, r, b),
            }
            for key, a, r, b in plan["conflicts"]
        ]
        # Case folding and URL changes can collide with the existing dedup key.
        # Do not silently choose among duplicate identities or recreate renamed URLs.
        identities = {}
        for side, entries in (("apple", apple_entries), ("provider", remote_entries)):
            for e in entries:
                identity = (e.title.lower().strip(), e.username.lower().strip())
                identities.setdefault(identity, {}).setdefault(side, []).append(e)
        blocked = set()
        for identity, sides in identities.items():
            if any(len(v) > 1 for v in sides.values()) or (
                len(sides) == 2
                and sides["apple"][0].get_dedup_key() != sides["provider"][0].get_dedup_key()
            ):
                blocked.add(identity)
                reviews.append(
                    {
                        "title": identity[0],
                        "username": identity[1],
                        "action": "review",
                        "reason": "ambiguous_identity",
                    }
                )

        # An export may rename a login while retaining its URL and username.
        # Hold possible aliases even before a baseline exists; otherwise the first
        # sync can propose a duplicate and only recognize it on subsequent runs.
        matched = set(plan["unchanged"])
        url_indexes = []
        for entries in (apple_entries, remote_entries):
            index = {}
            for entry in entries:
                username = entry.username.lower().strip()
                if not username:
                    continue
                for url in entry.get_all_urls():
                    index.setdefault((entry.normalize_url(url), username), set()).add(
                        entry.get_dedup_key()
                    )
            url_indexes.append(index)
        for entries, own, opposite in (
            (apple_entries, apple_map, url_indexes[1]),
            (remote_entries, remote_map, url_indexes[0]),
        ):
            for entry in entries:
                key = entry.get_dedup_key()
                identity = (key[0], key[2])
                if key in matched or identity in blocked:
                    continue
                candidates = set()
                for url in entry.get_all_urls():
                    candidates.update(opposite.get((entry.normalize_url(url), key[2]), set()))
                # A candidate still present on this side is a separate login that
                # shares the site and username (e.g. two accounts on one SSO domain),
                # not the old name of this one.
                if candidates - {key} - own.keys():
                    blocked.add(identity)
                    reviews.append(
                        {
                            "title": entry.title,
                            "username": entry.username,
                            "action": "review",
                            "reason": "possible_renamed_identity",
                        }
                    )

        # Index identities once; equal pairs can clear old renamed mappings.
        by_title, by_url, by_id = {}, {}, {}
        for own, entries in ((apple_map, apple_entries), (remote_map, remote_entries)):
            for e in entries:
                key = e.get_dedup_key()
                if key in matched:
                    continue
                by_title.setdefault((key[0], key[2]), []).append((e, own))
                if key[1]:
                    by_url.setdefault((key[1], key[2]), []).append((e, own))
                if e.provider_id:
                    by_id.setdefault(e.provider_id, []).append((e, own))
        for key, previous in baseline.items():
            same_object = by_id.get(previous.get("provider_id"), [])
            similar = by_title.get((key[0], key[2]), []) + by_url.get((key[1], key[2]), [])
            # If the mapped login still exists on that side, a similar entry next
            # to it is a separate login, not a rename of the mapped one.
            candidates = same_object + [(e, own) for e, own in similar if key not in own]
            for e, _own in candidates:
                current = e.get_dedup_key()
                identity = (current[0], current[2])
                if current != key and identity not in blocked:
                    blocked.add(identity)
                    reviews.append(
                        {
                            "title": e.title,
                            "username": e.username,
                            "action": "review",
                            "reason": "mapped_identity_changed",
                        }
                    )
        def allowed(e):
            return (e.title.lower().strip(), e.username.lower().strip()) not in blocked

        additions = [e for e in plan["create_provider"] if allowed(e)] if run_push else []
        exports = [e for e in plan["create_apple"] if allowed(e)] if run_pull else []
        # Apple Passwords keeps one login per website (host) and username, with
        # usernames compared case-sensitively: importing a second one overwrites
        # the first. Never export a login that would collide with an Apple login
        # or with another login in the same export.
        def sites(e):
            username = e.username.strip()
            return {(e.normalize_url(url), username) for url in e.get_all_urls()}

        apple_sites = {site for a in apple_entries for site in sites(a)}
        export_site_counts = {}
        for e in exports:
            for site in sites(e):
                export_site_counts[site] = export_site_counts.get(site, 0) + 1
        importable = []
        for e in exports:
            if any(s in apple_sites or export_site_counts[s] > 1 for s in sites(e)):
                reviews.append(
                    {
                        "title": e.title,
                        "username": e.username,
                        "action": "review",
                        "reason": "apple_one_login_per_site",
                    }
                )
            else:
                importable.append(e)
        exports = importable
        # Without a fresh Apple snapshot we cannot establish absence safely.
        if run_pull and apple_csv_path is None:
            exports = []
            reviews.append({"action": "review", "reason": "fresh_apple_export_required"})
        if exports and not simulate and output_apple_csv is None:
            raise InvalidInputError("An output path is required when exporting logins for Apple.")
        created = failed = 0
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
            if apple_csv_path is not None:
                confirmed = []
                for key in plan["unchanged"]:
                    a, r = apple_map[key], remote_map[key]
                    if allowed(a):
                        confirmed.append(
                            {
                                "title": a.title,
                                "username": a.username,
                                "url": a.key_url(),
                                "provider_id": r.provider_id,
                                "provider_type": provider_type,
                                "last_apple_hash": a.get_password_hash(),
                                "last_provider_hash": r.get_password_hash(),
                            }
                        )
                # Retire tracking omitted by both snapshots or superseded by an
                # actually observed equal pair with Apple's host-only URL.
                retired = []
                for m in mappings:
                    key = (
                        m["title"].lower().strip(),
                        PasswordEntry.normalize_url(m["url"]),
                        m["username"].lower().strip(),
                    )
                    alias_replaced = (
                        key in matched
                        and allowed(apple_map[key])
                        and (m.get("url") or "")
                        != (apple_map[key].key_url() or "")
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
        push = {
            "created": created,
            "planned_created": len(additions),
            "updated": 0,
            "deleted": 0,
            "failed": failed,
            "errors": [],
            "simulate": simulate,
            "queued": len(additions),
            "skipped": len(apple_entries) - len(additions),
            "folders_created": folders_created,
            "conflicts": reviews,
            "entries": [
                {
                    "title": e.title,
                    "username": e.username,
                    "action": "would_create" if simulate else "addition_attempted",
                }
                for e in additions
            ]
            + reviews,
        }
        pull = {
            "new_entries": len(exports) if not simulate else 0,
            "updated": 0,
            "planned_new_entries": len(exports),
            "deleted": 0,
            "simulate": simulate,
            "conflicts": reviews,
            "download_path": str(output_apple_csv) if exports and not simulate else None,
            "entries": [
                {
                    "title": e.title,
                    "username": e.username,
                    "action": "would_export" if simulate else "export_for_manual_import",
                }
                for e in exports
            ]
            + reviews,
        }
        return {
            "push": push if run_push else None,
            "pull": pull if run_pull else None,
            "total_time": time.time() - start,
            "simulate": simulate,
            "run_push": run_push,
            "run_pull": run_pull,
            "policy": "additive_only",
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

