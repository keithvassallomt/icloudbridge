"""Reminders synchronization endpoints."""

import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, status

from icloudbridge.api.dependencies import ConfigDep, RemindersDBDep, RemindersSyncEngineDep
from icloudbridge.api.models import RemindersSyncRequest
from icloudbridge.utils.credentials import CredentialStore
from icloudbridge.utils.datetime_utils import safe_fromtimestamp
from icloudbridge.utils.db import SyncLogsDB

logger = logging.getLogger(__name__)

router = APIRouter()


def _reminder_stats_message(stats: dict) -> str:
    """Generate a human-readable summary for reminder sync stats."""

    calendars_count = stats.get("calendars_synced", 0)
    total_created = stats.get("total_created", 0)
    total_updated = stats.get("total_updated", 0)
    total_deleted = stats.get("total_deleted", 0)
    total_changes = total_created + total_updated + total_deleted

    if total_changes == 0:
        return "No changes detected"

    msg_parts = []
    if total_created > 0:
        msg_parts.append(f"created {total_created}")
    if total_updated > 0:
        msg_parts.append(f"updated {total_updated}")
    if total_deleted > 0:
        msg_parts.append(f"deleted {total_deleted}")

    details = ", ".join(msg_parts)
    return f"Synced {calendars_count} calendar(s): {details} reminder(s)"


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
            ssl_verify_cert=config.reminders.caldav_ssl_verify_cert,
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
    # Create sync log entry ONLY if not a dry run
    log_id = None
    sync_logs_db = None
    if not request.dry_run:
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

            # Aggregate stats from per-calendar results
            total_errors = 0
            total_created = 0
            total_updated = 0
            total_deleted = 0
            total_unchanged = 0
            aggregate_error_messages: list[str] = []

            for cal_stats in per_calendar_results.values():
                total_errors += cal_stats.get("errors", 0)
                total_created += cal_stats.get("created_remote", 0) + cal_stats.get("created_local", 0)
                total_updated += cal_stats.get("updated_remote", 0) + cal_stats.get("updated_local", 0)
                total_deleted += cal_stats.get("deleted_remote", 0) + cal_stats.get("deleted_local", 0)
                total_unchanged += cal_stats.get("unchanged", 0)
                aggregate_error_messages.extend(cal_stats.get("error_messages", []))

            # Return per-calendar stats with aggregated totals
            result = {
                "calendars_synced": len(per_calendar_results),
                "per_calendar": per_calendar_results,  # Keep detailed breakdown
                "total_errors": total_errors,
                "total_created": total_created,
                "total_updated": total_updated,
                "total_deleted": total_deleted,
                "total_unchanged": total_unchanged,
                "error_messages": aggregate_error_messages,
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
                "total_errors": 0,
                "error_messages": [],
                "per_calendar": {},
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
                    all_stats["total_created"] += result.get("created_local", 0) + result.get("created_remote", 0)
                    all_stats["total_updated"] += result.get("updated_local", 0) + result.get("updated_remote", 0)
                    all_stats["total_deleted"] += result.get("deleted_local", 0) + result.get("deleted_remote", 0)
                    all_stats["total_unchanged"] += result.get("unchanged", 0)
                    all_stats["total_errors"] += result.get("errors", 0)
                    if result.get("error_messages"):
                        all_stats["error_messages"].extend(result["error_messages"])
                    all_stats["per_calendar"][f"{apple_calendar} → {caldav_calendar}"] = result

                except Exception as e:
                    logger.error(f"Failed to sync {apple_calendar} → {caldav_calendar}: {e}")
                    all_stats["total_errors"] += 1
                    all_stats["error_messages"].append(str(e))
                    # Continue with other calendars even if one fails

            result = all_stats

        duration = time.time() - start_time

        # Determine sync status based on errors
        total_errors = result.get("total_errors", 0)
        if total_errors > 0:
            # Check if there were any successful operations
            successful_ops = (
                result.get("total_created", 0) +
                result.get("total_updated", 0) +
                result.get("total_deleted", 0)
            )
            sync_status = "partial_success" if successful_ops > 0 else "failed"
        else:
            sync_status = "completed"

        # Update sync log (only if not dry run)
        if sync_logs_db and log_id:
            await sync_logs_db.update_log(
                log_id=log_id,
                status=sync_status,
                duration_seconds=round(duration, 0),
                stats_json=json.dumps(result),
            )

        # Create a descriptive message based on the sync results
        calendars_count = result.get("calendars_synced", 0)

        if any(key in result for key in ("total_created", "total_updated", "total_deleted")):
            base_message = _reminder_stats_message(result)
        else:
            base_message = f"Synced {calendars_count} calendar(s)"

        if total_errors > 0:
            base_message += f" (⚠️ {total_errors} error(s) occurred)"

        message = base_message

        # Determine overall status for API response
        api_status = "success" if total_errors == 0 else "partial_success" if sync_status == "partial_success" else "error"

        return {
            "status": api_status,
            "message": message,
            "duration_seconds": duration,
            "stats": result,
        }

    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)

        logger.error(f"Reminders sync failed: {error_msg}")

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
            if any(key in sync_stats for key in ("total_created", "total_updated", "total_deleted")):
                message = _reminder_stats_message(sync_stats)
            else:
                calendars_count = sync_stats.get("calendars_synced", 0)
                message = f"Synced {calendars_count} calendar(s)"
        else:
            message = "Sync operation completed"

        # Convert timestamps to ISO strings
        started_at = safe_fromtimestamp(log.get("started_at"))
        started_at = started_at.isoformat() if started_at else None
        completed_at = safe_fromtimestamp(log.get("completed_at"))
        completed_at = completed_at.isoformat() if completed_at else None

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
        "last_sync": last_sync,
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
            if any(key in stats for key in ("total_created", "total_updated", "total_deleted")):
                message = _reminder_stats_message(stats)
            else:
                calendars_count = stats.get("calendars_synced", 0)
                message = f"Synced {calendars_count} calendar(s)"
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
async def reset_database(engine: RemindersSyncEngineDep, config: ConfigDep):
    """Reset reminders sync database, history, and keychain password.

    Clears all reminder mappings from the database, deletes sync history,
    and removes CalDAV password from keychain. This will cause all reminders
    to be re-synced on the next sync operation.

    Returns:
        Success message
    """
    try:
        # Reset reminders database
        await engine.reset_database()
        logger.info("Reminders database reset successfully")

        # Clear sync history for reminders service
        sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
        await sync_logs_db.initialize()
        await sync_logs_db.clear_service_logs("reminders")
        logger.info("Reminders sync history cleared")

        # Delete CalDAV password from keychain if username exists
        if config.reminders.caldav_username:
            try:
                credential_store = CredentialStore()
                credential_store.delete_caldav_password(config.reminders.caldav_username)
                logger.info(f"Deleted CalDAV password for: {config.reminders.caldav_username}")
            except Exception as e:
                logger.warning(f"Failed to delete CalDAV password: {e}")

        return {
            "status": "success",
            "message": "Reminders database, history, and keychain password reset successfully.",
        }
    except Exception as e:
        logger.error(f"Failed to reset reminders: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset reminders: {str(e)}"
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
