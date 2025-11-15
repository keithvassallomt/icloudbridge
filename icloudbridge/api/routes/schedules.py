"""Schedule management endpoints."""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, status

from icloudbridge.api.dependencies import ConfigDep
from icloudbridge.api.models import ScheduleCreate, ScheduleResponse, ScheduleUpdate
from icloudbridge.utils.db import SchedulesDB

ALLOWED_SCHEDULE_SERVICES = {"notes", "reminders", "photos"}


def _normalize_services(services: list[str] | None, legacy_service: str | None) -> list[str]:
    """Return a deduplicated, validated list of services."""

    normalized: list[str] = []
    candidates = services or []
    if not candidates and legacy_service:
        candidates = [legacy_service]

    for svc in candidates:
        if not svc:
            continue
        svc_lower = svc.lower()
        if svc_lower not in ALLOWED_SCHEDULE_SERVICES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported service '{svc}'. Allowed services: {', '.join(sorted(ALLOWED_SCHEDULE_SERVICES))}",
            )
        if svc_lower not in normalized:
            normalized.append(svc_lower)

    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one service must be selected",
        )

    return normalized


def _serialize_config_json(config_value: str | dict | None) -> str | None:
    """Ensure config_json is stored as a string."""

    if config_value is None or config_value == "":
        return None
    if isinstance(config_value, str):
        return config_value
    return json.dumps(config_value)


def _format_timestamp(value: float | str | None) -> str | None:
    """Convert numeric timestamps (seconds) to ISO strings for the UI."""

    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).isoformat()

    # value might already be ISO or stored as stringified float
    try:
        numeric = float(value)
        return datetime.fromtimestamp(numeric).isoformat()
    except (TypeError, ValueError):
        return str(value)


def _prepare_schedule_response(schedule: dict | None) -> ScheduleResponse:
    """Convert raw schedule dictionaries into ScheduleResponse objects."""

    if not schedule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Schedule not found",
        )

    schedule_data = dict(schedule)
    services_value = schedule_data.get("services")

    if isinstance(services_value, str):
        try:
            schedule_data["services"] = json.loads(services_value) if services_value else []
        except json.JSONDecodeError:
            schedule_data["services"] = [services_value]
    elif not services_value:
        service = schedule_data.get("service")
        schedule_data["services"] = [service] if service else []

    for field in ("created_at", "updated_at", "last_run", "next_run"):
        schedule_data[field] = _format_timestamp(schedule_data.get(field))

    return ScheduleResponse(**schedule_data)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("", response_model=list[ScheduleResponse])
async def list_schedules(
    config: ConfigDep,
    service: str | None = None,
    enabled: bool | None = None,
):
    """List all schedules with optional filtering.

    Args:
        service: Filter by service name (notes, reminders, passwords)
        enabled: Filter by enabled status

    Returns:
        List of schedules
    """
    try:
        schedules_db = SchedulesDB(config.general.data_dir / "schedules.db")
        await schedules_db.initialize()

        schedules = await schedules_db.get_schedules(service=service, enabled=enabled)

        return [_prepare_schedule_response(schedule) for schedule in schedules]

    except Exception as e:
        logger.error(f"Failed to list schedules: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list schedules: {str(e)}"
        )


@router.post("", response_model=ScheduleResponse)
async def create_schedule(schedule: ScheduleCreate, config: ConfigDep):
    """Create a new schedule.

    Args:
        schedule: Schedule configuration

    Returns:
        Created schedule with ID
    """
    try:
        services = _normalize_services(schedule.services, schedule.service)

        # Validate schedule type
        if schedule.schedule_type not in ["interval", "datetime"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="schedule_type must be 'interval' or 'datetime'"
            )

        # Validate interval or cron expression
        if schedule.schedule_type == "interval" and not schedule.interval_minutes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="interval_minutes required for interval type"
            )
        if schedule.schedule_type == "datetime" and not schedule.cron_expression:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cron_expression required for datetime type"
            )

        schedules_db = SchedulesDB(config.general.data_dir / "schedules.db")
        await schedules_db.initialize()

        schedule_id = await schedules_db.create_schedule(
            service=services[0],
            name=schedule.name,
            schedule_type=schedule.schedule_type,
            interval_minutes=schedule.interval_minutes,
            cron_expression=schedule.cron_expression,
            config_json=_serialize_config_json(schedule.config_json),
            enabled=schedule.enabled,
            services=services,
        )

        # Get the created schedule
        created = await schedules_db.get_schedule(schedule_id)

        logger.info(f"Schedule created: {schedule.name} (ID: {schedule_id})")

        # Register schedule with APScheduler
        from icloudbridge.api.app import scheduler
        if scheduler:
            await scheduler.add_schedule(schedule_id)

        return _prepare_schedule_response(created)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create schedule: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create schedule: {str(e)}"
        )


