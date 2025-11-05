"""Notes synchronization endpoints."""

import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, status

from icloudbridge.api.dependencies import ConfigDep, NotesDBDep, NotesSyncEngineDep
from icloudbridge.api.models import NotesSyncRequest
from icloudbridge.utils.db import SyncLogsDB

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/folders")
async def list_folders(engine: NotesSyncEngineDep):
    """List all Apple Notes folders.

    Returns:
        List of folder names with note counts
    """
    try:
        folders = await engine.list_folders()
        return {
            "folders": [
                {
                    "name": folder["name"],
                    "uuid": folder.get("uuid", ""),
                    "note_count": folder.get("note_count", 0),
                }
                for folder in folders
            ]
        }
    except Exception as e:
        logger.error(f"Failed to list folders: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list folders: {str(e)}"
        )


@router.post("/sync")
async def sync_notes(
    request: NotesSyncRequest,
    engine: NotesSyncEngineDep,
    config: ConfigDep,
):
    """Trigger notes synchronization.

    Args:
        request: Sync configuration options

    Returns:
        Sync results with statistics
    """
    # Create sync log entry ONLY if not a dry run
    log_id = None
    sync_logs_db = None
    if not request.dry_run:
        sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
        await sync_logs_db.initialize()

        log_id = await sync_logs_db.create_log(
            service="notes",
            sync_type="manual",
            status="running",
        )

    start_time = time.time()

    try:
        # Perform sync
        result = await engine.sync_folder(
            folder_name=request.folder,
            markdown_subfolder=None,
            dry_run=request.dry_run,
            skip_deletions=request.skip_deletions,
            deletion_threshold=request.deletion_threshold,
        )

        duration = time.time() - start_time

        # Update sync log with success (only if not dry run)
        if sync_logs_db and log_id:
            await sync_logs_db.update_log(
                log_id=log_id,
                status="completed",
                duration_seconds=round(duration, 0),
                stats_json=json.dumps(result),
            )

        return {
            "status": "success",
            "duration_seconds": duration,
            "stats": result,
        }

    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)

        logger.error(f"Notes sync failed: {error_msg}")

        # Update sync log with error (only if not dry run)
        if sync_logs_db and log_id:
            await sync_logs_db.update_log(
                log_id=log_id,
                status="failed",
                duration_seconds=round(duration, 0),
                error_message=error_msg,
            )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync failed: {error_msg}"
        )


@router.get("/status")
async def get_status(notes_db: NotesDBDep, config: ConfigDep):
    """Get notes sync status.

    Returns:
        Status information including last sync and mapping count
    """
    stats = await notes_db.get_stats()

    # Get last sync from logs
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()
    logs = await sync_logs_db.get_logs(service="notes", limit=1)

    # Transform last sync log to match frontend expectations
    last_sync = None
    if logs:
        log = logs[0]
        sync_stats = {}
        if log.get("stats_json"):
            try:
                sync_stats = json.loads(log["stats_json"])
            except json.JSONDecodeError:
                pass

        # Build message
        message = ""
        if log["status"] == "failed":
            message = log.get("error_message", "Sync failed")
        elif sync_stats:
            msg_parts = []
            if sync_stats.get("created", 0) > 0:
                msg_parts.append(f"created {sync_stats['created']}")
            if sync_stats.get("updated", 0) > 0:
                msg_parts.append(f"updated {sync_stats['updated']}")
            if sync_stats.get("deleted", 0) > 0:
                msg_parts.append(f"deleted {sync_stats['deleted']}")

            if msg_parts:
                message = f"Synced: {', '.join(msg_parts)} note(s)"
            else:
                message = "Synced, no changes needed"
        else:
            message = "Sync operation completed"

        # Convert timestamps to ISO strings
        started_at = datetime.fromtimestamp(log["started_at"]).isoformat() if log.get("started_at") else None
        completed_at = datetime.fromtimestamp(log["completed_at"]).isoformat() if log.get("completed_at") else None

        last_sync = {
            "id": log["id"],
            "service": log["service"],
            "operation": log["sync_type"],
            "status": log["status"],
            "message": message,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": log.get("duration_seconds"),
            "stats": sync_stats,
            "error_message": log.get("error_message"),
        }

    return {
        "enabled": config.notes.enabled,
        "remote_folder": str(config.notes.remote_folder) if config.notes.remote_folder else None,
        "total_mappings": stats.get("total", 0),
        "last_sync": last_sync,
    }


@router.get("/history")
async def get_history(
    config: ConfigDep,
    limit: int = 10,
    offset: int = 0,
):
    """Get notes sync history.

    Args:
        limit: Maximum number of logs to return
        offset: Number of logs to skip

    Returns:
        List of sync log entries
    """
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    logs = await sync_logs_db.get_logs(
        service="notes",
        limit=limit,
        offset=offset,
    )

    # Transform logs to match frontend expectations
    transformed_logs = []
    for log in logs:
        # Parse stats from JSON
        stats = {}
        if log.get("stats_json"):
            try:
                stats = json.loads(log["stats_json"])
            except json.JSONDecodeError:
                pass

        # Build descriptive message from stats
        message = ""
        if log["status"] == "failed":
            message = log.get("error_message", "Sync failed")
        elif stats:
            msg_parts = []
            if stats.get("created", 0) > 0:
                msg_parts.append(f"created {stats['created']}")
            if stats.get("updated", 0) > 0:
                msg_parts.append(f"updated {stats['updated']}")
            if stats.get("deleted", 0) > 0:
                msg_parts.append(f"deleted {stats['deleted']}")

            if msg_parts:
                message = f"Synced: {', '.join(msg_parts)} note(s)"
            else:
                message = "Synced, no changes needed"
        else:
            message = "Sync operation completed"

        # Convert Unix timestamps (seconds) to ISO strings
        started_at = datetime.fromtimestamp(log["started_at"]).isoformat() if log.get("started_at") else None
        completed_at = datetime.fromtimestamp(log["completed_at"]).isoformat() if log.get("completed_at") else None

        transformed_logs.append({
            "id": log["id"],
            "service": log["service"],
            "operation": log["sync_type"],
            "status": log["status"],
            "message": message,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": log.get("duration_seconds"),
            "stats": stats,
            "error_message": log.get("error_message"),
        })

    return {
        "logs": transformed_logs,
        "limit": limit,
        "offset": offset,
    }


@router.post("/reset")
async def reset_database(notes_db: NotesDBDep, engine: NotesSyncEngineDep):
    """Reset notes sync database.

    Clears all note mappings from the database. This will cause
    all notes to be re-synced on the next sync operation.

    Returns:
        Success message
    """
    try:
        await engine.reset_database()
        logger.info("Notes database reset successfully")

        return {
            "status": "success",
            "message": "Notes database reset successfully. All mappings cleared.",
        }
    except Exception as e:
        logger.error(f"Failed to reset notes database: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset database: {str(e)}"
        )
