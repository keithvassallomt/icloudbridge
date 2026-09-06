"""Photo export engine: Apple Photos -> Local Folder.

Exports photos from Apple Photos to a local folder (typically synced by
NextCloud desktop app to the cloud). Supports date-based organization
and deduplication to avoid re-exporting photos.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable

from icloudbridge.sources.photos.applescript import PhotosAppleScriptAdapter
from icloudbridge.sources.photos.library_reader import PhotosLibraryReader, PhotoAsset
from icloudbridge.sources.photos.photokit_bridge import (
    PhotoKitBridgeClient,
    PhotoKitUnavailable,
)
from icloudbridge.utils.photos_db import PhotosDB

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], Awaitable[None]]

# Cloud-only assets are exported in bounded batches. Each Photos filename
# lookup is a full library scan, so an unbounded batch can occupy Photos for
# hours before writing a single file. Batching keeps files landing on disk,
# bounds the blast radius of a hung query, and gives natural checkpoints.
CLOUD_EXPORT_BATCH_SIZE = 200

# Progress band reserved for the cloud-only export phase.
CLOUD_PROGRESS_START = 70
CLOUD_PROGRESS_END = 90

# Headroom to leave on the destination volume. Filling a boot disk completely
# takes macOS down with it, so an export refuses to start if it would eat into
# this.
DISK_SPACE_MARGIN_BYTES = 2 * 1024**3


class InsufficientDiskSpace(RuntimeError):
    """The destination cannot hold what the export would write."""


@dataclass
class ExportConfig:
    """Configuration for photo export to local folder."""

    export_folder: Path
    organize_by: str = "date"  # "date" (2026/02/) or "flat" (no subfolders)


class PhotoExportEngine:
    """Export photos from Apple Photos to a local folder.

    Handles:
    - Scanning Photos library for new/changed photos
    - Hash-based deduplication to avoid re-exporting
    - Tracking export state in database
    - Organizing by date in the local folder
    """

    def __init__(
        self,
        config: ExportConfig,
        db: PhotosDB,
        library_path: Path | None = None,
    ):
        self.config = config
        self.db = db
        self.reader = PhotosLibraryReader(library_path)

    async def initialize(self) -> None:
        """Initialize the export engine."""
        await self.db.initialize()
        # Ensure export folder exists
        self.config.export_folder.mkdir(parents=True, exist_ok=True)

    async def cleanup(self) -> None:
        """Clean up resources."""
        self.reader.cleanup()

    async def export(
        self,
        *,
        full_library: bool = False,
        since_date: datetime | None = None,
        album_filter: str | None = None,
        dry_run: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> dict:
        """Export photos from Apple Photos to local folder.

        Args:
            full_library: If True, export all photos regardless of baseline
            since_date: Only export photos created after this date
            album_filter: Only export from specific album
            dry_run: Preview without copying files
            progress_callback: Async callback for progress updates

        Returns:
            Statistics dict with counts
        """
        if progress_callback:
            await progress_callback(0, "Initializing export...")

        # Get export state
        export_state = await self.db.get_export_state()
        baseline_date: datetime | None = None

        if not full_library:
            if since_date:
                baseline_date = since_date
            elif export_state and export_state.get("baseline_date"):
                baseline_date = datetime.fromtimestamp(export_state["baseline_date"])
            else:
                # First run - set baseline to now
                await self.db.set_export_baseline()
                logger.info(
                    "First export run - baseline set to now. "
                    "Future exports will only include new photos."
                )
                return {
                    "exported": 0,
                    "skipped_before_baseline": 0,
                    "skipped_already_exported": 0,
                    "skipped_imported_from_nextcloud": 0,
                    "errors": 0,
                    "dry_run": dry_run,
                    "baseline_set": True,
                    "message": "Baseline set. Run export again to export new photos.",
                }

        if progress_callback:
            await progress_callback(5, "Scanning Apple Photos library...")

        # Enumerate assets from Photos library
        assets: list[PhotoAsset] = []
        async for asset in self.reader.enumerate_assets(
            since_date=baseline_date,
            album_filter=album_filter,
        ):
            assets.append(asset)

        total_assets = len(assets)
        logger.info("Found %d assets to potentially export", total_assets)

        if progress_callback:
            await progress_callback(10, f"Found {total_assets} photos to analyze...")

        # Filter and prepare for export
        to_export: list[tuple[PhotoAsset, str, Path]] = []  # (asset, hash, local_dest)
        cloud_only: list[PhotoAsset] = []  # Assets without local originals
        skipped_baseline = 0
        skipped_exported = 0
        skipped_imported = 0
        errors = 0

        for idx, asset in enumerate(assets):
            if progress_callback and (idx % 10 == 0 or idx == total_assets - 1):
                progress = 10 + int((idx / total_assets) * 40)
                await progress_callback(
                    progress, f"Analyzing photo {idx + 1} of {total_assets}..."
                )

            # Skip if before baseline (shouldn't happen with since_date filter, but double-check)
            if baseline_date and asset.created_date < baseline_date:
                skipped_baseline += 1
                continue

            # Check if already exported by UUID
            existing_export = await self.db.get_export_by_uuid(asset.uuid)
            if existing_export:
                logger.debug("Skipping %s: already exported", asset.uuid)
                skipped_exported += 1
                continue

            # If file isn't locally available, queue for AppleScript export
            if not asset.file_path or not asset.file_path.exists():
                if not asset.filename:
                    errors += 1
                    continue
                cloud_only.append(asset)
                continue

            # Compute hash
            content_hash = await self.reader.compute_asset_hash(asset)
            if not content_hash:
                logger.warning("Failed to compute hash for %s", asset.uuid)
                errors += 1
                continue

            # Check if already exported by hash
            existing_by_hash = await self.db.get_export_by_hash(content_hash)
            if existing_by_hash:
                logger.debug("Skipping %s: already exported (by hash)", asset.uuid)
                skipped_exported += 1
                continue

            # Check if this was imported from NextCloud (don't re-export)
            import_record = await self.db.get_by_hash(content_hash)
            if import_record and import_record.get("origin") == "nextcloud":
                logger.debug("Skipping %s: imported from NextCloud", asset.uuid)
                skipped_imported += 1
                continue

            # Determine local destination path
            dest_path = self._get_local_dest_path(asset)

            to_export.append((asset, content_hash, dest_path))

        if cloud_only:
            logger.info(
                "%d photos have no local originals (cloud-only / shared library), "
                "will export via Photos.app",
                len(cloud_only),
            )

        logger.info(
            "Export analysis: %d to export, %d cloud-only, %d skipped (baseline), "
            "%d skipped (already exported), %d skipped (from NextCloud), %d errors",
            len(to_export),
            len(cloud_only),
            skipped_baseline,
            skipped_exported,
            skipped_imported,
            errors,
        )

        if dry_run:
            if progress_callback:
                would = len(to_export) + len(cloud_only)
                await progress_callback(100, f"Dry run complete - {would} would be exported")

            return {
                "exported": 0,
                "would_export": len(to_export),
                "would_export_cloud": len(cloud_only),
                "skipped_before_baseline": skipped_baseline,
                "skipped_already_exported": skipped_exported,
                "skipped_imported_from_nextcloud": skipped_imported,
                "errors": errors,
                "dry_run": True,
                "preview": [
                    {
                        "filename": asset.filename,
                        "dest_path": str(dest_path),
                        "size": asset.file_size,
                        "created": asset.created_date.isoformat(),
                    }
                    for asset, _, dest_path in to_export[:50]  # Limit preview
                ],
            }

        # Refuse to start rather than fill the destination volume. A boot
        # disk at zero free space takes the whole machine down, and a
        # part-finished export leaves thousands of unrecorded copies behind.
        self._clear_orphaned_staging()
        await self._check_disk_space(to_export, cloud_only)

        # Perform actual export (copy files with local originals)
        exported = 0
        export_errors = 0
        total_to_export = len(to_export)

        for idx, (asset, content_hash, dest_path) in enumerate(to_export):
            if progress_callback and (idx % 5 == 0 or idx == total_to_export - 1):
                progress = 50 + int(
                    (idx / max(total_to_export, 1)) * (CLOUD_PROGRESS_START - 50)
                )
                await progress_callback(
                    progress, f"Exporting {idx + 1} of {total_to_export}..."
                )

            try:
                # Ensure destination directory exists
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                # Another photo of the same name may already hold this path.
                dest_path = await self._resolve_dest_path(dest_path, content_hash)

                # Copy file to destination
                self._place(asset.file_path, dest_path, move=False)

                # Record in database
                await self.db.record_export(
                    content_hash=content_hash,
                    apple_asset_uuid=asset.uuid,
                    nextcloud_path=str(dest_path.relative_to(self.config.export_folder)),
                    nextcloud_etag=None,  # No etag for local files
                    file_size=asset.file_size,
                    media_type=asset.media_type,
                    captured_at=asset.created_date,
                )
                exported += 1
                logger.debug("Exported: %s -> %s", asset.filename, dest_path)

            except OSError as e:
                if e.errno == errno.ENOSPC:
                    # Every remaining copy would fail the same way. Carrying on
                    # just buries the real cause under thousands of identical
                    # errors and leaves the volume pinned at zero free space.
                    raise InsufficientDiskSpace(
                        f"Ran out of space on {self.config.export_folder} after "
                        f"exporting {exported} of {total_to_export} photos. "
                        f"Free up space or point the export at a larger volume, "
                        f"then run the sync again to resume."
                    ) from e
                logger.error("Failed to export %s: %s", asset.filename, e)
                export_errors += 1
            except Exception as e:
                logger.error("Failed to export %s: %s", asset.filename, e)
                export_errors += 1

        # Export cloud-only photos via AppleScript (downloads originals automatically)
        cloud_exported = 0
        if cloud_only:
            cloud_exported, cloud_errors, cloud_skipped = await self._export_cloud_only(
                cloud_only, progress_callback
            )
            exported += cloud_exported
            export_errors += cloud_errors
            # Assets skipped by hash dedup are neither exported nor failures;
            # counting them as neither used to make the totals silently
            # under-report the size of the run.
            skipped_exported += cloud_skipped

        # Update last export time
        await self.db.update_last_export()

        if progress_callback:
            await progress_callback(100, f"Export complete - {exported} files copied")

        return {
            "exported": exported,
            "exported_cloud": cloud_exported,
            "skipped_before_baseline": skipped_baseline,
            "skipped_already_exported": skipped_exported,
            "skipped_imported_from_nextcloud": skipped_imported,
            "errors": errors + export_errors,
            "dry_run": False,
        }

    async def _export_cloud_only(
        self,
        assets: list[PhotoAsset],
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[int, int, int]:
        """Export cloud-only photos, preferring PhotoKit over AppleScript.

        Photos' AppleScript interface cannot select a subset of the library
        efficiently: `whose filename is`, `media item id` and even a contiguous
        index range each rescan every asset, costing seconds apiece. PhotoKit's
        `fetchAssets(withLocalIdentifiers:)` is an indexed lookup, so the
        menubar app does the work when it is running.

        The AppleScript path remains as a fallback for when the app is closed,
        and picks up whatever PhotoKit did not manage if the bridge drops
        mid-run.

        Returns:
            Tuple of (exported_count, error_count, skipped_count)
        """
        client = PhotoKitBridgeClient()

        if not await client.is_available():
            logger.info(
                "PhotoKit bridge unavailable; using AppleScript export for %d "
                "cloud-only photos (this is much slower)",
                len(assets),
            )
            return await self._export_cloud_only_applescript(assets, progress_callback)

        exported, errors, skipped, handled = await self._export_cloud_only_photokit(
            assets, client, progress_callback
        )

        remaining = [a for a in assets if a.uuid not in handled]
        if not remaining:
            return exported, errors, skipped

        logger.warning(
            "PhotoKit handled %d of %d cloud-only photos; falling back to "
            "AppleScript for the remaining %d",
            len(handled),
            len(assets),
            len(remaining),
        )
        fb_exported, fb_errors, fb_skipped = await self._export_cloud_only_applescript(
            remaining, progress_callback
        )
        return exported + fb_exported, errors + fb_errors, skipped + fb_skipped

    async def _export_cloud_only_photokit(
        self,
        assets: list[PhotoAsset],
        client: PhotoKitBridgeClient,
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[int, int, int, set[str]]:
        """Export via the menubar app's PhotoKit bridge.

        Files are staged in a temp directory and processed as they stream back,
        so progress is continuous and each asset is checkpointed to the
        database as soon as its file lands.

        Returns:
            Tuple of (exported, errors, skipped, handled_uuids). `handled_uuids`
            lets the caller retry only what PhotoKit did not get to if the
            bridge drops mid-run.
        """
        by_uuid = {asset.uuid: asset for asset in assets}
        total = len(assets)

        exported = 0
        errors = 0
        skipped = 0
        handled: set[str] = set()

        temp_dir = self._make_staging_dir("photokit_")
        try:
            try:
                async for progress in client.export(list(by_uuid), temp_dir):
                    for item in progress.items:
                        asset = by_uuid.get(item.identifier)
                        if asset is None:
                            logger.warning(
                                "PhotoKit returned an unrequested asset %s",
                                item.identifier,
                            )
                            continue

                        try:
                            outcome = await self._store_photokit_item(asset, item)
                        except Exception as e:
                            logger.error(
                                "Failed to store PhotoKit export %s (%s): %s",
                                asset.filename,
                                item.identifier,
                                e,
                            )
                            if item.kind == "original":
                                errors += 1
                                handled.add(asset.uuid)
                            continue

                        if item.kind == "original":
                            handled.add(asset.uuid)
                            if outcome == "exported":
                                exported += 1
                            else:
                                skipped += 1

                    for failure in progress.failures:
                        if failure.identifier not in handled:
                            handled.add(failure.identifier)
                            errors += 1
                            logger.warning(
                                "PhotoKit could not export %s: %s",
                                failure.identifier,
                                failure.message,
                            )

                    if progress_callback and total:
                        done = min(progress.completed, total)
                        pct = CLOUD_PROGRESS_START + int(
                            (done / total) * (CLOUD_PROGRESS_END - CLOUD_PROGRESS_START)
                        )
                        await progress_callback(
                            pct,
                            f"Downloading cloud photos via Photos: {done} of {total}...",
                        )

            except PhotoKitUnavailable as e:
                # Keep whatever completed; the caller retries the rest.
                logger.warning("PhotoKit export interrupted: %s", e)

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return exported, errors, skipped, handled

    async def _store_photokit_item(self, asset: PhotoAsset, item) -> str:
        """Hash, dedupe, move and record one file the bridge exported.

        Returns:
            "exported" or "skipped".
        """
        content_hash = await self._hash_file(item.path)

        if await self.db.get_export_by_hash(content_hash):
            logger.debug("Skipping cloud %s: already exported (by hash)", asset.uuid)
            return "skipped"

        import_record = await self.db.get_by_hash(content_hash)
        if import_record and import_record.get("origin") == "nextcloud":
            logger.debug("Skipping cloud %s: imported from NextCloud", asset.uuid)
            return "skipped"

        dest_path = self._get_local_dest_path(asset)
        if item.kind == "pairedVideo":
            dest_path = dest_path.with_suffix(".mov")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path = await self._resolve_dest_path(dest_path, content_hash)
        self._place(item.path, dest_path, move=True)

        await self.db.record_export(
            content_hash=content_hash,
            # The paired Live Photo video is a second file for the same asset,
            # so it needs its own key to avoid colliding with the still.
            apple_asset_uuid=(
                asset.uuid if item.kind == "original" else asset.uuid + "_live"
            ),
            nextcloud_path=str(dest_path.relative_to(self.config.export_folder)),
            nextcloud_etag=None,
            file_size=dest_path.stat().st_size,
            media_type="video" if item.kind == "pairedVideo" else asset.media_type,
            captured_at=asset.created_date,
        )
        logger.info("Exported cloud-only via PhotoKit: %s -> %s", item.filename, dest_path)
        return "exported"

    async def _export_cloud_only_applescript(
        self,
        assets: list[PhotoAsset],
        progress_callback: ProgressCallback | None = None,
    ) -> tuple[int, int, int]:
        """Export cloud-only photos via AppleScript, in bounded batches.

        Fallback for when the menubar app's PhotoKit bridge is unavailable.
        This path is slow by nature - see `_export_cloud_only` - and is kept
        only so a sync still works with the app closed.

        Photos.app automatically downloads originals from iCloud (including
        shared library) before exporting.

        Work is split into batches so that files land on disk continuously,
        progress is observable, each batch is checkpointed to the database as
        it completes, and a hung Photos query kills only its own batch rather
        than the whole run.

        Returns:
            Tuple of (exported_count, error_count, skipped_count)
        """
        adapter = PhotosAppleScriptAdapter()
        total = len(assets)
        batch_count = (total + CLOUD_EXPORT_BATCH_SIZE - 1) // CLOUD_EXPORT_BATCH_SIZE

        exported = 0
        errors = 0
        skipped = 0

        for batch_index, start in enumerate(
            range(0, total, CLOUD_EXPORT_BATCH_SIZE), start=1
        ):
            batch = assets[start : start + CLOUD_EXPORT_BATCH_SIZE]

            if progress_callback:
                progress = CLOUD_PROGRESS_START + int(
                    (start / max(total, 1))
                    * (CLOUD_PROGRESS_END - CLOUD_PROGRESS_START)
                )
                await progress_callback(
                    progress,
                    f"Downloading cloud photos from Photos.app: "
                    f"{start} of {total} (batch {batch_index} of {batch_count})...",
                )

            temp_dir = self._make_staging_dir("cloud_export_")
            try:
                # Photos.app uses original filenames (e.g. IMG_1234.HEIC), not
                # the UUID-based ZFILENAME from the database.
                filenames = [a.original_filename or a.filename for a in batch]

                try:
                    count = await adapter.export_by_filenames(filenames, temp_dir)
                except (TimeoutError, RuntimeError) as e:
                    logger.error(
                        "Cloud export batch %d of %d failed (%d photos): %s",
                        batch_index,
                        batch_count,
                        len(batch),
                        e,
                    )
                    errors += len(batch)
                    continue

                if count == 0:
                    logger.warning(
                        "AppleScript export returned 0 items for batch %d of %d "
                        "(%d cloud-only photos)",
                        batch_index,
                        batch_count,
                        len(batch),
                    )
                    errors += len(batch)
                    continue

                logger.info(
                    "Photos.app exported %d items for batch %d of %d",
                    count,
                    batch_index,
                    batch_count,
                )

                b_exported, b_errors, b_skipped = await self._process_exported_batch(
                    batch, temp_dir
                )
                exported += b_exported
                errors += b_errors
                skipped += b_skipped

            finally:
                shutil.rmtree(temp_dir, ignore_errors=True)

        if progress_callback:
            await progress_callback(
                CLOUD_PROGRESS_END,
                f"Cloud photo export complete - {exported} of {total} exported",
            )

        return exported, errors, skipped

    async def _process_exported_batch(
        self, batch: list[PhotoAsset], temp_dir: Path
    ) -> tuple[int, int, int]:
        """Hash, move and record the files Photos wrote for one batch.

        Returns:
            Tuple of (exported_count, error_count, skipped_count)
        """
        # Index exported files by name. Several assets can legitimately share
        # an original filename, and Photos may rename collisions on export, so
        # each name maps to a *list* of files which are consumed as they are
        # claimed. A plain dict would silently drop all but one.
        available: dict[str, list[Path]] = {}
        for f in sorted(temp_dir.iterdir()):
            if f.is_file():
                available.setdefault(f.name.lower(), []).append(f)

        exported = 0
        errors = 0
        skipped = 0

        for asset in batch:
            original = asset.original_filename or asset.filename
            exported_file = self._claim_exported_file(available, original, asset.filename)

            if not exported_file:
                logger.debug(
                    "Cloud export: %s (%s) not found in temp folder",
                    asset.filename,
                    original,
                )
                errors += 1
                continue

            try:
                content_hash = await self._hash_file(exported_file)

                # Check dedup (by hash) before moving
                existing = await self.db.get_export_by_hash(content_hash)
                if existing:
                    logger.debug(
                        "Skipping cloud %s: already exported (by hash)", asset.uuid
                    )
                    skipped += 1
                    continue

                import_record = await self.db.get_by_hash(content_hash)
                if import_record and import_record.get("origin") == "nextcloud":
                    logger.debug(
                        "Skipping cloud %s: imported from NextCloud", asset.uuid
                    )
                    skipped += 1
                    continue

                dest_path = self._get_local_dest_path(asset)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path = await self._resolve_dest_path(dest_path, content_hash)
                stem = exported_file.stem
                self._place(exported_file, dest_path, move=True)

                file_size = dest_path.stat().st_size

                await self.db.record_export(
                    content_hash=content_hash,
                    apple_asset_uuid=asset.uuid,
                    nextcloud_path=str(
                        dest_path.relative_to(self.config.export_folder)
                    ),
                    nextcloud_etag=None,
                    file_size=file_size,
                    media_type=asset.media_type,
                    captured_at=asset.created_date,
                )
                exported += 1
                logger.info("Exported cloud-only: %s -> %s", asset.filename, dest_path)

                # Check for a paired Live Photo .mov with the same stem
                mov_file = self._claim_exported_file(
                    available, stem + ".mov", exact_only=True
                )
                if mov_file and mov_file.exists():
                    mov_hash = await self._hash_file(mov_file)
                    if not await self.db.get_export_by_hash(mov_hash):
                        mov_dest = await self._resolve_dest_path(
                            dest_path.with_suffix(".mov"), mov_hash
                        )
                        self._place(mov_file, mov_dest, move=True)
                        mov_size = mov_dest.stat().st_size
                        await self.db.record_export(
                            content_hash=mov_hash,
                            apple_asset_uuid=asset.uuid + "_live",
                            nextcloud_path=str(
                                mov_dest.relative_to(self.config.export_folder)
                            ),
                            nextcloud_etag=None,
                            file_size=mov_size,
                            media_type="video",
                            captured_at=asset.created_date,
                        )
                        logger.info(
                            "Exported Live Photo video: %s -> %s",
                            mov_file.name,
                            mov_dest,
                        )

            except Exception as e:
                logger.error("Failed to export cloud-only %s: %s", asset.filename, e)
                errors += 1

        return exported, errors, skipped

    @staticmethod
    def _claim_exported_file(
        available: dict[str, list[Path]], *names: str, exact_only: bool = False
    ) -> Path | None:
        """Take one exported file matching any of `names`, removing it from the pool.

        Each file is handed out at most once, so N assets sharing a filename
        claim N distinct exported files instead of all resolving to the same
        one (which previously left the first mover's file gone and every
        subsequent asset failing).

        Args:
            exact_only: Skip the collision-rename fallback. Used for the paired
                Live Photo video, where a loose match would let an asset with
                no video of its own claim one belonging to a same-named asset.
        """
        for name in names:
            if not name:
                continue
            candidates = available.get(name.lower())
            if candidates:
                return candidates.pop(0)

        if exact_only:
            return None

        # Photos renames collisions on export (e.g. "IMG_1234 2.HEIC"). Fall
        # back to any unclaimed file whose stem starts with the requested stem,
        # but only after exact matches are exhausted, since a suffix like
        # "IMG_0465 (1).JPG" can also be a genuine original filename.
        for name in names:
            if not name:
                continue
            stem = Path(name).stem.lower()
            suffix = Path(name).suffix.lower()
            for key, candidates in available.items():
                if not candidates:
                    continue
                cand = Path(key)
                if cand.suffix == suffix and cand.stem.startswith(stem):
                    return candidates.pop(0)

        return None

    @staticmethod
    async def _hash_file(path: Path) -> str:
        """Compute SHA256 hash of a file."""
        import asyncio

        def _do_hash() -> str:
            h = hashlib.sha256()
            with path.open("rb") as f:
                while chunk := f.read(1024 * 1024):
                    h.update(chunk)
            return h.hexdigest()

        return await asyncio.to_thread(_do_hash)

    def _staging_root(self) -> Path:
        """Where in-flight files live before being moved into place.

        Two constraints. It must be on the *same volume* as the export folder:
        `tempfile.mkdtemp()` uses /var/folders on the boot disk, so a large
        cloud export would fill the boot disk while the destination sat empty,
        and a cross-volume move is a copy rather than a rename. And it must sit
        *outside* the export folder, or the NextCloud client would sync the
        half-written files staged inside it.

        A sibling directory satisfies both. If the export folder is a volume
        root - where there is no usable sibling - fall back to a dot-directory
        inside it.
        """
        parent = self.config.export_folder.parent
        if parent != self.config.export_folder and os.access(parent, os.W_OK):
            return parent / ".icloudbridge-staging"
        return self.config.export_folder / ".icloudbridge-staging"

    def _make_staging_dir(self, prefix: str) -> Path:
        base = self._staging_root()
        base.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=base))

    def _clear_orphaned_staging(self) -> None:
        """Remove staging left by a run that was killed mid-export.

        Those files were never recorded, so the assets they belong to will be
        exported again; keeping them only wastes space on the volume the
        export is about to need.
        """
        base = self._staging_root()
        if not base.exists():
            return
        for leftover in base.iterdir():
            if leftover.is_dir():
                logger.info("Clearing orphaned export staging: %s", leftover)
                shutil.rmtree(leftover, ignore_errors=True)

    async def _check_disk_space(
        self,
        to_export: list[tuple[PhotoAsset, str, Path]],
        cloud_only: list[PhotoAsset],
    ) -> None:
        """Abort before writing anything if the destination cannot hold it.

        `PhotoAsset.file_size` cannot be trusted: ZASSET has no ZFILESIZE
        column on several macOS versions, and the reader reports 0 for every
        asset there. So the originals are measured on disk instead, and
        cloud-only assets - which have no local file yet - are approximated
        from the average of the ones that do.

        Raises:
            InsufficientDiskSpace: with the numbers needed to act on it.
        """

        def measure() -> tuple[int, int]:
            sizes: list[int] = []
            for asset, _, _ in to_export:
                size = asset.file_size or 0
                if not size and asset.file_path:
                    try:
                        size = asset.file_path.stat().st_size
                    except OSError:
                        size = 0
                sizes.append(size)

            known = [size for size in sizes if size]
            average = int(sum(known) / len(known)) if known else 0

            cloud_estimate = 0
            for asset in cloud_only:
                cloud_estimate += asset.file_size or average

            return sum(sizes), cloud_estimate

        local_bytes, cloud_bytes = await asyncio.to_thread(measure)
        required = local_bytes + cloud_bytes
        if required == 0:
            logger.warning(
                "Could not estimate export size; skipping the free-space check"
            )
            return

        try:
            free = shutil.disk_usage(self.config.export_folder).free
        except OSError as e:
            logger.warning("Could not check free space on export volume: %s", e)
            return

        def gb(n: int) -> str:
            return f"{n / 1024**3:.1f} GB"

        logger.info(
            "Export needs about %s (%d local, %d cloud-only); %s free on %s",
            gb(required),
            len(to_export),
            len(cloud_only),
            gb(free),
            self.config.export_folder,
        )

        if required + DISK_SPACE_MARGIN_BYTES <= free:
            return

        raise InsufficientDiskSpace(
            f"Not enough room on {self.config.export_folder}: this export needs "
            f"about {gb(required)} for {len(to_export) + len(cloud_only)} photos "
            f"but only {gb(free)} is free "
            f"(keeping {gb(DISK_SPACE_MARGIN_BYTES)} spare). "
            f"Point the export at a larger volume or free up space, then run "
            f"the sync again."
        )

    @staticmethod
    def _place(src: Path, dest: Path, *, move: bool) -> None:
        """Put `src` at `dest` atomically.

        Writing straight to the final path means a killed sync leaves a
        truncated file there - which `_resolve_dest_path` would later read as a
        *different* photo and step around, stranding the partial forever. Data
        goes to a staging name first and is renamed into place, so `dest` only
        ever exists complete.
        """
        staging = dest.with_name(dest.name + ".icbpart")
        try:
            if move:
                shutil.move(str(src), staging)
            else:
                shutil.copy2(src, staging)
            os.replace(staging, dest)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise

    async def _resolve_dest_path(
        self, dest_path: Path, content_hash: str
    ) -> Path:
        """Pick a destination that will not overwrite a different photo.

        Filenames are not unique in a Photos library - several distinct assets
        can be called IMG_1234.HEIC - and the date layout puts everything from
        one month in one folder, so same-named photos collide. Writing straight
        to `_get_local_dest_path` silently replaced whichever photo got there
        first, and both rows still pointed at the one surviving file.

        When the occupying file has the same content (a run that copied the
        file but died before recording it) the path is reused, so resuming does
        not litter the folder with near-duplicates.
        """
        if not dest_path.exists():
            return dest_path

        if await self._hash_file(dest_path) == content_hash:
            return dest_path

        stem, suffix = dest_path.stem, dest_path.suffix
        counter = 2
        while True:
            candidate = dest_path.with_name(f"{stem} {counter}{suffix}")
            if not candidate.exists():
                return candidate
            if await self._hash_file(candidate) == content_hash:
                return candidate
            counter += 1

    def _get_local_dest_path(self, asset: PhotoAsset) -> Path:
        """Determine the local destination path for an asset."""
        filename = asset.original_filename or asset.filename

        if self.config.organize_by == "flat":
            return self.config.export_folder / filename

        # Default: organize by date (YYYY/MM format)
        if asset.created_date:
            year = asset.created_date.strftime("%Y")
            month = asset.created_date.strftime("%m")
            return self.config.export_folder / year / month / filename

        return self.config.export_folder / "Unknown" / filename

    async def set_baseline(self, date: datetime | None = None) -> None:
        """Set the export baseline date.

        Photos before this date won't be exported (unless full_library=True).
        """
        await self.db.set_export_baseline(date)

    async def get_library_stats(self) -> dict:
        """Get statistics about the Apple Photos library."""
        return await self.reader.get_library_stats()

    async def list_albums(self) -> list[dict]:
        """List albums in Apple Photos."""
        albums = await self.reader.list_albums()
        return [
            {"uuid": a.uuid, "name": a.name, "count": a.asset_count}
            for a in albums
        ]