@router.get("/{schedule_id}", response_model=ScheduleResponse)
async def get_schedule(schedule_id: int, config: ConfigDep):
    """Get a schedule by ID.

    Args:
        schedule_id: Schedule ID

    Returns:
        Schedule details
    """
    try:
        schedules_db = SchedulesDB(config.general.data_dir / "schedules.db")
        await schedules_db.initialize()

        schedule = await schedules_db.get_schedule(schedule_id)

        return _prepare_schedule_response(schedule)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get schedule: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get schedule: {str(e)}"
        )


@router.put("/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule(
    schedule_id: int,
    update: ScheduleUpdate,
    config: ConfigDep,
):
    """Update a schedule.

    Args:
        schedule_id: Schedule ID
        update: Schedule updates

    Returns:
        Updated schedule
    """
    try:
        schedules_db = SchedulesDB(config.general.data_dir / "schedules.db")
        await schedules_db.initialize()

        # Check if schedule exists
        schedule = await schedules_db.get_schedule(schedule_id)
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Schedule {schedule_id} not found"
            )

        services = None
        if update.services is not None:
            services = _normalize_services(update.services, schedule.get("service"))

        # Update schedule
        await schedules_db.update_schedule(
            schedule_id=schedule_id,
            name=update.name,
            enabled=update.enabled,
            schedule_type=update.schedule_type,
            interval_minutes=update.interval_minutes,
            cron_expression=update.cron_expression,
            config_json=_serialize_config_json(update.config_json),
            services=services,
        )

        # Get updated schedule
        updated = await schedules_db.get_schedule(schedule_id)

        logger.info(f"Schedule updated: {schedule_id}")

        # Update schedule in APScheduler
        from icloudbridge.api.app import scheduler
        if scheduler:
            await scheduler.update_schedule(schedule_id)

        return _prepare_schedule_response(updated)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update schedule: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update schedule: {str(e)}"
        )


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: int, config: ConfigDep):
    """Delete a schedule.

    Args:
        schedule_id: Schedule ID

    Returns:
        Success message
    """
    try:
        schedules_db = SchedulesDB(config.general.data_dir / "schedules.db")
        await schedules_db.initialize()

        # Check if schedule exists
        schedule = await schedules_db.get_schedule(schedule_id)
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Schedule {schedule_id} not found"
            )

        await schedules_db.delete_schedule(schedule_id)

        logger.info(f"Schedule deleted: {schedule_id}")

        # Remove schedule from APScheduler
        from icloudbridge.api.app import scheduler
        if scheduler:
            await scheduler.remove_schedule(schedule_id)

        return {
            "status": "success",
            "message": f"Schedule {schedule_id} deleted",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete schedule: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete schedule: {str(e)}"
        )


@router.post("/{schedule_id}/run")
async def run_schedule(schedule_id: int, config: ConfigDep):
    """Manually trigger a schedule to run immediately.

    Args:
        schedule_id: Schedule ID

    Returns:
        Success message
    """
    try:
        schedules_db = SchedulesDB(config.general.data_dir / "schedules.db")
        await schedules_db.initialize()

        # Check if schedule exists
        schedule = await schedules_db.get_schedule(schedule_id)
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Schedule {schedule_id} not found"
            )

        logger.info(f"Manual run requested for schedule: {schedule_id}")

        # Trigger schedule execution in APScheduler
        from icloudbridge.api.app import scheduler
        if scheduler:
            await scheduler.trigger_schedule(schedule_id)

        return {
            "status": "success",
            "message": f"Schedule {schedule_id} triggered",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to run schedule: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to run schedule: {str(e)}"
        )


@router.put("/{schedule_id}/toggle")
async def toggle_schedule(schedule_id: int, config: ConfigDep):
    """Toggle a schedule's enabled status.

    Args:
        schedule_id: Schedule ID

    Returns:
        Updated schedule
    """
    try:
        schedules_db = SchedulesDB(config.general.data_dir / "schedules.db")
        await schedules_db.initialize()

        # Get current schedule
        schedule = await schedules_db.get_schedule(schedule_id)
        if not schedule:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Schedule {schedule_id} not found"
            )

        # Toggle enabled status
        new_enabled = not schedule["enabled"]
        await schedules_db.update_schedule(
            schedule_id=schedule_id,
            enabled=new_enabled,
        )

        # Get updated schedule
        updated = await schedules_db.get_schedule(schedule_id)

        logger.info(f"Schedule {schedule_id} {'enabled' if new_enabled else 'disabled'}")

        # Enable/disable schedule in APScheduler
        from icloudbridge.api.app import scheduler
        if scheduler:
            if new_enabled:
                await scheduler.add_schedule(schedule_id)
            else:
                await scheduler.remove_schedule(schedule_id)

        return _prepare_schedule_response(updated)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to toggle schedule: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to toggle schedule: {str(e)}"
        )
