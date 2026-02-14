"""Photo export engine: Apple Photos -> Local Folder.

Exports photos from Apple Photos to a local folder (typically synced by
NextCloud desktop app to the cloud). Supports date-based organization
and deduplication to avoid re-exporting photos.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Awaitable

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

            # Skip if no file path
            if not asset.file_path or not asset.file_path.exists():
                logger.debug("Skipping %s: file not found", asset.uuid)
                errors += 1
                continue

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

        logger.info(
            "Export analysis: %d to export, %d skipped (baseline), "
            "%d skipped (already exported), %d skipped (from NextCloud)",
            len(to_export),
            skipped_baseline,
            skipped_exported,
            skipped_imported,
        )

        if dry_run:
            if progress_callback:
                await progress_callback(100, f"Dry run complete - {len(to_export)} would be exported")

            return {
                "exported": 0,
                "would_export": len(to_export),
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

        # Perform actual export (copy files)
        exported = 0
        export_errors = 0
        total_to_export = len(to_export)

        for idx, (asset, content_hash, dest_path) in enumerate(to_export):
            if progress_callback and (idx % 5 == 0 or idx == total_to_export - 1):
                progress = 55 + int((idx / total_to_export) * 40)
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

        # Update last export time
        await self.db.update_last_export()

        if progress_callback:
            await progress_callback(100, f"Export complete - {exported} files copied")

        return {
            "exported": exported,
            "skipped_before_baseline": skipped_baseline,
            "skipped_already_exported": skipped_exported,
            "skipped_imported_from_nextcloud": skipped_imported,
            "errors": errors + export_errors,
            "dry_run": False,
        }

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
