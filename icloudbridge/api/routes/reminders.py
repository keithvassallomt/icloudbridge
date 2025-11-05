"""Reminders synchronization endpoints."""

import json
import logging
import time

from fastapi import APIRouter, HTTPException, status

from icloudbridge.api.dependencies import ConfigDep, RemindersDBDep, RemindersSyncEngineDep
from icloudbridge.api.models import RemindersSyncRequest
from icloudbridge.utils.credentials import CredentialStore
from icloudbridge.utils.db import SyncLogsDB

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/calendars")
async def list_calendars(engine: RemindersSyncEngineDep):
    """List all Apple Reminders lists.

    Returns:
        List of reminder list names with reminder counts
    """
    try:
        # Get Apple Reminders lists
        from icloudbridge.sources.reminders.eventkit import RemindersAdapter

        adapter = RemindersAdapter()
        await adapter.request_access()
        calendars = await adapter.list_calendars()  # Fixed: added await

        # Count reminders for each list
        result = []
        for cal in calendars:
            reminders = await adapter.get_reminders(calendar_id=cal.uuid)
            result.append({
                "name": cal.title,  # Fixed: use 'name' to match frontend
                "reminder_count": len(reminders),  # Fixed: actually count reminders
            })

        return {"calendars": result}
    except Exception as e:
        logger.error(f"Failed to list reminder lists: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list reminder lists: {str(e)}"
        )


@router.get("/caldav-calendars")
async def list_caldav_calendars(config: ConfigDep):
    """List all CalDAV calendars available on the configured server.

    Returns:
        List of CalDAV calendar names
    """
    try:
        from icloudbridge.sources.reminders.caldav_adapter import CalDAVAdapter

        # Get CalDAV credentials
        caldav_password = config.reminders.get_caldav_password()
        if not caldav_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="CalDAV credentials not configured. Please set up credentials first."
            )

        # Connect to CalDAV server
        adapter = CalDAVAdapter(
            config.reminders.caldav_url,
            config.reminders.caldav_username,
            caldav_password,
        )
        await adapter.connect()
        calendars = await adapter.list_calendars()

        # Return just the calendar names for autocomplete
        return {"calendars": [cal["name"] for cal in calendars]}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list CalDAV calendars: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list CalDAV calendars: {str(e)}"
        )


@router.post("/sync")
async def sync_reminders(
    request: RemindersSyncRequest,
    engine: RemindersSyncEngineDep,
    config: ConfigDep,
):
    """Trigger reminders synchronization.

    Args:
        request: Sync configuration options

    Returns:
        Sync results with statistics
    """
    # Create sync log entry
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    log_id = await sync_logs_db.create_log(
        service="reminders",
        sync_type="manual",
        status="running",
    )

    start_time = time.time()

    try:
        # Perform sync based on mode
        if request.auto:
            # Auto mode - sync all calendars
            per_calendar_results = await engine.discover_and_sync_all(
                base_mappings=config.reminders.calendar_mappings,
                dry_run=request.dry_run,
                skip_deletions=request.skip_deletions,
                deletion_threshold=request.deletion_threshold,
            )

            # Return per-calendar stats with details
            result = {
                "calendars_synced": len(per_calendar_results),
                "per_calendar": per_calendar_results,  # Keep detailed breakdown
            }

        else:
            # Manual mode - sync calendars based on saved mappings
            mappings = config.reminders.calendar_mappings or {}

            if not mappings:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="No calendar mappings configured for manual mode. Please configure mappings first."
                )

            # Sync each mapped calendar pair
            all_stats = {
                "calendars_synced": 0,
                "total_created": 0,
                "total_updated": 0,
                "total_deleted": 0,
                "total_unchanged": 0,
            }

            for apple_calendar, caldav_calendar in mappings.items():
                try:
                    result = await engine.sync_calendar(
                        apple_calendar_name=apple_calendar,
                        caldav_calendar_name=caldav_calendar,
                        dry_run=request.dry_run,
                        skip_deletions=request.skip_deletions,
                        deletion_threshold=request.deletion_threshold,
                    )

                    # Aggregate stats
                    all_stats["calendars_synced"] += 1
                    all_stats["total_created"] += result.get("created", 0)
                    all_stats["total_updated"] += result.get("updated", 0)
                    all_stats["total_deleted"] += result.get("deleted", 0)
                    all_stats["total_unchanged"] += result.get("unchanged", 0)

                except Exception as e:
                    logger.error(f"Failed to sync {apple_calendar} → {caldav_calendar}: {e}")
                    # Continue with other calendars even if one fails

            result = all_stats

        duration = time.time() - start_time

        # Update sync log with success
        await sync_logs_db.update_log(
            log_id=log_id,
            status="success",
            duration_seconds=duration,
            stats_json=json.dumps(result),
        )

        # Create a descriptive message based on the sync results
        calendars_count = result.get("calendars_synced", 0)

        # Build message with statistics (handle both auto and manual mode results)
        if "total_created" in result:
            # Manual mode with aggregated stats
            msg_parts = []
            if result.get("total_created", 0) > 0:
                msg_parts.append(f"created {result['total_created']}")
            if result.get("total_updated", 0) > 0:
                msg_parts.append(f"updated {result['total_updated']}")
            if result.get("total_deleted", 0) > 0:
                msg_parts.append(f"deleted {result['total_deleted']}")

            if msg_parts:
                message = f"Successfully synced {calendars_count} calendar(s): {', '.join(msg_parts)} reminder(s)"
            else:
                message = f"Successfully synced {calendars_count} calendar(s), no changes needed"
        else:
            # Auto mode or simple result
            message = f"Successfully synced {calendars_count} calendar(s)"

        return {
            "status": "success",
            "message": message,
            "duration_seconds": duration,
            "stats": result,
        }

    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)

        logger.error(f"Reminders sync failed: {error_msg}")

        # Update sync log with error
        await sync_logs_db.update_log(
            log_id=log_id,
            status="error",
            duration_seconds=duration,
            error_message=error_msg,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync failed: {error_msg}"
        )


