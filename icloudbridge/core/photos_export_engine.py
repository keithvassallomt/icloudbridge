"""Photo export engine: Apple Photos -> Local Folder.

Exports photos from Apple Photos to a local folder (typically synced by
NextCloud desktop app to the cloud). Supports date-based organization
and deduplication to avoid re-exporting photos.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable

from icloudbridge.sources.photos.applescript import PhotosAppleScriptAdapter
from icloudbridge.sources.photos.library_reader import PhotosLibraryReader, PhotoAsset
from icloudbridge.utils.photos_db import PhotosDB

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, str], Awaitable[None]]


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

        # Perform actual export (copy files with local originals)
        exported = 0
        export_errors = 0
        total_to_export = len(to_export)

        for idx, (asset, content_hash, dest_path) in enumerate(to_export):
            if progress_callback and (idx % 5 == 0 or idx == total_to_export - 1):
                progress = 55 + int((idx / max(total_to_export, 1)) * 20)
                await progress_callback(
                    progress, f"Exporting {idx + 1} of {total_to_export}..."
                )

            try:
                # Ensure destination directory exists
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                # Copy file to destination
                shutil.copy2(asset.file_path, dest_path)

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

            except Exception as e:
                logger.error("Failed to export %s: %s", asset.filename, e)
                export_errors += 1

        # Export cloud-only photos via AppleScript (downloads originals automatically)
        cloud_exported = 0
        if cloud_only:
            cloud_exported, cloud_errors = await self._export_cloud_only(
                cloud_only, progress_callback
            )
            exported += cloud_exported
            export_errors += cloud_errors

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
    ) -> tuple[int, int]:
        """Export cloud-only photos via AppleScript.

        Photos.app automatically downloads originals from iCloud (including
        shared library) before exporting.

        Returns:
            Tuple of (exported_count, error_count)
        """
        if progress_callback:
            await progress_callback(
                78, f"Downloading {len(assets)} cloud-only photos via Photos.app..."
            )

        adapter = PhotosAppleScriptAdapter()

        # Export all cloud-only photos to a temp folder, then move to final destinations
        temp_dir = Path(tempfile.mkdtemp(prefix="icloudbridge_cloud_export_"))

        try:
            # Photos.app uses original filenames (e.g. IMG_1234.HEIC), not
            # the UUID-based ZFILENAME from the database.
            filenames = [a.original_filename or a.filename for a in assets]
            count = await adapter.export_by_filenames(filenames, temp_dir)

            if count == 0:
                logger.warning(
                    "AppleScript export returned 0 items for %d cloud-only photos",
                    len(assets),
                )
                return 0, len(assets)

            logger.info("Photos.app exported %d cloud-only items to temp folder", count)

            # Build lookup of exported files by name (case-insensitive).
            # "using originals" preserves the original format and may also
            # export paired Live Photo .mov files alongside the image.
            exported_by_name: dict[str, Path] = {}
            for f in temp_dir.iterdir():
                if f.is_file():
                    exported_by_name[f.name.lower()] = f

            # Process exported files: hash, move to destination, record
            exported = 0
            errors = 0

            for asset in assets:
                original = asset.original_filename or asset.filename
                exported_file = exported_by_name.get(original.lower())
                if not exported_file:
                    # Fallback: try database filename
                    exported_file = exported_by_name.get(asset.filename.lower())
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
                        logger.debug("Skipping cloud %s: already exported (by hash)", asset.uuid)
                        continue

                    import_record = await self.db.get_by_hash(content_hash)
                    if import_record and import_record.get("origin") == "nextcloud":
                        logger.debug("Skipping cloud %s: imported from NextCloud", asset.uuid)
                        continue

                    dest_path = self._get_local_dest_path(asset)
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(exported_file), dest_path)

                    file_size = dest_path.stat().st_size

                    await self.db.record_export(
                        content_hash=content_hash,
                        apple_asset_uuid=asset.uuid,
                        nextcloud_path=str(dest_path.relative_to(self.config.export_folder)),
                        nextcloud_etag=None,
                        file_size=file_size,
                        media_type=asset.media_type,
                        captured_at=asset.created_date,
                    )
                    exported += 1
                    logger.info("Exported cloud-only: %s -> %s", asset.filename, dest_path)

                except Exception as e:
                    logger.error("Failed to export cloud-only %s: %s", asset.filename, e)
                    errors += 1

            return exported, errors

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

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
