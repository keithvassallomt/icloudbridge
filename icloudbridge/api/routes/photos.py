"""Photo synchronization endpoints."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, status

from icloudbridge.api.dependencies import ConfigDep, PhotosDBDep, PhotosSyncEngineDep
from icloudbridge.api.models import PhotoSyncRequest
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

    # Create sync log
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

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

        # Update sync log
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

        # Update sync log with error
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

    # Get statistics from the database
    import aiosqlite

    async with aiosqlite.connect(photos_db.db_path) as db:
        # Count total imported assets
        cursor = await db.execute("SELECT COUNT(*) FROM photo_assets WHERE last_imported IS NOT NULL")
        total_imported = (await cursor.fetchone())[0]

        # Count new assets discovered but not imported
        cursor = await db.execute("SELECT COUNT(*) FROM photo_assets WHERE last_imported IS NULL")
        pending = (await cursor.fetchone())[0]

        # Get most recent import time
        cursor = await db.execute(
            "SELECT MAX(last_imported) FROM photo_assets WHERE last_imported IS NOT NULL"
        )
        last_sync_timestamp = (await cursor.fetchone())[0]

    last_sync = None
    if last_sync_timestamp:
        last_sync = datetime.fromtimestamp(last_sync_timestamp).isoformat()

    return {
        "enabled": True,
        "total_imported": total_imported,
        "pending": pending,
        "last_sync": last_sync,
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
