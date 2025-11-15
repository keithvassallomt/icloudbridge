"""Notes synchronization endpoints."""

import asyncio
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


def build_notes_sync_message(stats: dict | None) -> str:
    """Create a human readable summary for sync statistics."""
    if not stats:
        return "Sync operation completed"

    def combined(*keys: str) -> int:
        return sum(int(stats.get(key, 0) or 0) for key in keys)

    created = stats.get("created")
    if created is None:
        created = combined("created_local", "created_remote")

    updated = stats.get("updated")
    if updated is None:
        updated = combined("updated_local", "updated_remote")

    deleted = stats.get("deleted")
    if deleted is None:
        deleted = combined("deleted_local", "deleted_remote")

    pending_notes = stats.get("pending_local_notes")
    if isinstance(pending_notes, list):
        pending_count = len(pending_notes)
    else:
        pending_count = 0

    msg_parts: list[str] = []
    if created:
        msg_parts.append(f"created {created}")
    if updated:
        msg_parts.append(f"updated {updated}")
    if deleted:
        msg_parts.append(f"deleted {deleted}")

    if msg_parts:
        message = f"Synced: {', '.join(msg_parts)} note(s)"
    elif pending_count:
        message = "Pending edits detected"
    else:
        message = "Synced, no changes needed"

    errors = int(stats.get("errors", 0) or 0)
    if errors:
        plural = "s" if errors != 1 else ""
        message += f" (⚠️ {errors} folder error{plural} encountered)"

    if pending_count:
        plural = "s" if pending_count != 1 else ""
        message += f" (⚠️ {pending_count} note{plural} appears to be mid-edit)"

    return message


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


@router.get("/folders/all")
async def get_all_folders(engine: NotesSyncEngineDep):
    """Get all folders from both Apple Notes and Markdown sources.

    Returns hierarchical folder information with existence indicators.
    Useful for UI that needs to show which folders exist where.

    Returns:
        Dictionary mapping folder paths to source indicators:
        {
            "Work": {"apple": True, "markdown": True},
            "Work/Projects": {"apple": True, "markdown": False},
            "Personal": {"apple": True, "markdown": True},
            "Configs": {"apple": False, "markdown": True}
        }
    """
    try:
        folders_info = await engine.get_all_folders()
        return {"folders": folders_info}
    except Exception as e:
        logger.error(f"Failed to get all folders: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get all folders: {str(e)}"
        )


