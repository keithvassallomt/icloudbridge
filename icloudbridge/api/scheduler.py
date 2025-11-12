"""Scheduler manager for automated sync operations.

This module provides APScheduler integration for running scheduled syncs.
Schedules are stored in SQLite and synchronized with APScheduler on startup.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from icloudbridge.api.websocket import send_schedule_run, send_sync_progress
from icloudbridge.core.config import AppConfig
from icloudbridge.core.passwords_sync import PasswordsSyncEngine
from icloudbridge.core.photos_sync import PhotoSyncEngine
from icloudbridge.core.reminders_sync import RemindersSyncEngine
from icloudbridge.core.sync import NotesSyncEngine
from icloudbridge.sources.passwords.vaultwarden_api import VaultwardenAPIClient
from icloudbridge.utils.credentials import CredentialStore
from icloudbridge.utils.db import SchedulesDB, SyncLogsDB

logger = logging.getLogger(__name__)


class SchedulerManager:
    """Manages scheduled sync operations using APScheduler.

    Features:
    - Loads schedules from database on startup
    - Executes syncs based on interval or cron expressions
    - Broadcasts progress via WebSocket
    - Logs all scheduled operations
    """

    def __init__(self, config: AppConfig):
        """Initialize the scheduler manager.

        Args:
            config: Application configuration
        """
        self.config = config
        self.scheduler = AsyncIOScheduler()
        self.schedules_db = SchedulesDB(config.general.data_dir / "schedules.db")
        self.sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
        self._running = False

    async def start(self) -> None:
        """Start the scheduler and load all enabled schedules."""
        if self._running:
            logger.warning("Scheduler already running")
            return

        # Initialize databases
        await self.schedules_db.initialize()
        await self.sync_logs_db.initialize()

        # Load all enabled schedules from database
        schedules = await self.schedules_db.get_schedules(enabled=True)

        for schedule in schedules:
            await self._add_schedule_to_scheduler(schedule)

        # Start the scheduler
        self.scheduler.start()
        self._running = True

        logger.info(f"Scheduler started with {len(schedules)} active schedules")

    async def stop(self) -> None:
        """Stop the scheduler and cleanup."""
        if not self._running:
            return

        self.scheduler.shutdown(wait=False)
        self._running = False

        logger.info("Scheduler stopped")

    async def _add_schedule_to_scheduler(self, schedule: dict) -> None:
        """Add a schedule to APScheduler.

        Args:
            schedule: Schedule dictionary from database
        """
        schedule_id = schedule["id"]
        service = schedule["service"]
        schedule_type = schedule["schedule_type"]

        # Create trigger based on type
        if schedule_type == "interval":
            trigger = IntervalTrigger(minutes=schedule["interval_minutes"])
        elif schedule_type == "datetime":
            # Parse cron expression
            # Format: "minute hour day month day_of_week"
            # Example: "0 8 * * *" = daily at 8am
            try:
                trigger = CronTrigger.from_crontab(schedule["cron_expression"])
            except Exception as e:
                logger.error(f"Invalid cron expression for schedule {schedule_id}: {e}")
                return
        else:
            logger.error(f"Unknown schedule type: {schedule_type}")
            return

        # Parse config JSON
        config_dict = {}
        if schedule["config_json"]:
            try:
                config_dict = json.loads(schedule["config_json"])
            except json.JSONDecodeError:
                logger.warning(f"Invalid config JSON for schedule {schedule_id}")

        # Add job to scheduler
        self.scheduler.add_job(
            self._execute_sync,
            trigger=trigger,
            args=[schedule_id, service, config_dict],
            id=f"schedule_{schedule_id}",
            replace_existing=True,
            name=schedule["name"],
        )

        logger.info(f"Schedule {schedule_id} ({schedule['name']}) added to scheduler")

    async def _execute_sync(
        self,
        schedule_id: int,
        service: str,
        config: dict,
    ) -> None:
        """Execute a scheduled sync operation.

        Args:
            schedule_id: Schedule ID
            service: Service name (notes, reminders, passwords)
            config: Sync configuration options
        """
        # Get schedule details
        schedule = await self.schedules_db.get_schedule(schedule_id)
        if not schedule:
            logger.error(f"Schedule {schedule_id} not found")
            return

        schedule_name = schedule["name"]

        logger.info(f"Executing scheduled sync: {schedule_name} (ID: {schedule_id})")

        # Notify clients
        await send_schedule_run(service, schedule_id, schedule_name, "started")

        # Create sync log
        log_id = await self.sync_logs_db.create_log(
            service=service,
            sync_type="scheduled",
            status="running",
        )

        start_time = datetime.now().timestamp()

        try:
            # Execute sync based on service
            if service == "notes":
                result = await self._sync_notes(config)
            elif service == "reminders":
                result = await self._sync_reminders(config)
            elif service == "passwords":
                result = await self._sync_passwords(config)
            elif service == "photos":
                result = await self._sync_photos(config)
            else:
                raise ValueError(f"Unknown service: {service}")

            duration = datetime.now().timestamp() - start_time

            # Update sync log with success
            await self.sync_logs_db.update_log(
                log_id=log_id,
                status="success",
                duration_seconds=duration,
                stats_json=json.dumps(result),
            )

            # Update schedule's last_run
            await self.schedules_db.update_schedule(
                schedule_id=schedule_id,
                last_run=datetime.now().timestamp(),
            )

            # Notify clients
            await send_schedule_run(service, schedule_id, schedule_name, "completed")
            await send_sync_progress(
                service=service,
                status="success",
                progress=100,
                message=f"Scheduled sync completed: {schedule_name}",
                stats=result,
            )

            logger.info(f"Scheduled sync completed: {schedule_name} (duration: {duration:.2f}s)")

        except Exception as e:
            duration = datetime.now().timestamp() - start_time
            error_msg = str(e)

            logger.error(f"Scheduled sync failed: {schedule_name} - {error_msg}")

            # Update sync log with error
            await self.sync_logs_db.update_log(
                log_id=log_id,
                status="error",
                duration_seconds=duration,
                error_message=error_msg,
            )

            # Notify clients
            await send_schedule_run(service, schedule_id, schedule_name, "failed")
            await send_sync_progress(
                service=service,
                status="error",
                progress=0,
                message=f"Scheduled sync failed: {schedule_name}",
            )

    async def _sync_notes(self, config: dict) -> dict:
        """Execute notes sync.

        Args:
            config: Sync configuration

        Returns:
            Sync statistics
        """
        engine = NotesSyncEngine(self.config.notes)
        await engine.initialize()

        return await engine.sync_folder(
            folder_name=config.get("folder"),
            markdown_subfolder=None,
            dry_run=config.get("dry_run", False),
            skip_deletions=config.get("skip_deletions", False),
            deletion_threshold=config.get("deletion_threshold", 5),
        )

    async def _sync_reminders(self, config: dict) -> dict:
        """Execute reminders sync.

        Args:
            config: Sync configuration

        Returns:
            Sync statistics
        """
        engine = RemindersSyncEngine(self.config.reminders)
        await engine.initialize()

        if config.get("auto", True):
            # Auto mode
            return await engine.discover_and_sync_all(
                base_mappings=self.config.reminders.calendar_mappings,
                dry_run=config.get("dry_run", False),
                skip_deletions=config.get("skip_deletions", False),
                deletion_threshold=config.get("deletion_threshold", 5),
            )
        else:
            # Manual mode
            return await engine.sync_calendar(
                apple_calendar_name=config.get("apple_calendar"),
                caldav_calendar_name=config.get("caldav_calendar"),
                dry_run=config.get("dry_run", False),
                skip_deletions=config.get("skip_deletions", False),
                deletion_threshold=config.get("deletion_threshold", 5),
            )

    async def _sync_passwords(self, config: dict) -> dict:
        """Execute passwords sync.

        Args:
            config: Sync configuration

        Returns:
            Sync statistics
        """
        # Note: Scheduled password sync requires CSV files
        # This is a limitation of the current implementation
        # For now, we'll skip scheduled password syncs
        logger.warning("Scheduled password sync not implemented (requires CSV files)")
        return {
            "status": "skipped",
            "message": "Scheduled password sync requires manual CSV export",
        }

    async def _sync_photos(self, config: dict) -> dict:
        """Execute photos sync.

        Args:
            config: Sync configuration

        Returns:
            Sync statistics
        """
        engine = PhotoSyncEngine(
            config=self.config.photos,
            data_dir=self.config.general.data_dir,
        )
        await engine.initialize()

        return await engine.sync(
            sources=config.get("sources"),
            dry_run=config.get("dry_run", False),
        )

    async def add_schedule(self, schedule_id: int) -> None:
        """Add a schedule to the scheduler.

        Args:
            schedule_id: Schedule ID to add
        """
        schedule = await self.schedules_db.get_schedule(schedule_id)
        if schedule and schedule["enabled"]:
            await self._add_schedule_to_scheduler(schedule)

    async def remove_schedule(self, schedule_id: int) -> None:
        """Remove a schedule from the scheduler.

        Args:
            schedule_id: Schedule ID to remove
        """
        job_id = f"schedule_{schedule_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)
            logger.info(f"Schedule {schedule_id} removed from scheduler")

    async def update_schedule(self, schedule_id: int) -> None:
        """Update a schedule in the scheduler.

        Args:
            schedule_id: Schedule ID to update
        """
        # Remove old job and add updated one
        await self.remove_schedule(schedule_id)
        await self.add_schedule(schedule_id)

    async def trigger_schedule(self, schedule_id: int) -> None:
        """Manually trigger a schedule to run immediately.

        Args:
            schedule_id: Schedule ID to trigger
        """
        schedule = await self.schedules_db.get_schedule(schedule_id)
        if not schedule:
            raise ValueError(f"Schedule {schedule_id} not found")

        # Parse config
        config_dict = {}
        if schedule["config_json"]:
            try:
                config_dict = json.loads(schedule["config_json"])
            except json.JSONDecodeError:
                logger.warning(f"Invalid config JSON for schedule {schedule_id}")

        # Execute sync immediately
        await self._execute_sync(schedule_id, schedule["service"], config_dict)