@router.get("/status")
async def get_status(reminders_db: RemindersDBDep, config: ConfigDep):
    """Get reminders sync status.

    Returns:
        Status information including last sync and mapping count
    """
    stats = await reminders_db.get_stats()

    # Get last sync from logs
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()
    logs = await sync_logs_db.get_logs(service="reminders", limit=1)

    # Check if password is available
    credential_store = CredentialStore()
    has_password = credential_store.has_caldav_password(config.reminders.caldav_username or "")

    return {
        "enabled": config.reminders.enabled,
        "caldav_url": config.reminders.caldav_url,
        "caldav_username": config.reminders.caldav_username,
        "has_password": has_password,
        "sync_mode": config.reminders.sync_mode,
        "total_mappings": stats.get("total", 0),
        "last_sync": logs[0] if logs else None,
    }


@router.get("/history")
async def get_history(
    config: ConfigDep,
    limit: int = 10,
    offset: int = 0,
):
    """Get reminders sync history.

    Args:
        limit: Maximum number of logs to return
        offset: Number of logs to skip

    Returns:
        List of sync log entries
    """
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    logs = await sync_logs_db.get_logs(
        service="reminders",
        limit=limit,
        offset=offset,
    )

    return {
        "logs": logs,
        "limit": limit,
        "offset": offset,
    }


@router.post("/reset")
async def reset_database(engine: RemindersSyncEngineDep):
    """Reset reminders sync database.

    Clears all reminder mappings from the database. This will cause
    all reminders to be re-synced on the next sync operation.

    Returns:
        Success message
    """
    try:
        await engine.reset_database()
        logger.info("Reminders database reset successfully")

        return {
            "status": "success",
            "message": "Reminders database reset successfully. All mappings cleared.",
        }
    except Exception as e:
        logger.error(f"Failed to reset reminders database: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset database: {str(e)}"
        )


@router.post("/password")
async def set_password(username: str, password: str):
    """Store CalDAV password in system keyring.

    Args:
        username: CalDAV username
        password: CalDAV password

    Returns:
        Success message
    """
    try:
        credential_store = CredentialStore()
        credential_store.set_caldav_password(username, password)

        logger.info(f"CalDAV password stored for user: {username}")

        return {
            "status": "success",
            "message": f"Password stored securely for {username}",
        }
    except Exception as e:
        logger.error(f"Failed to store CalDAV password: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store password: {str(e)}"
        )


@router.delete("/password")
async def delete_password(username: str):
    """Delete CalDAV password from system keyring.

    Args:
        username: CalDAV username

    Returns:
        Success message
    """
    try:
        credential_store = CredentialStore()
        credential_store.delete_caldav_password(username)

        logger.info(f"CalDAV password deleted for user: {username}")

        return {
            "status": "success",
            "message": f"Password deleted for {username}",
        }
    except Exception as e:
        logger.error(f"Failed to delete CalDAV password: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete password: {str(e)}"
        )