@router.post("/sync")
async def sync_notes(
    request: NotesSyncRequest,
    config: ConfigDep,
):
    """Trigger notes synchronization.

    Args:
        request: Sync configuration options
            - folder: Specific folder to sync, or None to sync all folders
            - dry_run: Preview changes without applying
            - skip_deletions: Skip all deletion operations
            - deletion_threshold: Max deletions before confirmation (default: 5)
            - rich_notes_export: Export read-only rich notes snapshot after sync
            - use_shortcuts: Override shortcut pipeline preference (None = use config)

    Returns:
        Sync results with statistics
    """
    # Create sync engine with optional shortcut pipeline override
    from icloudbridge.core.sync import NotesSyncEngine

    config.ensure_data_dir()
    db_path = config.general.data_dir / "notes.db"
    markdown_base_path = config.notes.remote_folder

    if not markdown_base_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Notes remote_folder not configured"
        )

    # Use per-request override if provided, otherwise fall back to config
    prefer_shortcuts = request.use_shortcuts if request.use_shortcuts is not None else True

    engine = NotesSyncEngine(markdown_base_path, db_path, prefer_shortcuts=prefer_shortcuts)
    await engine.initialize()

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
        # Handle all-folders sync vs single folder sync
        if request.folder:
            # Single folder sync
            result = await engine.sync_folder(
                folder_name=request.folder,
                markdown_subfolder=None,
                dry_run=request.dry_run,
                skip_deletions=request.skip_deletions,
                deletion_threshold=request.deletion_threshold,
                sync_mode=request.mode,
            )
        else:
            # All-folders sync
            # Check if folder mappings are configured
            if config.notes.folder_mappings:
                # Use selective sync with mappings
                logger.info(f"Using folder mappings for selective sync ({len(config.notes.folder_mappings)} mappings)")

                # Convert FolderMapping objects to dict format expected by sync_with_mappings
                folder_mappings_dict = {}
                for apple_folder, mapping_obj in config.notes.folder_mappings.items():
                    folder_mappings_dict[apple_folder] = {
                        "markdown_folder": mapping_obj.markdown_folder,
                        "mode": mapping_obj.mode
                    }

                folder_results = await engine.sync_with_mappings(
                    folder_mappings=folder_mappings_dict,
                    dry_run=request.dry_run,
                    skip_deletions=request.skip_deletions,
                    deletion_threshold=request.deletion_threshold,
                )

                # Convert results to match expected format
                total_stats = {
                    "created": 0,
                    "updated": 0,
                    "deleted": 0,
                    "unchanged": 0,
                    "errors": 0,
                    "pending_local_notes": [],
                }
                formatted_results = []

                for folder_name, stats in folder_results.items():
                    if "error" in stats:
                        total_stats["errors"] += 1
                        formatted_results.append({
                            "folder": folder_name,
                            "status": "error",
                            "error": stats["error"]
                        })
                    else:
                        total_stats["created"] += stats.get("created_local", 0) + stats.get("created_remote", 0)
                        total_stats["updated"] += stats.get("updated_local", 0) + stats.get("updated_remote", 0)
                        total_stats["deleted"] += stats.get("deleted_local", 0) + stats.get("deleted_remote", 0)
                        total_stats["unchanged"] += stats.get("unchanged", 0)
                        if stats.get("pending_local_notes"):
                            total_stats["pending_local_notes"].extend(stats["pending_local_notes"])
                        formatted_results.append({
                            "folder": folder_name,
                            "status": "success",
                            "stats": stats
                        })

                result = total_stats.copy()
                result["pending_local_notes"] = list(total_stats.get("pending_local_notes", []))
                result["folder_count"] = len(folder_results)
                result["folder_results"] = formatted_results
                result["mapping_mode"] = True

            else:
                # Auto 1:1 sync for all folders
                logger.info("Using automatic 1:1 folder sync")
                folders = await engine.list_folders()

                # Initialize aggregated statistics
                total_stats = {
                    "created": 0,
                    "updated": 0,
                    "deleted": 0,
                    "unchanged": 0,
                    "errors": 0,
                    "pending_local_notes": [],
                }
                folder_results = []

                for folder_info in folders:
                    folder_name = folder_info["name"]
                    try:
                        folder_result = await engine.sync_folder(
                            folder_name=folder_name,
                            markdown_subfolder=None,
                            dry_run=request.dry_run,
                            skip_deletions=request.skip_deletions,
                            deletion_threshold=request.deletion_threshold,
                            sync_mode=request.mode,
                        )

                        # Aggregate statistics
                        total_stats["created"] += folder_result.get("created", 0)
                        total_stats["updated"] += folder_result.get("updated", 0)
                        total_stats["deleted"] += folder_result.get("deleted", 0)
                        total_stats["unchanged"] += folder_result.get("unchanged", 0)
                        if folder_result.get("pending_local_notes"):
                            total_stats["pending_local_notes"].extend(folder_result["pending_local_notes"])

                        folder_results.append({
                            "folder": folder_name,
                            "status": "success",
                            "stats": folder_result
                        })
                    except Exception as e:
                        total_stats["errors"] += 1
                        folder_results.append({
                            "folder": folder_name,
                            "status": "error",
                            "error": str(e)
                        })
                        logger.error(f"Failed to sync folder {folder_name}: {e}")

                # Create aggregated result for automatic mode
                result = total_stats.copy()
                result["pending_local_notes"] = list(total_stats.get("pending_local_notes", []))
                result["folder_count"] = len(folders)
                result["folder_results"] = folder_results

        # Handle rich notes export if requested (NEW)
        if request.rich_notes_export and not request.dry_run:
            try:
                from icloudbridge.sources.notes.rich_notes_exporter import RichNotesExporter
                exporter = RichNotesExporter(
                    db_path=db_path,
                    remote_folder=markdown_base_path
                )
                await asyncio.to_thread(exporter.export, dry_run=False)
                logger.info("Rich notes exported to RichNotes/ folder")
                if "metadata" not in result:
                    result["metadata"] = {}
                result["metadata"]["rich_notes_exported"] = True
            except Exception as e:
                logger.error(f"Rich notes export failed: {e}")
                if "metadata" not in result:
                    result["metadata"] = {}
                result["metadata"]["rich_notes_export_error"] = str(e)

        duration = time.time() - start_time

        # Update sync log with success (only if not dry run)
        if sync_logs_db and log_id:
            await sync_logs_db.update_log(
                log_id=log_id,
                status="completed",
                duration_seconds=round(duration, 0),
                stats_json=json.dumps(result),
            )

        # Add pipeline info to metadata
        if "metadata" not in result:
            result["metadata"] = {}
        result["metadata"]["pipeline_used"] = "shortcuts" if prefer_shortcuts else "classic_applescript"

        response = {
            "status": "success",
            "message": build_notes_sync_message(result),
            "duration_seconds": duration,
            "stats": result,
            "log_id": log_id,
        }

        return response

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
        else:
            message = build_notes_sync_message(sync_stats)

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
        else:
            message = build_notes_sync_message(stats)

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
async def reset_database(notes_db: NotesDBDep, engine: NotesSyncEngineDep, config: ConfigDep):
    """Reset notes sync database and history.

    Clears all note mappings from the database and deletes sync history.
    This will cause all notes to be re-synced on the next sync operation.

    Returns:
        Success message
    """
    try:
        # Reset notes database
        await engine.reset_database()
        logger.info("Notes database reset successfully")

        # Clear sync history for notes service
        sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
        await sync_logs_db.initialize()
        await sync_logs_db.clear_service_logs("notes")
        logger.info("Notes sync history cleared")

        # Clear manual folder mappings so UI returns to auto mode
        had_manual_mappings = bool(config.notes.folder_mappings)
        if had_manual_mappings:
            config.notes.folder_mappings.clear()
            config.notes.folder_mappings = {}
            config_path = getattr(config.general, "config_file", None)
            if config_path is None:
                config_path = config.default_config_path
            try:
                config.save_to_file(config_path)
                from icloudbridge.api.dependencies import get_config
                get_config.cache_clear()
                logger.info("Cleared notes folder mappings during reset")
            except Exception as e:
                logger.warning(f"Failed to persist cleared folder mappings: {e}")

        return {
            "status": "success",
            "message": "Notes database and history reset successfully. All mappings and sync logs cleared.",
        }
    except Exception as e:
        logger.error(f"Failed to reset notes: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset notes: {str(e)}"
        )
