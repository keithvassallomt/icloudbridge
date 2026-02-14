"""Photo synchronization engine (local folder → Apple Photos)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from icloudbridge.core.config import PhotoSourceConfig, PhotosConfig
from icloudbridge.sources.photos import (
    PhotoCandidate,
    PhotoSourceScanner,
    PhotosAppleScriptAdapter,
)
from icloudbridge.utils.exif import extract_capture_timestamp, extract_original_filename
from icloudbridge.utils.photos_db import PhotosDB

logger = logging.getLogger(__name__)

# Type alias for progress callback
ProgressCallback = Callable[[int, str], Awaitable[None]]


@dataclass(slots=True)
class PhotoImportRecord:
    candidate: PhotoCandidate
    content_hash: str
    captured_at: datetime | None


class PhotoSyncEngine:
    """High-level coordinator for folder → Apple Photos imports."""

    def __init__(self, config: PhotosConfig, data_dir: Path):
        self.config = config
        self.data_dir = data_dir
        self.db = PhotosDB(data_dir / "photos.db")
        self.scanner = PhotoSourceScanner(config.sources)
        self.apple = PhotosAppleScriptAdapter()
        self.temp_dir = data_dir / "photos" / "tmp"
        self.meta_dir = data_dir / "photos" / "meta"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self._name_exists_cache: dict[str, bool] = {}
        self._original_name_cache: dict[Path, str | None] = {}
        self._existing_live_stems: set[str] = set()

    async def initialize(self) -> None:
        await self.db.initialize()
        # Run one-time migration to catch up files added since initial scan
        await self._migrate_new_files_to_db()
        # Mark all existing records as imported (initial scan didn't set this)
        await self._migrate_mark_all_imported()

    async def _migrate_new_files_to_db(self) -> None:
        """One-time migration: add files in source folders but not in DB.

        Assumes these files are already in Apple Photos (e.g., synced via iCloud).
        This avoids the slow AppleScript check for each file.
        """
        if await self.db.has_migration("new_files_catchup_v1"):
            return

        logger.info("Running one-time migration: catching up new files...")
        candidates = list(self.scanner.iter_candidates())
        added = 0

        for candidate in candidates:
            # Check if already in DB by path+size (fast)
            existing = await self.db.get_by_path_and_size(candidate.path, candidate.size)
            if existing:
                continue

            # Hash the file and check by hash
            file_hash = await self._hash_file(candidate.path)
            existing_by_hash = await self.db.get_by_hash(file_hash)
            if existing_by_hash:
                continue

            # New file - add to DB as "already imported" (assume in Photos)
            await self.db.record_discovery(
                content_hash=file_hash,
                path=candidate.path,
                size=candidate.size,
                media_type=candidate.media_type,
                source_name=candidate.source_name,
                album=candidate.album,
                captured_at=candidate.mtime,
                mtime=candidate.mtime,
            )
            # Mark as imported so it doesn't get queued for import
            await self.db.mark_imported(
                content_hash=file_hash,
                album=candidate.album,
                apple_local_identifier=None,
            )
            added += 1

        await self.db.set_migration("new_files_catchup_v1")
        logger.info("Migration complete: added %d files to database", added)

    async def _migrate_mark_all_imported(self) -> None:
        """One-time migration: mark all records as imported.

        The initial scan (FirstRunWizard) recorded files but didn't mark them
        as imported. This fixes the count so total_imported reflects reality.
        """
        if await self.db.has_migration("mark_all_imported_v1"):
            return

        logger.info("Running migration: marking all records as imported...")
        await self.db.mark_all_imported()
        await self.db.set_migration("mark_all_imported_v1")
        logger.info("Migration complete: all records marked as imported")

    async def sync(
        self,
        *,
        sources: Iterable[str] | None = None,
        dry_run: bool = False,
        initial_scan: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        if not self.config.sources:
            raise RuntimeError("No photo sources configured")

        await self.initialize()
        self._name_exists_cache.clear()
        self._original_name_cache.clear()
        self._existing_live_stems.clear()

        if progress_callback:
            await progress_callback(5, "Scanning source folders...")

        selected_sources = list(sources) if sources is not None else None
        scanned_sources = selected_sources if selected_sources is not None else self.scanner.available_sources()

        candidates = list(self.scanner.iter_candidates(selected_sources))
        if not initial_scan:
            candidates.sort(key=lambda candidate: 0 if candidate.media_type == "image" else 1)

        if progress_callback:
            await progress_callback(10, f"Found {len(candidates)} files, analyzing...")

        new_records: list[PhotoImportRecord] = []
        skipped_existing = 0
        skipped_exported = 0  # Files exported from Apple Photos (bidirectional dedup)
        total_candidates = len(candidates)

        skipped_by_mtime = 0
        for idx, candidate in enumerate(candidates):
            # Report progress every 10 files or at milestones
            if progress_callback and (idx % 10 == 0 or idx == total_candidates - 1):
                # Progress scaling depends on whether we'll import or not
                # Dry-run/initial_scan: 10% to 95% during hashing (no import phase)
                # Normal sync: 10% to 50% during hashing (import phase is 55-95%)
                if dry_run or initial_scan:
                    progress = 10 + int((idx / total_candidates) * 85)
                else:
                    progress = 10 + int((idx / total_candidates) * 40)
                await progress_callback(progress, f"Analyzing file {idx + 1} of {total_candidates}...")

            # Fast-path: check if file is already known by path+size+mtime (skip expensive hashing)
            existing_by_path = await self.db.get_by_path_and_size(candidate.path, candidate.size)
            if existing_by_path:
                stored_mtime = existing_by_path.get("mtime")
                # If mtime matches, file is unchanged - skip without hashing
                if stored_mtime is not None and abs(stored_mtime - candidate.mtime.timestamp()) < 1.0:
                    skipped_by_mtime += 1
                    continue

            file_hash = await self._hash_file(candidate.path)
            existing = await self.db.get_by_hash(file_hash)
            if existing:
                # Backfill mtime for existing records (migration from older schema)
                if existing.get("mtime") is None:
                    await self.db.update_mtime(file_hash, candidate.mtime)
                continue

            # Bidirectional dedup: skip if this was exported FROM Apple Photos
            # (Wife's photo scenario - we exported it, now seeing it in NextCloud)
            export_record = await self.db.get_export_by_hash(file_hash)
            if export_record:
                logger.debug(
                    "Skipping %s (exported from Apple Photos)",
                    candidate.path,
                )
                skipped_exported += 1
                continue

            if (
                not initial_scan
                and candidate.media_type == "video"
                and candidate.path.stem in self._existing_live_stems
            ):
                logger.debug(
                    "Skipping %s (live photo video already present in Photos)",
                    candidate.path,
                )
                skipped_existing += 1
                continue

            # Extract EXIF timestamp for images, fall back to mtime
            captured_at = candidate.mtime
            if not initial_scan and candidate.media_type == "image":
                exif_timestamp = await asyncio.to_thread(extract_capture_timestamp, candidate.path)
                if exif_timestamp:
                    captured_at = exif_timestamp

            if not initial_scan:
                filename_for_lookup: str | None = None
                if candidate.media_type == "image" and captured_at:
                    filename_for_lookup = await self._get_original_filename(candidate)
                    if not filename_for_lookup:
                        filename_for_lookup = candidate.path.name

                if await self._already_in_library(filename=filename_for_lookup):
                    logger.debug("Skipping %s (already in Photos)", candidate.path)
                    self._existing_live_stems.add(candidate.path.stem)
                    skipped_existing += 1
                    continue

            # Only persist discoveries when we're preparing for a real import.
            # The simulator (dry-run) must stay read-only so running it doesn't
            # permanently suppress pending imports once hashes are cached.
            if not dry_run:
                await self.db.record_discovery(
                    content_hash=file_hash,
                    path=candidate.path,
                    size=candidate.size,
                    media_type=candidate.media_type,
                    source_name=candidate.source_name,
                    album=candidate.album,
                    captured_at=captured_at,
                    mtime=candidate.mtime,
                )

            new_records.append(
                PhotoImportRecord(candidate=candidate, content_hash=file_hash, captured_at=captured_at)
            )

        if not new_records:
            if progress_callback:
                await progress_callback(100, "No new files to import")
            logger.info(
                "Photo sync complete: %d discovered, %d skipped by mtime (fast-path), "
                "%d skipped existing, %d skipped (exported from Photos)",
                len(candidates), skipped_by_mtime, skipped_existing, skipped_exported,
            )
            return {
                "discovered": len(candidates),
                "new_assets": 0,
                "imported": 0,
                "dry_run": dry_run,
                "skipped_existing": skipped_existing,
                "skipped_exported": skipped_exported,
                "skipped_by_mtime": skipped_by_mtime,
                "sources": scanned_sources,
            }

        # Skip import if dry_run (simulate) or initial_scan (building DB)
        if dry_run or initial_scan:
            if progress_callback:
                if initial_scan:
                    await progress_callback(100, f"Initial scan complete - {len(new_records)} files discovered")
                else:
                    await progress_callback(100, f"Simulation complete - {len(new_records)} files would be imported")
            return {
                "discovered": len(candidates),
                "new_assets": len(new_records),
                "imported": 0,
                "dry_run": dry_run,
                "initial_scan": initial_scan,
                "pending": [str(record.candidate.path) for record in new_records[:50]],
                "skipped_existing": skipped_existing,
                "skipped_exported": skipped_exported,
                "skipped_by_mtime": skipped_by_mtime,
                "sources": scanned_sources,
            }

        grouped: dict[str, list[PhotoImportRecord]] = defaultdict(list)
        for record in new_records:
            # Use source album, fall back to config default, then hard-coded default
            album = (record.candidate.album or self.config.default_album or "iCloudBridge Imports").strip()
            if not album:
                album = "iCloudBridge Imports"
            grouped[album].append(record)

        if progress_callback:
            await progress_callback(55, f"Importing {len(new_records)} files to Apple Photos...")

        total_imported = 0
        total_albums = len(grouped)
        for album_idx, (album, records) in enumerate(grouped.items()):
            # Progress from 55% to 95% during import phase
            base_progress = 55 + int((album_idx / total_albums) * 40)
            if progress_callback:
                await progress_callback(
                    base_progress,
                    f"Importing {len(records)} files to album '{album}'..."
                )
            await self.apple.ensure_album(album)
            manifest_path = await self._write_manifest(records)
            try:
                # Import files and get back their Apple local identifiers
                local_identifiers = await self.apple.import_files(manifest_path, album)
            finally:
                manifest_path.unlink(missing_ok=True)

            # Match identifiers to records (they should be in the same order)
            for idx, record in enumerate(records):
                local_id = local_identifiers[idx] if idx < len(local_identifiers) else None
                await self.db.mark_imported(
                    content_hash=record.content_hash,
                    album=album,
                    apple_local_identifier=local_id,
                )
                self._write_sidecar(record, album, local_id)
            total_imported += len(records)

        if progress_callback:
            await progress_callback(100, f"Import complete - {total_imported} files imported")

        return {
            "discovered": len(candidates),
            "new_assets": len(new_records),
            "imported": total_imported,
            "dry_run": False,
            "skipped_existing": skipped_existing,
            "skipped_exported": skipped_exported,
            "skipped_by_mtime": skipped_by_mtime,
            "albums": {album: len(records) for album, records in grouped.items()},
            "sources": scanned_sources,
        }

    async def _hash_file(self, path: Path) -> str:
        algorithm = self.config.hash_algorithm

        def _reader() -> str:
            h = hashlib.new(algorithm)
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    h.update(chunk)
            return h.hexdigest()

        return await asyncio.to_thread(_reader)

    async def _get_original_filename(self, candidate: PhotoCandidate) -> str | None:
        cached = self._original_name_cache.get(candidate.path)
        if cached is not None:
            return cached

        value = await asyncio.to_thread(extract_original_filename, candidate.path)
        self._original_name_cache[candidate.path] = value
        return value

    async def _already_in_library(self, *, filename: str | None) -> bool:
        if not filename:
            return False

        cached_name = self._name_exists_cache.get(filename)
        if cached_name is not None:
            return cached_name

        try:
            exists_by_name = await self.apple.asset_exists_by_name(filename)
        except Exception as exc:  # pragma: no cover - Photos may be unavailable
            logger.warning("Failed to query Photos by name %s: %s", filename, exc)
            exists_by_name = False

        self._name_exists_cache[filename] = exists_by_name
        return exists_by_name

    async def _write_manifest(self, records: list[PhotoImportRecord]) -> Path:
        manifest = self.temp_dir / f"import_{uuid4().hex}.txt"
        contents = "\n".join(str(record.candidate.path) for record in records)
        await asyncio.to_thread(manifest.write_text, contents)
        return manifest

    def _write_sidecar(self, record: PhotoImportRecord, album: str, local_identifier: str | None = None) -> None:
        if not self._source_config(record.candidate.source_name).metadata_sidecars:
            return

        payload = {
            "hash": record.content_hash,
            "source_path": str(record.candidate.path),
            "source_name": record.candidate.source_name,
            "media_type": record.candidate.media_type,
            "album": album,
            "captured_at": record.captured_at.isoformat() if record.captured_at else None,
            "imported_at": datetime.utcnow().isoformat(),
            "apple_local_identifier": local_identifier,
        }
        sidecar = self.meta_dir / f"{record.content_hash}.json"
        sidecar.write_text(json.dumps(payload, indent=2))

    def _source_config(self, name: str) -> PhotoSourceConfig:
        cfg = self.config.sources.get(name)
        if not cfg:
            raise KeyError(f"Unknown photo source '{name}'")
        return cfg
