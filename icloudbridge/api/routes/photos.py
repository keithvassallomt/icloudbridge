"""Photo synchronization endpoints."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, status

from icloudbridge.api.dependencies import (
    ConfigDep,
    PhotosDBDep,
    PhotosExportEngineDep,
    PhotosSyncEngineDep,
)
from icloudbridge.api.models import PhotoExportRequest, PhotoSyncRequest
from icloudbridge.api.websocket import send_sync_progress
from icloudbridge.utils.db import SyncLogsDB

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sync")
async def sync_photos(
    request: PhotoSyncRequest,
    config: ConfigDep,
    engine: PhotosSyncEngineDep,
):
    """Trigger a photo synchronization run."""

    if not config.photos.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Photo sync disabled in configuration",
        )

    # Create sync log only for real runs. Dry-run simulations shouldn't clutter history.
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    log_id = None
    if not request.dry_run and not request.initial_scan:
        log_id = await sync_logs_db.create_log(
            service="photos",
            sync_type="manual",
            status="running",
        )

    # Send initial progress update
    await send_sync_progress(
        service="photos",
        status="running",
        progress=0,
        message="Starting photo sync...",
    )

    start_time = datetime.now().timestamp()

    # Define progress callback for real-time updates
    async def progress_callback(progress: int, message: str) -> None:
        await send_sync_progress(
            service="photos",
            status="running",
            progress=progress,
            message=message,
        )

    try:
        stats = await engine.sync(
            sources=request.sources,
            dry_run=request.dry_run,
            initial_scan=request.initial_scan,
            progress_callback=progress_callback,
        )

        duration = datetime.now().timestamp() - start_time

        if log_id is not None:
            # Update sync log for real runs
            await sync_logs_db.update_log(
                log_id=log_id,
                status="success",
                duration_seconds=duration,
                stats_json=json.dumps(stats),
            )

        # Send success progress update
        await send_sync_progress(
            service="photos",
            status="success",
            progress=100,
            message="Photo sync completed successfully",
            stats=stats,
        )

        return {"message": "photo sync complete", "stats": stats}

    except Exception as exc:
        duration = datetime.now().timestamp() - start_time
        logger.exception("Photo sync failed: %s", exc)

        if log_id is not None:
            # Update sync log with error for real runs
            await sync_logs_db.update_log(
                log_id=log_id,
                status="error",
                duration_seconds=duration,
                error_message=str(exc),
            )

        # Send error progress update
        await send_sync_progress(
            service="photos",
            status="error",
            progress=0,
            message=f"Photo sync failed: {str(exc)}",
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Photo sync failed: {exc}",
        ) from exc


@router.get("/status")
async def get_status(photos_db: PhotosDBDep, config: ConfigDep):
    """Get photo sync status and statistics."""

    if not config.photos.enabled:
        return {
            "enabled": False,
            "message": "Photo sync is disabled",
        }

    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    photos_success_logs = await sync_logs_db.get_logs(service="photos", status="success", limit=1)
    if not photos_success_logs:
        photos_success_logs = await sync_logs_db.get_logs(service="photos", status="completed", limit=1)

    photos_pending_since = None
    last_skipped_existing = 0
    last_imported_count = 0
    if photos_success_logs:
        last_log = photos_success_logs[0]
        photos_pending_since = last_log.get("completed_at") or last_log.get("started_at")
        stats_json = last_log.get("stats_json")
        if stats_json:
            try:
                stats_payload = json.loads(stats_json)
                last_skipped_existing = int(stats_payload.get("skipped_existing", 0) or 0)
                last_imported_count = int(stats_payload.get("imported", 0) or 0)
            except (ValueError, TypeError):
                last_skipped_existing = 0
                last_imported_count = 0

    await photos_db.initialize()
    stats = await photos_db.get_stats(pending_since=photos_pending_since)

    # Get most recent import time
    import aiosqlite

    async with aiosqlite.connect(photos_db.db_path) as db:
        cursor = await db.execute(
            "SELECT MAX(last_imported) FROM photo_assets WHERE last_imported IS NOT NULL"
        )
        last_sync_timestamp = (await cursor.fetchone())[0]

    last_sync = None
    if last_sync_timestamp:
        last_sync = datetime.fromtimestamp(last_sync_timestamp).isoformat()

    return {
        "enabled": True,
        "library_items": stats.get("total_imported", 0),
        "last_imported": last_imported_count,
        "pending": stats.get("pending", 0),
        "last_sync": last_sync,
        "skipped_existing": last_skipped_existing,
        "sources": list(config.photos.sources.keys()) if config.photos.sources else [],
    }


@router.get("/history")
async def get_history(
    config: ConfigDep,
    limit: int = 10,
):
    """Get photo sync history."""

    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    logs = await sync_logs_db.get_logs(service="photos", limit=limit)

    return {"logs": logs}


@router.post("/reset")
async def reset_database(photos_db: PhotosDBDep, config: ConfigDep):
    """Reset photo sync state by clearing the database."""

    if not config.photos.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Photo sync is disabled",
        )

    logger.info("Resetting photos database")

    # Drop and recreate the photos table
    import aiosqlite

    async with aiosqlite.connect(photos_db.db_path) as db:
        await db.execute("DROP TABLE IF EXISTS photo_assets")
        await db.commit()

    # Reinitialize the database
    await photos_db.initialize()

    # Clear sync history for photos service
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()
    await sync_logs_db.clear_service_logs("photos")

    return {
        "status": "success",
        "message": "Photos sync state has been reset",
    }


# =============================================================================
# Export endpoints (Apple Photos → NextCloud)
# =============================================================================


@router.post("/export")
async def export_photos(
    request: PhotoExportRequest,
    config: ConfigDep,
    photos_db: PhotosDBDep,
):
    """Export photos from Apple Photos to local folder.

    Requires sync_mode='export' or 'bidirectional' in config.
    Default behavior: only export photos added after baseline.
    Use full_library=True to export entire library.
    Use dry_run=True to preview without copying files.
    """
    from pathlib import Path

    from icloudbridge.core.photos_export_engine import ExportConfig, PhotoExportEngine

    # Validate config
    if not config.photos.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Photo sync is disabled",
        )

    if config.photos.sync_mode not in ("export", "bidirectional"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Photo export requires sync_mode='export' or 'bidirectional', got '{config.photos.sync_mode}'",
        )

    export_cfg = config.photos.export

    # Determine export folder (defaults to first import source path)
    export_folder = export_cfg.export_folder
    if not export_folder:
        if config.photos.sources:
            first_source = next(iter(config.photos.sources.values()))
            export_folder = first_source.path
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No export folder configured and no import sources available",
            )

    # Create export engine for local file copy
    export_config = ExportConfig(
        export_folder=Path(export_folder),
        organize_by=export_cfg.organize_by,
    )

    engine = PhotoExportEngine(config=export_config, db=photos_db)
    await engine.initialize()

    # Parse since_date if provided
    since_date = None
    if request.since_date:
        try:
            since_date = datetime.fromisoformat(request.since_date)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid since_date format: {request.since_date}. Use ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)",
            )

    # Create sync log only for real runs
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    log_id = None
    if not request.dry_run:
        log_id = await sync_logs_db.create_log(
            service="photos_export",
            sync_type="manual",
            status="running",
        )

    # Send initial progress update
    await send_sync_progress(
        service="photos_export",
        status="running",
        progress=0,
        message="Starting photo export...",
    )

    start_time = datetime.now().timestamp()

    async def progress_callback(progress: int, message: str) -> None:
        await send_sync_progress(
            service="photos_export",
            status="running",
            progress=progress,
            message=message,
        )

    try:
        stats = await engine.export(
            full_library=request.full_library,
            since_date=since_date,
            album_filter=request.album_filter,
            dry_run=request.dry_run,
            progress_callback=progress_callback,
        )

        duration = datetime.now().timestamp() - start_time

        if log_id is not None:
            await sync_logs_db.update_log(
                log_id=log_id,
                status="success",
                duration_seconds=duration,
                stats_json=json.dumps(stats),
            )

        await send_sync_progress(
            service="photos_export",
            status="success",
            progress=100,
            message="Photo export completed successfully",
            stats=stats,
        )

        return {"message": "photo export complete", "stats": stats}

    except Exception as exc:
        duration = datetime.now().timestamp() - start_time
        logger.exception("Photo export failed: %s", exc)

        if log_id is not None:
            await sync_logs_db.update_log(
                log_id=log_id,
                status="error",
                duration_seconds=duration,
                error_message=str(exc),
            )

        await send_sync_progress(
            service="photos_export",
            status="error",
            progress=0,
            message=f"Photo export failed: {str(exc)}",
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Photo export failed: {exc}",
        ) from exc

    finally:
        await engine.cleanup()


@router.get("/export/status")
async def get_export_status(photos_db: PhotosDBDep, config: ConfigDep):
    """Get photo export status and statistics."""
    if not config.photos.enabled:
        return {
            "enabled": False,
            "message": "Photo sync is disabled",
        }

    if config.photos.sync_mode not in ("export", "bidirectional"):
        return {
            "enabled": False,
            "message": f"Export requires sync_mode='export' or 'bidirectional', got '{config.photos.sync_mode}'",
        }

    # Determine export folder for display
    export_cfg = config.photos.export
    export_folder = export_cfg.export_folder
    if not export_folder and config.photos.sources:
        first_source = next(iter(config.photos.sources.values()))
        export_folder = first_source.path

    # Get export stats
    export_stats = await photos_db.get_export_stats()
    export_state = await photos_db.get_export_state()

    baseline_date = None
    last_export = None
    if export_state:
        if export_state.get("baseline_date"):
            baseline_date = datetime.fromtimestamp(export_state["baseline_date"]).isoformat()
        if export_state.get("last_export"):
            last_export = datetime.fromtimestamp(export_state["last_export"]).isoformat()

    return {
        "enabled": True,
        "sync_mode": config.photos.sync_mode,
        "export_mode": config.photos.export_mode,
        "export_folder": str(export_folder) if export_folder else None,
        "organize_by": export_cfg.organize_by,
        "total_exported": export_stats.get("total_exported", 0),
        "baseline_date": baseline_date,
        "last_export": last_export,
    }


@router.get("/library/albums")
async def list_library_albums(config: ConfigDep, photos_db: PhotosDBDep):
    """List albums in Apple Photos library."""
    from icloudbridge.sources.photos import PhotosLibraryReader

    if not config.photos.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Photo sync is disabled",
        )

    reader = PhotosLibraryReader()
    try:
        albums = await reader.list_albums()
        return {
            "albums": [
                {"uuid": a.uuid, "name": a.name, "count": a.asset_count}
                for a in albums
            ]
        }
    finally:
        reader.cleanup()


@router.get("/library/stats")
async def get_library_stats(config: ConfigDep, photos_db: PhotosDBDep):
    """Get statistics about the Apple Photos library."""
    from icloudbridge.sources.photos import PhotosLibraryReader

    if not config.photos.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Photo sync is disabled",
        )

    reader = PhotosLibraryReader()
    try:
        stats = await reader.get_library_stats()
        export_state = await photos_db.get_export_state()

        baseline_date = None
        if export_state and export_state.get("baseline_date"):
            baseline_date = datetime.fromtimestamp(export_state["baseline_date"]).isoformat()

        return {
            **stats,
            "baseline_date": baseline_date,
        }
    finally:
        reader.cleanup()


@router.post("/export/set-baseline")
async def set_export_baseline(config: ConfigDep, photos_db: PhotosDBDep):
    """Set the export baseline to now.

    Photos before this date won't be exported (unless full_library=True).
    """
    if not config.photos.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Photo sync is disabled",
        )

    await photos_db.set_export_baseline()

    export_state = await photos_db.get_export_state()
    baseline_date = None
    if export_state and export_state.get("baseline_date"):
        baseline_date = datetime.fromtimestamp(export_state["baseline_date"]).isoformat()

    return {
        "status": "success",
        "message": "Export baseline set to current time",
        "baseline_date": baseline_date,
    }


@router.get("/export/history")
async def get_export_history(config: ConfigDep, limit: int = 10):
    """Get photo export history."""
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    logs = await sync_logs_db.get_logs(service="photos_export", limit=limit)
    return {"logs": logs}
