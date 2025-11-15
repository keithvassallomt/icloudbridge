"""Health check and status endpoints."""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends

from icloudbridge import __version__
from icloudbridge.api.dependencies import (
    ConfigDep,
    NotesDBDep,
    PasswordsDBDep,
    PhotosDBDep,
    RemindersDBDep,
)
from icloudbridge.api.models import HealthResponse, StatusResponse, VersionResponse
from icloudbridge.utils.db import SchedulesDB, SyncLogsDB

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint.

    Returns basic health status of the API server.
    """
    return HealthResponse(
        status="healthy",
        timestamp=datetime.now().isoformat(),
    )


@router.get("/version", response_model=VersionResponse)
async def get_version():
    """Get version information.

    Returns the current version of iCloudBridge and Python runtime.
    """
    return VersionResponse(
        version=__version__,
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.patch}",
    )


@router.get("/status", response_model=StatusResponse)
async def get_status(
    config: ConfigDep,
    notes_db: NotesDBDep,
    reminders_db: RemindersDBDep,
    passwords_db: PasswordsDBDep,
    photos_db: PhotosDBDep,
):
    """Get overall sync status for all services.

    Returns:
        StatusResponse with status information for each service
    """
    # Get sync + schedule databases
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()
    schedules_db = SchedulesDB(config.general.data_dir / "schedules.db")
    await schedules_db.initialize()

    # Get last sync for each service
    notes_logs = await sync_logs_db.get_logs(service="notes", limit=1)
    reminders_logs = await sync_logs_db.get_logs(service="reminders", limit=1)
    passwords_logs = await sync_logs_db.get_logs(service="passwords", limit=1)
    photos_logs = await sync_logs_db.get_logs(service="photos", limit=1)

    photos_success_logs = await sync_logs_db.get_logs(service="photos", status="success", limit=1)
    if not photos_success_logs:
        photos_success_logs = await sync_logs_db.get_logs(service="photos", status="completed", limit=1)
    photos_pending_since = None
    if photos_success_logs:
        last_log = photos_success_logs[0]
        photos_pending_since = last_log.get("completed_at") or last_log.get("started_at")

    # Transform logs to match frontend expectations
    def transform_log(log):
        if not log:
            return None

        stats = {}
        if log.get("stats_json"):
            try:
                stats = json.loads(log["stats_json"])
            except json.JSONDecodeError:
                pass

        # Build message based on service type
        message = ""
        if log["status"] == "failed":
            message = log.get("error_message", "Sync failed")
        elif stats:
            if log["service"] == "notes":
                msg_parts = []
                if stats.get("created", 0) > 0:
                    msg_parts.append(f"created {stats['created']}")
                if stats.get("updated", 0) > 0:
                    msg_parts.append(f"updated {stats['updated']}")
                if stats.get("deleted", 0) > 0:
                    msg_parts.append(f"deleted {stats['deleted']}")
                message = f"Synced: {', '.join(msg_parts)} note(s)" if msg_parts else "No changes detected"
            elif log["service"] == "reminders":
                calendars_count = stats.get("calendars_synced", 0)
                if "total_created" in stats:
                    msg_parts = []
                    if stats.get("total_created", 0) > 0:
                        msg_parts.append(f"created {stats['total_created']}")
                    if stats.get("total_updated", 0) > 0:
                        msg_parts.append(f"updated {stats['total_updated']}")
                    if stats.get("total_deleted", 0) > 0:
                        msg_parts.append(f"deleted {stats['total_deleted']}")
                    message = f"Synced {calendars_count} calendar(s): {', '.join(msg_parts)} reminder(s)" if msg_parts else f"Synced {calendars_count} calendar(s), no changes needed"
                else:
                    message = f"Synced {calendars_count} calendar(s)"
            elif log["service"] == "passwords":
                push_stats = stats.get("push", {})
                pull_stats = stats.get("pull", {})
                msg_parts = []
                if push_stats.get("pushed", 0) > 0:
                    msg_parts.append(f"pushed {push_stats['pushed']} to VaultWarden")
                if pull_stats.get("pulled", 0) > 0:
                    msg_parts.append(f"pulled {pull_stats['pulled']} from VaultWarden")
                message = f"Synced: {', '.join(msg_parts)}" if msg_parts else "No changes detected"
        else:
            message = "Sync operation completed"

        # Convert timestamps to ISO strings
        started_at = datetime.fromtimestamp(log["started_at"]).isoformat() if log.get("started_at") else None
        completed_at = datetime.fromtimestamp(log["completed_at"]).isoformat() if log.get("completed_at") else None

        return {
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
        }

    notes_last_sync = transform_log(notes_logs[0]) if notes_logs else None
    reminders_last_sync = transform_log(reminders_logs[0]) if reminders_logs else None
    passwords_last_sync = transform_log(passwords_logs[0]) if passwords_logs else None
    photos_last_sync = transform_log(photos_logs[0]) if photos_logs else None

    # Get counts
    notes_count_result = await notes_db.get_stats()
    notes_count = notes_count_result.get("total", 0)

    reminders_count_result = await reminders_db.get_stats()
    reminders_count = reminders_count_result.get("total", 0)

    passwords_count_result = await passwords_db.get_stats()
    passwords_count = passwords_count_result.get("total", 0)

    await photos_db.initialize()
    photos_stats = await photos_db.get_stats(pending_since=photos_pending_since)

    try:
        from icloudbridge.api.app import scheduler as app_scheduler
    except ImportError:
        app_scheduler = None  # pragma: no cover

    scheduler_running = bool(app_scheduler and getattr(app_scheduler, "is_running", False))
    try:
        active_schedules = len(await schedules_db.get_schedules(enabled=True))
    except Exception:
        active_schedules = 0

    def service_state(last_sync):
        return last_sync["status"] if isinstance(last_sync, dict) and last_sync.get("status") else "idle"

    return StatusResponse(
        notes={
            "enabled": config.notes.enabled,
            "sync_count": notes_count,
            "last_sync": notes_last_sync,
            "status": service_state(notes_last_sync),
        },
        reminders={
            "enabled": config.reminders.enabled,
            "sync_count": reminders_count,
            "last_sync": reminders_last_sync,
            "status": service_state(reminders_last_sync),
        },
        passwords={
            "enabled": config.passwords.enabled,
            "sync_count": passwords_count,
            "last_sync": passwords_last_sync,
            "status": service_state(passwords_last_sync),
        },
        photos={
            "enabled": config.photos.enabled,
            "sync_count": photos_stats.get("total_imported", 0),
            "pending": photos_stats.get("pending", 0),
            "last_sync": photos_last_sync,
            "status": service_state(photos_last_sync),
        },
        scheduler_running=scheduler_running,
        active_schedules=active_schedules,
    )
