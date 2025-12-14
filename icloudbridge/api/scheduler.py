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
from icloudbridge.core.config import AppConfig, load_config
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

    @property
    def is_running(self) -> bool:
        """Return True if the scheduler is actively running."""
        return self._running

    def _refresh_config(self) -> None:
        """Reload configuration from disk so scheduled runs see latest settings."""
        config_path = getattr(self.config.general, "config_file", None)
        if not config_path:
            config_path = self.config.default_config_path

        try:
            self.config = load_config(config_path)
            self.config.ensure_data_dir()
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Failed to reload config for scheduler: %s", exc)

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

        # Add job to scheduler
        job = self.scheduler.add_job(
            self._execute_sync,
            trigger=trigger,
            args=[schedule_id],
            id=f"schedule_{schedule_id}",
            replace_existing=True,
            name=schedule["name"],
        )

        logger.info(f"Schedule {schedule_id} ({schedule['name']}) added to scheduler")

        next_run_timestamp = self._get_next_run_timestamp(job)
        if next_run_timestamp:
            await self.schedules_db.update_schedule(
                schedule_id=schedule_id,
                next_run=next_run_timestamp,
            )

    async def _execute_sync(
        self,
        schedule_id: int,
    ) -> None:
        """Execute a scheduled sync operation for all services in the schedule."""

        self._refresh_config()

        schedule = await self.schedules_db.get_schedule(schedule_id)
        if not schedule:
            logger.error(f"Schedule {schedule_id} not found")
            return

        schedule_name = schedule["name"]
        services = schedule.get("services") or []
        if not services:
            logger.warning(f"Schedule {schedule_id} has no services configured")
            return

        logger.info(
            "Executing scheduled sync: %s (ID: %s, services: %s)",
            schedule_name,
            schedule_id,
            ", ".join(services),
        )

        config_dict: dict = {}
        if schedule.get("config_json"):
            try:
                parsed = json.loads(schedule["config_json"])
                if isinstance(parsed, dict):
                    config_dict = parsed
            except json.JSONDecodeError:
                logger.warning(f"Invalid config JSON for schedule {schedule_id}")

        # Track whether any service failed
        schedule_failed = False

        for service in services:
            service_config = self._extract_service_config(config_dict, service)
            await send_schedule_run(service, schedule_id, schedule_name, "started")

            log_id = await self.sync_logs_db.create_log(
                service=service,
                sync_type="scheduled",
                status="running",
            )

            start_time = datetime.now().timestamp()

            try:
                result = await self._run_service_sync(service, service_config)
                duration = datetime.now().timestamp() - start_time

                await self.sync_logs_db.update_log(
                    log_id=log_id,
                    status="success",
                    duration_seconds=duration,
                    stats_json=json.dumps(result),
                )

                await send_schedule_run(service, schedule_id, schedule_name, "completed")
                await send_sync_progress(
                    service=service,
                    status="success",
                    progress=100,
                    message=f"Scheduled sync completed: {schedule_name}",
                    stats=result,
                )

                logger.info(
                    "Scheduled %s sync completed: %s (duration: %.2fs)",
                    service,
                    schedule_name,
                    duration,
                )

            except Exception as exc:  # pylint: disable=broad-except
                schedule_failed = True
                duration = datetime.now().timestamp() - start_time
                error_msg = str(exc)

                logger.error(
                    "Scheduled %s sync failed (%s): %s",
                    service,
                    schedule_name,
                    error_msg,
                )

                await self.sync_logs_db.update_log(
                    log_id=log_id,
                    status="error",
                    duration_seconds=duration,
                    error_message=error_msg,
                )

                await send_schedule_run(service, schedule_id, schedule_name, "failed")
                await send_sync_progress(
                    service=service,
                    status="error",
                    progress=0,
                    message=f"Scheduled sync failed: {schedule_name}",
                )

        # Update schedule run timestamps regardless of success so users can diagnose failures
        next_run_time = None
        job = self.scheduler.get_job(f"schedule_{schedule_id}")
        next_run_time = self._get_next_run_timestamp(job)

        await self.schedules_db.update_schedule(
            schedule_id=schedule_id,
            last_run=datetime.now().timestamp(),
            next_run=next_run_time,
        )

        if schedule_failed:
            logger.warning("Scheduled sync completed with failures: %s", schedule_name)
        else:
            logger.info("Scheduled sync completed for all services: %s", schedule_name)

    def _extract_service_config(self, config_dict: dict, service: str) -> dict:
        """Return the config dictionary applicable to a specific service."""

        if not config_dict:
            return {}

        service_config = config_dict.get(service)
        if isinstance(service_config, dict):
            return service_config

        if isinstance(config_dict, dict):
            return config_dict

        return {}

    async def _run_service_sync(self, service: str, config: dict) -> dict:
        """Dispatch sync execution for the requested service."""

        if service == "notes":
            return await self._sync_notes(config)
        if service == "reminders":
            return await self._sync_reminders(config)
        if service == "passwords":
            return await self._sync_passwords(config)
        if service == "photos":
            return await self._sync_photos(config)

        raise ValueError(f"Unknown service: {service}")

    async def _sync_notes(self, config: dict) -> dict:
        """Execute notes sync (single folder or full mapping/auto mode)."""

        def _aggregate_stats(target: dict, stats: dict) -> None:
            """Update aggregate counters with per-folder stats."""
            target["created"] += stats.get("created_local", 0) + stats.get("created_remote", 0)
            target["updated"] += stats.get("updated_local", 0) + stats.get("updated_remote", 0)
            target["deleted"] += stats.get("deleted_local", 0) + stats.get("deleted_remote", 0)
            target["unchanged"] += stats.get("unchanged", 0)
            if stats.get("pending_local_notes"):
                target.setdefault("pending_local_notes", []).extend(stats["pending_local_notes"])

        self.config.ensure_data_dir()
        markdown_base_path = self.config.notes.remote_folder
        if not markdown_base_path:
            raise ValueError("Notes remote_folder not configured for scheduled sync")

        engine = NotesSyncEngine(
            markdown_base_path=markdown_base_path,
            db_path=self.config.notes_db_path,
            prefer_shortcuts=self.config.notes.use_shortcuts_for_push,
        )
        await engine.initialize()

        dry_run = config.get("dry_run", False)
        skip_deletions = config.get("skip_deletions", False)
        deletion_threshold = config.get("deletion_threshold", 5)
        sync_mode = config.get("mode", "bidirectional")

        # Single-folder schedules include a folder in their config JSON
        folder_name = config.get("folder")
        if folder_name:
            stats = await engine.sync_folder(
                folder_name=folder_name,
                markdown_subfolder=folder_name,
                dry_run=dry_run,
                skip_deletions=skip_deletions,
                deletion_threshold=deletion_threshold,
                sync_mode=sync_mode,
            )
            return {
                **stats,
                "folder_count": 1,
                "folder_results": [
                    {
                        "folder": folder_name,
                        "status": "success",
                        "stats": stats,
                    }
                ],
            }

        # No folder specified: run the same logic as the manual/CLI "sync all" flow
        if self.config.notes.folder_mappings:
            folder_mappings_dict = {
                apple_folder: {
                    "markdown_folder": mapping.markdown_folder,
                    "mode": mapping.mode,
                }
                for apple_folder, mapping in self.config.notes.folder_mappings.items()
            }

            folder_results = await engine.sync_with_mappings(
                folder_mappings=folder_mappings_dict,
                dry_run=dry_run,
                skip_deletions=skip_deletions,
                deletion_threshold=deletion_threshold,
            )

            total_stats = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0}
            total_stats["pending_local_notes"] = []
            formatted_results: list[dict] = []
            for folder, stats in folder_results.items():
                if "error" in stats:
                    total_stats["errors"] += 1
                    formatted_results.append(
                        {"folder": folder, "status": "error", "error": stats["error"]}
                    )
                else:
                    _aggregate_stats(total_stats, stats)
                    formatted_results.append({"folder": folder, "status": "success", "stats": stats})

            total_stats["folder_count"] = len(folder_results)
            total_stats["folder_results"] = formatted_results
            total_stats["mapping_mode"] = True
            return total_stats

        # Automatic 1:1 sync over every Apple folder
        folders = await engine.list_folders()
        total_stats = {"created": 0, "updated": 0, "deleted": 0, "unchanged": 0, "errors": 0}
        total_stats["pending_local_notes"] = []
        folder_results: list[dict] = []

        for folder_info in folders:
            folder = folder_info["name"]
            try:
                stats = await engine.sync_folder(
                    folder_name=folder,
                    markdown_subfolder=folder,
                    dry_run=dry_run,
                    skip_deletions=skip_deletions,
                    deletion_threshold=deletion_threshold,
                    sync_mode=sync_mode,
                )
                _aggregate_stats(total_stats, stats)
                folder_results.append({"folder": folder, "status": "success", "stats": stats})
            except Exception as exc:  # pylint: disable=broad-except
                total_stats["errors"] += 1
                folder_results.append({"folder": folder, "status": "error", "error": str(exc)})
                logger.error("Scheduled notes sync failed for folder %s: %s", folder, exc)

        total_stats["folder_count"] = len(folder_results)
        total_stats["folder_results"] = folder_results
        return total_stats

    async def _sync_reminders(self, config: dict) -> dict:
        """Execute reminders sync.

        Args:
            config: Sync configuration

        Returns:
            Sync statistics
        """
        self.config.ensure_data_dir()

        if not self.config.reminders.caldav_url:
            raise ValueError("CalDAV URL not configured for scheduled sync")

        caldav_password = self.config.reminders.get_caldav_password()
        if not self.config.reminders.caldav_username or not caldav_password:
            raise ValueError("CalDAV credentials not configured for scheduled sync")

        engine = RemindersSyncEngine(
            caldav_url=self.config.reminders.caldav_url,
            caldav_username=self.config.reminders.caldav_username,
            caldav_password=caldav_password,
            db_path=self.config.reminders_db_path,
        )
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
        self.config.ensure_data_dir()

        if not self.config.photos.enabled:
            raise ValueError("Photo sync is disabled in configuration")

        if not self.config.photos.sources:
            raise ValueError("No photo sources configured for scheduled sync")

        requested_sources = config.get("sources")
        if requested_sources:
            available = set(self.config.photos.sources.keys())
            invalid = [name for name in requested_sources if name not in available]
            if invalid:
                raise ValueError(f"Unknown photo sources requested: {', '.join(invalid)}")

        engine = PhotoSyncEngine(
            config=self.config.photos,
            data_dir=self.config.general.data_dir,
        )
        await engine.initialize()

        return await engine.sync(
            sources=requested_sources,
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

        # Execute sync immediately
        await self._execute_sync(schedule_id)

    def _get_next_run_timestamp(self, job) -> float | None:
        """Return the next run timestamp for a job, if known."""

        if not job:
            return None

        next_run_dt = getattr(job, "next_run_time", None)
        if next_run_dt:
            return next_run_dt.timestamp()

        trigger = getattr(job, "trigger", None)
        if not trigger:
            return None

        try:
            now = datetime.now(self.scheduler.timezone)
        except Exception:  # pragma: no cover - fallback path
            now = datetime.now()

        try:
            fire_time = trigger.get_next_fire_time(None, now)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Failed to compute next fire time for job %s: %s", job.id, exc)
            return None

        if fire_time:
            return fire_time.timestamp()

        return None
