"""Core synchronization logic for Apple Reminders ↔ CalDAV."""

import hashlib
import json
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from icloudbridge.sources.reminders.caldav_adapter import (
    CalDAVAdapter,
    CalDAVAlarm,
    CalDAVRecurrence,
    CalDAVReminder,
)
from icloudbridge.sources.reminders.eventkit import (
    RECURRENCE_FREQUENCIES,
    EventKitReminder,
    ReminderAlarm,
    ReminderCalendar,
    ReminderRecurrence,
    RemindersAdapter,
)
from icloudbridge.utils.datetime_utils import safe_fromtimestamp
from icloudbridge.utils.db import RemindersDB
from icloudbridge.utils.exceptions import SourceUnavailableError

logger = logging.getLogger(__name__)

# Version of the alarm and repeat rule fingerprints stored with each mapping. Bump it
# whenever a conversion changes what a side's alarms or repeat rules sync as. Older
# fingerprints then count as missing, so the next sync records new ones instead of
# taking the difference for an edit.
FINGERPRINT_VERSION = 4


def _fingerprint(items: list) -> str:
    """Short hash of a list of alarms or repeat rules, ignoring their order."""
    encoded = sorted(json.dumps(asdict(item), sort_keys=True, default=str) for item in items)
    return hashlib.sha256(json.dumps(encoded).encode()).hexdigest()[:16]


# EventKit weekdays (1=Sunday … 7=Saturday) and their RRULE codes
WEEKDAY_CODES = {1: "SU", 2: "MO", 3: "TU", 4: "WE", 5: "TH", 6: "FR", 7: "SA"}


def setup_sync_file_logging(log_dir: Path) -> logging.FileHandler:
    """
    Set up a file handler for this sync operation.

    Args:
        log_dir: Directory to store log files (will be created if doesn't exist)

    Returns:
        FileHandler that was added to the logger
    """
    # Create log directory if it doesn't exist
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create log filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"sync_{timestamp}.log"

    # Create file handler
    file_handler = logging.FileHandler(log_file, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)

    # Create detailed formatter for file logs
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)

    # Add handler to root logger to capture all logs
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    # Set root logger to DEBUG so all messages are captured
    root_logger.setLevel(logging.DEBUG)

    logger.info(f"Sync log file created: {log_file}")

    return file_handler


class RemindersSyncEngine:
    """
    Orchestrates bidirectional synchronization between Apple Reminders and CalDAV.

    Design Philosophy:
    - Single-pass bidirectional sync (same as Notes sync)
    - Last-write-wins conflict resolution
    - Database tracks: local UUID ↔ CalDAV UID/URL mappings
    - Timestamp-based change detection

    Sync Algorithm:
    1. Fetch all reminders from Apple Reminders (by calendar/list)
    2. Fetch all TODOs from CalDAV calendar
    3. Build sync plan based on timestamps and database mappings
    4. Execute sync operations (create/update/delete on both sides)
    5. Update database with new mappings

    Key Difference from Notes:
    - Uses EventKit instead of AppleScript (better API, more features)
    - Syncs to CalDAV VTODO instead of markdown files
    - Supports alarms, recurrence rules, and URLs
    """

    def __init__(
        self,
        caldav_url: str,
        caldav_username: str,
        caldav_password: str,
        db_path: Path,
        caldav_ssl_verify_cert: bool | str = True,
        auto_delete_lists: bool = False,
    ):
        """
        Initialize the sync engine.

        Args:
            caldav_url: CalDAV server URL
            caldav_username: CalDAV username
            caldav_password: CalDAV password
            caldav_ssl_verify_cert: SSL verification flag or CA bundle path
            db_path: Path to SQLite database for state tracking
            auto_delete_lists: When a synced list is deleted on one side, delete
                               it on the other too instead of asking first
        """
        self.reminders_adapter = RemindersAdapter()
        self.caldav_adapter = CalDAVAdapter(
            caldav_url,
            caldav_username,
            caldav_password,
            ssl_verify_cert=caldav_ssl_verify_cert,
        )
        self.db = RemindersDB(db_path)
        self.auto_delete_lists = auto_delete_lists

    async def initialize(self) -> None:
        """
        Initialize the sync engine.

        Sets up database schema and connects to CalDAV server.
        """
        logger.debug("Initializing database...")
        await self.db.initialize()
        logger.debug("Requesting EventKit access...")
        await self.reminders_adapter.request_access()
        logger.debug("Connecting to CalDAV server...")
        await self.caldav_adapter.connect()
        logger.info("Reminders sync engine initialized")

    @staticmethod
    def _match_candidates(groups: dict[str, list]) -> dict:
        """
        Pick the reminder to match for each title and due date, leaving out ambiguous ones.

        A lone reminder is used as it is. Among several, completed ones are set aside: a
        shopping list keeps what was bought, so an open "Milk" often sits among many
        completed ones. With more than one open, or none, it's unclear which to match.
        """
        candidates = {}
        for key, items in groups.items():
            open_items = [item for item in items if not item.completed]
            if len(items) == 1:
                candidates[key] = items[0]
            elif len(open_items) == 1:
                candidates[key] = open_items[0]
        return candidates

    def _make_dedup_key(
        self, title: str | None, due_date: datetime | None, is_all_day: bool
    ) -> str:
        """
        Create a deduplication key from title + due_date for matching reminders.

        Used during bootstrap reconciliation to match reminders that exist on both
        Apple and CalDAV but aren't yet tracked in the database.

        Args:
            title: Reminder title (normalized: lowercase, trimmed)
            due_date: Due date (normalized by all-day status)
            is_all_day: Whether this is an all-day reminder

        Returns:
            String key in format "normalized_title|date_part"
        """
        normalized_title = title.strip().casefold() if title else ""

        if due_date is None:
            date_part = "no-date"
        elif is_all_day:
            date_part = due_date.strftime("%Y-%m-%d")
        else:
            date_part = due_date.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M")

        return f"{normalized_title}|{date_part}"

    async def sync_calendar(
        self,
        apple_calendar_name: str,
        caldav_calendar_name: str,
        dry_run: bool = False,
        skip_deletions: bool = False,
        deletion_threshold: int = 5,
    ) -> dict[str, int]:
        """
        Synchronize Apple Reminders calendar with CalDAV calendar.

        This is the MAIN SYNC METHOD that implements the bidirectional algorithm.

        Args:
            apple_calendar_name: Name of the Apple Reminders calendar/list to sync
            caldav_calendar_name: Name of the CalDAV calendar to sync with
            dry_run: If True, preview changes without applying them
            skip_deletions: If True, skip all deletion operations
            deletion_threshold: Prompt user if deletions exceed this count
                               (-1 to disable threshold, default: 5)

        Returns:
            Dictionary with sync statistics:
                - created_local: Reminders created in Apple Reminders
                - created_remote: TODOs created in CalDAV
                - updated_local: Reminders updated in Apple Reminders
                - updated_remote: TODOs updated in CalDAV
                - deleted_local: Reminders deleted from Apple Reminders
                - deleted_remote: TODOs deleted from CalDAV
                - unchanged: Items that didn't need syncing
                - errors: Number of errors encountered
        """
        stats = {
            "created_local": 0,
            "created_remote": 0,
            "updated_local": 0,
            "updated_remote": 0,
            "deleted_local": 0,
            "deleted_remote": 0,
            "unchanged": 0,
            "errors": 0,
        }

        try:
            logger.info(f"Starting sync: {apple_calendar_name} → {caldav_calendar_name}")
            logger.info(f"Dry run: {dry_run}, Skip deletions: {skip_deletions}")
            # Step 0: Ensure calendars exist on both sides
            # Check if Apple Reminders calendar exists, create if not
            apple_calendars = await self.reminders_adapter.list_calendars()

            # list_calendars() already rebuilt the store and retried before
            # returning nothing, so an empty list here means Reminders is truly
            # unreadable. Stop now: every mapped reminder would otherwise look
            # locally deleted and we would wipe the CalDAV side.
            if not apple_calendars:
                raise SourceUnavailableError(
                    "Apple Reminders returned no lists, so the backend cannot read "
                    "your reminders. Refusing to sync so that remote tasks are not "
                    "deleted. Restart iCloudBridge and re-grant Reminders access if "
                    "prompted."
                )

            apple_cal_lookup = {cal.title.lower(): cal for cal in apple_calendars}
            local_reminders: list[EventKitReminder]
            effective_apple_name = apple_calendar_name
            target_apple_calendar = apple_cal_lookup.get(apple_calendar_name.lower())
            # A list we have synced before but can no longer see is not the same
            # as a list the user emptied; withhold deletions in that case.
            apple_calendar_missing = target_apple_calendar is None

            caldav_calendars = await self.caldav_adapter.list_calendars()
            caldav_lookup = {cal["name"].lower(): cal for cal in caldav_calendars}
            target_caldav_calendar = caldav_lookup.get(caldav_calendar_name.lower())

            # A pair with no list on either side comes from a stale mapping
            # (the user deleted the list, or the mapping outlived a reset).
            # Creating both sides from nothing would bring a deleted list back.
            if apple_calendar_missing and target_caldav_calendar is None:
                logger.warning(
                    "Skipping %s → %s: neither the Apple list nor the CalDAV calendar exists. "
                    "Remove this mapping if the list was deleted.",
                    apple_calendar_name,
                    caldav_calendar_name,
                )
                return stats

            # A list that synced before and is now missing on one side was
            # deleted there. Recreating it, as for a new list, would undo that.
            if await self._handle_deleted_list(
                apple_calendars,
                target_apple_calendar,
                target_caldav_calendar,
                apple_calendar_name,
                caldav_calendar_name,
                stats,
                dry_run=dry_run,
                skip_deletions=skip_deletions,
            ):
                return stats

            if not target_apple_calendar:
                if dry_run:
                    logger.warning(
                        "Dry run: Apple Reminders calendar '%s' does not exist and would be created during a real sync.",
                        apple_calendar_name,
                    )
                    local_reminders = []
                else:
                    logger.warning(
                        f"Apple Reminders calendar '{apple_calendar_name}' not found, creating it..."
                    )
                    created_cal = await self.reminders_adapter.create_calendar(apple_calendar_name)
                    if not created_cal:
                        logger.error(f"Failed to create Apple calendar: {apple_calendar_name}")
                        raise RuntimeError(f"Failed to create Apple calendar: {apple_calendar_name}")
                    local_reminders = []
                    effective_apple_name = apple_calendar_name
            else:
                effective_apple_name = target_apple_calendar.title
                # Step 1: Fetch all reminders from Apple Reminders
                logger.info(f"Fetching reminders from Apple calendar '{effective_apple_name}'...")
                local_reminders = await self.reminders_adapter.get_reminders(
                    calendar_name=effective_apple_name
                )

            logger.info(f"Found {len(local_reminders)} local reminders")

            # Create the CalDAV calendar if it does not exist yet
            effective_caldav_name = caldav_calendar_name
            if not target_caldav_calendar:
                if dry_run:
                    logger.warning(
                        "Dry run: CalDAV calendar '%s' does not exist and would be created during a real sync.",
                        caldav_calendar_name,
                    )
                    remote_todos = []
                else:
                    logger.warning(
                        f"CalDAV calendar '{caldav_calendar_name}' not found, creating it..."
                    )
                    if not await self.caldav_adapter.create_calendar(caldav_calendar_name):
                        logger.error(f"Failed to create CalDAV calendar: {caldav_calendar_name}")
                        raise RuntimeError(f"Failed to create CalDAV calendar: {caldav_calendar_name}")
                    remote_todos = []
                    effective_caldav_name = caldav_calendar_name
            else:
                effective_caldav_name = target_caldav_calendar["name"]
                # Step 2: Fetch all TODOs from CalDAV
                logger.info(f"Fetching TODOs from CalDAV calendar '{effective_caldav_name}'...")
                remote_todos = await self.caldav_adapter.get_todos(calendar_name=effective_caldav_name)

            logger.info(f"Found {len(remote_todos)} remote TODOs")

            # Step 3: Build mappings
            # UUID → EventKitReminder
            local_by_uuid = {r.uuid: r for r in local_reminders}
            # UID → CalDAVReminder
            remote_by_uid = {r.uid: r for r in remote_todos}

            # Get all database mappings
            all_mappings = await self.db.get_all_mappings()
            # local_uuid → mapping
            db_mappings = {m["local_uuid"]: m for m in all_mappings}

            # Step 4: Determine sync operations
            effective_skip_deletions = skip_deletions
            if apple_calendar_missing and db_mappings and not skip_deletions:
                logger.warning(
                    "Apple list '%s' has %d existing mapping(s) but is no longer visible - "
                    "skipping deletions for this run so remote tasks are preserved",
                    apple_calendar_name,
                    len(db_mappings),
                )
                effective_skip_deletions = True

            sync_plan = await self._build_sync_plan(
                local_by_uuid,
                remote_by_uid,
                db_mappings,
                effective_skip_deletions,
                deletion_threshold,
            )

            # Step 5: Execute sync plan
            if dry_run:
                logger.info("=== DRY RUN MODE - No changes will be made ===")
                self._log_sync_plan(sync_plan)
                # Populate stats from sync plan for dry-run
                stats["created_local"] = len(sync_plan["create_local"])
                stats["created_remote"] = len(sync_plan["create_remote"])
                stats["updated_local"] = len(sync_plan["update_local"])
                stats["updated_remote"] = len(sync_plan["update_remote"])
                stats["deleted_local"] = len(sync_plan["delete_local"])
                stats["deleted_remote"] = len(sync_plan["delete_remote"])
                stats["unchanged"] = len(sync_plan["unchanged"])

                # Extract reminder metadata for tooltips (includes completion status, due dates, recurrence)
                stats["created_local_items"] = [
                    {
                        "title": r.summary,
                        "completed": r.completed,
                        "due_date": r.due_date.isoformat() if r.due_date else None,
                        "is_recurring": len(r.recurrence_rules) > 0,
                    }
                    for r in sync_plan["create_local"]
                ]
                stats["created_remote_items"] = [
                    {
                        "title": r.title,
                        "completed": r.completed,
                        "due_date": r.due_date.isoformat() if r.due_date else None,
                        "is_recurring": len(r.recurrence_rules) > 0,
                    }
                    for r in sync_plan["create_remote"]
                ]
                stats["updated_local_items"] = [
                    {
                        "title": r.summary,
                        "completed": r.completed,
                        "due_date": r.due_date.isoformat() if r.due_date else None,
                        "is_recurring": len(r.recurrence_rules) > 0,
                    }
                    for _, r, _ in sync_plan["update_local"]
                ]
                stats["updated_remote_items"] = [
                    {
                        "title": r.title,
                        "completed": r.completed,
                        "due_date": r.due_date.isoformat() if r.due_date else None,
                        "is_recurring": len(r.recurrence_rules) > 0,
                    }
                    for _, r, _ in sync_plan["update_remote"]
                ]
                stats["deleted_local_items"] = [
                    {
                        "title": r.title,
                        "completed": r.completed,
                        "due_date": r.due_date.isoformat() if r.due_date else None,
                        "is_recurring": len(r.recurrence_rules) > 0,
                    }
                    for _, r in sync_plan["delete_local"]
                ]
                stats["deleted_remote_items"] = [
                    {
                        "title": r.summary,
                        "completed": r.completed,
                        "due_date": r.due_date.isoformat() if r.due_date else None,
                        "is_recurring": len(r.recurrence_rules) > 0,
                    }
                    for _, r in sync_plan["delete_remote"]
                ]

                return stats

            stats = await self._execute_sync_plan(
                sync_plan,
                effective_apple_name,
                effective_caldav_name,
                local_by_uuid,
                remote_by_uid,
                db_mappings,
            )
            await self._record_list_pair(effective_apple_name, effective_caldav_name)

            logger.info(f"Sync completed: {stats}")
            return stats

        except Exception as e:
            logger.error(f"Sync failed: {e}")
            raise

    async def _build_sync_plan(
        self,
        local_by_uuid: dict[str, EventKitReminder],
        remote_by_uid: dict[str, CalDAVReminder],
        db_mappings: dict[str, dict],
        skip_deletions: bool,
        deletion_threshold: int,
    ) -> dict:
        """
        Build a sync plan based on timestamps and database mappings.

        Returns:
            Dictionary with lists of operations:
                - create_local: [(remote_todo, ...)]
                - create_remote: [(local_reminder, ...)]
                - update_local: [(local_uuid, remote_todo, ...)]
                - update_remote: [(remote_uid, local_reminder, ...)]
                - delete_local: [(local_uuid, ...)]
                - delete_remote: [(remote_uid, ...)]
                - unchanged: [(local_uuid, ...)]
                - matched: [(local_reminder, remote_todo, last_sync)], pairs matched by
                  title and due date, which need a mapping (they are in one of the
                  update lists or unchanged as well)
        """
        plan = {
            "create_local": [],
            "create_remote": [],
            "update_local": [],
            "update_remote": [],
            "delete_local": [],
            "delete_remote": [],
            "unchanged": [],
            "matched": [],
        }

        # Track which items we've processed
        processed_local = set()
        processed_remote = set()

        # Match by title and due date while none of this list's reminders are mapped: a
        # fresh setup, a reset, or a list that is new. Other lists' mappings don't count.
        is_fresh_list = not any(
            local_uuid in local_by_uuid or mapping["remote_uid"] in remote_by_uid
            for local_uuid, mapping in db_mappings.items()
        )

        # Process all database mappings
        for local_uuid, mapping in db_mappings.items():
            remote_uid = mapping["remote_uid"]
            last_sync = safe_fromtimestamp(mapping["last_sync_timestamp"])
            if last_sync is None:
                last_sync = datetime.now(timezone.utc)
            last_sync = last_sync.astimezone()

            local_reminder = local_by_uuid.get(local_uuid)
            remote_todo = remote_by_uid.get(remote_uid)

            # Case 1: Both exist - check for updates
            if local_reminder and remote_todo:
                local_mod = local_reminder.modification_date
                remote_mod = remote_todo.last_modified

                # Both changed since last sync - CONFLICT (last-write-wins)
                if local_mod > last_sync and remote_mod > last_sync:
                    if local_mod > remote_mod:
                        logger.info(
                            f"Conflict for '{local_reminder.title}': local wins (local={local_mod}, remote={remote_mod})"
                        )
                        plan["update_remote"].append((remote_uid, local_reminder, remote_todo))
                    else:
                        logger.info(
                            f"Conflict for '{local_reminder.title}': remote wins (local={local_mod}, remote={remote_mod})"
                        )
                        plan["update_local"].append((local_uuid, remote_todo, local_reminder))

                # Only local changed
                elif local_mod > last_sync:
                    plan["update_remote"].append((remote_uid, local_reminder, remote_todo))

                # Only remote changed
                elif remote_mod > last_sync:
                    plan["update_local"].append((local_uuid, remote_todo, local_reminder))

                # Neither changed
                else:
                    plan["unchanged"].append(local_uuid)

                processed_local.add(local_uuid)
                processed_remote.add(remote_uid)

            # Case 2: Only local exists - remote was deleted
            elif local_reminder and not remote_todo:
                if not skip_deletions:
                    plan["delete_local"].append((local_uuid, local_reminder))
                processed_local.add(local_uuid)

            # Case 3: Only remote exists - local was deleted
            elif not local_reminder and remote_todo:
                if not skip_deletions:
                    plan["delete_remote"].append((remote_uid, remote_todo))
                processed_remote.add(remote_uid)

            # Case 4: Both deleted - clean up mapping
            else:
                # Will be handled by database cleanup
                pass

        # --- Bootstrap reconciliation for a fresh list ---
        # When the same reminders exist on both Apple and CalDAV but none are mapped,
        # pair them by title + due_date to avoid creating duplicates.
        if is_fresh_list:
            # Group unprocessed items by title + due date
            local_groups: dict[str, list[EventKitReminder]] = {}
            for local_uuid, local_reminder in local_by_uuid.items():
                if local_uuid not in processed_local:
                    key = self._make_dedup_key(
                        local_reminder.title, local_reminder.due_date, local_reminder.is_all_day
                    )
                    local_groups.setdefault(key, []).append(local_reminder)

            remote_groups: dict[str, list[CalDAVReminder]] = {}
            for remote_uid, remote_todo in remote_by_uid.items():
                if remote_uid not in processed_remote:
                    key = self._make_dedup_key(
                        remote_todo.summary, remote_todo.due_date, remote_todo.is_all_day
                    )
                    remote_groups.setdefault(key, []).append(remote_todo)

            local_candidates = self._match_candidates(local_groups)
            remote_candidates = self._match_candidates(remote_groups)

            # Find matching pairs by title + due_date
            matched_keys = set(local_candidates).intersection(remote_candidates)

            for key in matched_keys:
                local_reminder = local_candidates[key]
                remote_todo = remote_candidates[key]

                # Determine source of truth by most recent modification
                local_mod = local_reminder.modification_date
                remote_mod = remote_todo.last_modified

                # Use a tolerance of 2 seconds when comparing timestamps
                # CalDAV doesn't store microseconds, so after a sync the timestamps
                # may differ by up to 1 second due to precision loss
                time_diff = abs((local_mod - remote_mod).total_seconds())
                timestamps_equal = time_diff < 2.0

                if timestamps_equal:
                    # Timestamps are essentially equal - no update needed
                    plan["unchanged"].append(local_reminder.uuid)
                    logger.info(
                        f"Bootstrap match: '{local_reminder.title}' - unchanged "
                        f"(diff={time_diff:.3f}s)"
                    )
                elif local_mod > remote_mod:
                    # Local is newer - update remote with local data
                    plan["update_remote"].append((remote_todo.uid, local_reminder, remote_todo))
                    logger.info(
                        f"Bootstrap match: '{local_reminder.title}' - local wins "
                        f"(local={local_mod}, remote={remote_mod})"
                    )
                else:
                    # Remote is newer - update local with remote data
                    plan["update_local"].append((local_reminder.uuid, remote_todo, local_reminder))
                    logger.info(
                        f"Bootstrap match: '{local_reminder.title}' - remote wins "
                        f"(local={local_mod}, remote={remote_mod})"
                    )

                # If an update is needed, sync from the older side's time, so that a
                # failed update is retried
                pick = max if timestamps_equal else min
                plan["matched"].append((local_reminder, remote_todo, pick(local_mod, remote_mod)))

                processed_local.add(local_reminder.uuid)
                processed_remote.add(remote_todo.uid)

            if matched_keys:
                logger.info(f"Bootstrap reconciliation matched {len(matched_keys)} reminders")

        # Process new local reminders (not in database)
        # Skip completed reminders that aren't already mapped - don't sync historical completed items
        # BUT still sync recurring reminders even if the current instance is completed
        for local_uuid, local_reminder in local_by_uuid.items():
            if local_uuid not in processed_local:
                if local_reminder.completed and not local_reminder.recurrence_rules:
                    logger.debug(f"Skipping new completed reminder: {local_reminder.title}")
                    continue
                plan["create_remote"].append(local_reminder)
                processed_local.add(local_uuid)

        # Process new remote TODOs (not in database)
        # Skip completed TODOs that aren't already mapped - don't sync historical completed items
        # BUT still sync recurring TODOs even if the current instance is completed
        for remote_uid, remote_todo in remote_by_uid.items():
            if remote_uid not in processed_remote:
                if remote_todo.completed and not remote_todo.recurrence_rules:
                    logger.debug(f"Skipping new completed TODO: {remote_todo.summary}")
                    continue
                plan["create_local"].append(remote_todo)
                processed_remote.add(remote_uid)

        # Check deletion threshold
        total_deletions = len(plan["delete_local"]) + len(plan["delete_remote"])
        if deletion_threshold >= 0 and total_deletions > deletion_threshold:
            logger.warning(
                f"Deletion threshold exceeded: {total_deletions} deletions (threshold: {deletion_threshold})"
            )

            # One side going completely empty while the other still holds every
            # mapped item is the signature of an unreadable source, not of a
            # user deleting everything. Say so, rather than inviting the user to
            # raise the threshold and destroy their data.
            if not local_by_uuid and plan["delete_remote"]:
                raise SourceUnavailableError(
                    f"Every mapped reminder ({total_deletions}) disappeared from Apple "
                    "Reminders at once, which almost always means the backend lost "
                    "access rather than that you deleted them. Refusing to delete the "
                    "remote copies. Restart iCloudBridge and check that your reminder "
                    "lists are visible before syncing again. If you really did clear "
                    "the list, re-run with deletions enabled once the count looks right."
                )

            logger.warning("Use --skip-deletions to skip deletions, or increase --deletion-threshold")
            raise RuntimeError(f"Deletion threshold exceeded: {total_deletions} > {deletion_threshold}")

        return plan

    def _log_sync_plan(self, plan: dict) -> None:
        """Log the sync plan (for dry-run mode)."""
        logger.info("=== Sync Plan ===")
        logger.info(f"Create in Apple Reminders: {len(plan['create_local'])}")
        logger.info(f"Create in CalDAV: {len(plan['create_remote'])}")
        logger.info(f"Update in Apple Reminders: {len(plan['update_local'])}")
        logger.info(f"Update in CalDAV: {len(plan['update_remote'])}")
        logger.info(f"Delete from Apple Reminders: {len(plan['delete_local'])}")
        logger.info(f"Delete from CalDAV: {len(plan['delete_remote'])}")
        logger.info(f"Unchanged: {len(plan['unchanged'])}")

        # Log details
        for reminder in plan["create_local"]:
            logger.info(f"  [CREATE LOCAL] {reminder.summary}")
        for reminder in plan["create_remote"]:
            logger.info(f"  [CREATE REMOTE] {reminder.title}")
        for _, reminder, _ in plan["update_local"]:
            logger.info(f"  [UPDATE LOCAL] {reminder.summary}")
        for _, reminder, _ in plan["update_remote"]:
            logger.info(f"  [UPDATE REMOTE] {reminder.title}")
        for _, reminder in plan["delete_local"]:
            logger.info(f"  [DELETE LOCAL] {reminder.title}")
        for _, todo in plan["delete_remote"]:
            logger.info(f"  [DELETE REMOTE] {todo.summary}")

    async def _execute_sync_plan(
        self,
        plan: dict,
        apple_calendar_name: str,
        caldav_calendar_name: str,
        local_by_uuid: dict[str, EventKitReminder],
        remote_by_uid: dict[str, CalDAVReminder],
        db_mappings: dict[str, dict],
    ) -> dict[str, int]:
        """Execute the sync plan and update the database."""
        stats = {
            "created_local": 0,
            "created_remote": 0,
            "updated_local": 0,
            "updated_remote": 0,
            "deleted_local": 0,
            "deleted_remote": 0,
            "unchanged": len(plan["unchanged"]),
            "errors": 0,
        }
        error_messages: list[str] = []

        # Get calendar IDs
        apple_calendars = await self.reminders_adapter.list_calendars()
        apple_calendar_id = None
        for cal in apple_calendars:
            if cal.title == apple_calendar_name:
                apple_calendar_id = cal.uuid
                break

        if not apple_calendar_id:
            raise ValueError(f"Apple Reminders calendar not found: {apple_calendar_name}")

        # Pairs matched by title and due date have no mapping yet. Record them first, so
        # the updates below have one to update and later syncs don't take them for new.
        for local_reminder, remote_todo, last_sync in plan["matched"]:
            try:
                await self.db.add_mapping(
                    local_uuid=local_reminder.uuid,
                    remote_uid=remote_todo.uid,
                    local_title=local_reminder.title,
                    remote_caldav_url=remote_todo.caldav_url,
                    last_sync=last_sync,
                    sync_fingerprints=json.dumps(
                        self._sync_fingerprints(local_reminder, remote_todo)
                    ),
                )
            except Exception as e:
                msg = f"Failed to record matched reminder '{local_reminder.title}': {e}"
                logger.error(msg)
                error_messages.append(msg)
                stats["errors"] += 1

        # Create in Apple Reminders
        for remote_todo in plan["create_local"]:
            try:
                # Convert CalDAV priority to Apple priority
                # CalDAV: 0=undefined, 1=highest, 9=lowest
                # Apple: 0=none, 1-4=high, 5-9=medium/low
                apple_priority = self._convert_priority_to_apple(remote_todo.priority)

                # Convert alarms and recurrence rules
                alarms = self._convert_alarms_to_eventkit(remote_todo.alarms)
                recurrence_rules = self._convert_recurrence_to_eventkit(remote_todo.recurrence_rules)

                created = await self.reminders_adapter.create_reminder(
                    calendar_id=apple_calendar_id,
                    title=remote_todo.summary,
                    notes=remote_todo.description,
                    completed=remote_todo.completed,
                    priority=apple_priority,
                    due_date=remote_todo.due_date,
                    is_all_day=remote_todo.is_all_day,
                    url=remote_todo.url,
                    alarms=alarms,
                    recurrence_rules=recurrence_rules,
                )

                # Add to database
                # Use the created Apple reminder's modification date as last_sync
                # This represents when the Apple reminder was created from the CalDAV source
                await self.db.add_mapping(
                    local_uuid=created.uuid,
                    remote_uid=remote_todo.uid,
                    local_title=created.title,
                    remote_caldav_url=remote_todo.caldav_url,
                    last_sync=created.modification_date,
                    sync_fingerprints=json.dumps(self._sync_fingerprints(created, remote_todo)),
                )

                stats["created_local"] += 1
                logger.info(f"Created in Apple Reminders: {created.title}")

            except Exception as e:
                msg = f"Failed to create local reminder '{remote_todo.summary}': {e}"
                logger.error(msg)
                error_messages.append(msg)
                stats["errors"] += 1

        # Create in CalDAV
        for local_reminder in plan["create_remote"]:
            try:
                # Convert Apple priority to CalDAV priority
                caldav_priority = self._convert_priority_to_caldav(local_reminder.priority)

                # Convert alarms and recurrence rules
                alarms = self._convert_alarms_to_caldav(local_reminder.alarms)
                recurrence_rules = self._convert_recurrence_to_caldav(local_reminder.recurrence_rules)

                created = await self.caldav_adapter.create_todo(
                    calendar_name=caldav_calendar_name,
                    uid=local_reminder.uuid,  # Use Apple UUID as CalDAV UID
                    summary=local_reminder.title,
                    description=local_reminder.notes,
                    completed=local_reminder.completed,
                    priority=caldav_priority,
                    due_date=local_reminder.due_date,
                    is_all_day=local_reminder.is_all_day,
                    url=local_reminder.url,
                    alarms=alarms,
                    recurrence_rules=recurrence_rules,
                    creation_date=local_reminder.creation_date,
                    modification_date=local_reminder.modification_date,
                )

                if created:
                    # Add to database
                    # Use the Apple reminder's modification date as last_sync
                    # This represents the source modification time we synced to CalDAV
                    await self.db.add_mapping(
                        local_uuid=local_reminder.uuid,
                        remote_uid=created.uid,
                        local_title=local_reminder.title,
                        remote_caldav_url=created.caldav_url,
                        last_sync=local_reminder.modification_date,
                        sync_fingerprints=json.dumps(
                            self._sync_fingerprints(local_reminder, created)
                        ),
                    )

                    stats["created_remote"] += 1
                    logger.info(f"Created in CalDAV: {created.summary}")
                else:
                    # Log creation failure prominently
                    logger.error(
                        f"⚠️  CALDAV CREATION FAILED for '{local_reminder.title}' ⚠️\n"
                        f"    Apple UUID: {local_reminder.uuid}\n"
                        f"    Will retry on next sync"
                    )
                    stats["errors"] += 1

            except Exception as e:
                msg = f"Failed to create remote TODO '{local_reminder.title}': {e}"
                logger.error(msg)
                error_messages.append(msg)
                stats["errors"] += 1

        # Update in Apple Reminders
        for local_uuid, remote_todo, local_reminder in plan["update_local"]:
            try:
                apple_priority = self._convert_priority_to_apple(remote_todo.priority)

                # Only rewrite alarms and recurrence rules that changed on CalDAV since the
                # last sync. Anything else, such as a new title, leaves them alone.
                fingerprints = self._sync_fingerprints(local_reminder, remote_todo)
                stored = self._stored_fingerprints(db_mappings.get(local_uuid))
                alarms = None
                if self._changed_since_sync(stored, fingerprints, "caldav", "alarms"):
                    alarms = self._convert_alarms_to_eventkit(remote_todo.alarms)
                recurrence_rules = None
                if self._changed_since_sync(stored, fingerprints, "caldav", "recurrence"):
                    recurrence_rules = self._convert_recurrence_to_eventkit(
                        remote_todo.recurrence_rules
                    )

                updated = await self.reminders_adapter.update_reminder(
                    uuid=local_uuid,
                    title=remote_todo.summary,
                    notes=remote_todo.description,
                    completed=remote_todo.completed,
                    priority=apple_priority,
                    due_date=remote_todo.due_date,
                    is_all_day=remote_todo.is_all_day,
                    url=remote_todo.url,
                    alarms=alarms,
                    recurrence_rules=recurrence_rules,
                )

                # Update database timestamp
                # Use the updated Apple reminder's modification date as last_sync
                # This represents when the Apple reminder was updated from the CalDAV source
                await self.db.update_mapping(
                    local_uuid=local_uuid,
                    remote_uid=remote_todo.uid,
                    remote_caldav_url=remote_todo.caldav_url,
                    last_sync=updated.modification_date,
                    sync_fingerprints=json.dumps(self._sync_fingerprints(updated, remote_todo)),
                )

                stats["updated_local"] += 1
                logger.info(f"Updated in Apple Reminders: {updated.title}")

            except Exception as e:
                msg = f"Failed to update local reminder '{local_reminder.title}': {e}"
                logger.error(msg)
                error_messages.append(msg)
                stats["errors"] += 1

        # Update in CalDAV
        for remote_uid, local_reminder, remote_todo in plan["update_remote"]:
            try:
                caldav_priority = self._convert_priority_to_caldav(local_reminder.priority)

                # Only rewrite alarms and recurrence rules that changed in Apple Reminders
                # since the last sync
                fingerprints = self._sync_fingerprints(local_reminder, remote_todo)
                stored = self._stored_fingerprints(db_mappings.get(local_reminder.uuid))
                alarms = None
                if self._changed_since_sync(stored, fingerprints, "apple", "alarms"):
                    alarms = self._convert_alarms_to_caldav(local_reminder.alarms)
                recurrence_rules = None
                if self._changed_since_sync(stored, fingerprints, "apple", "recurrence"):
                    recurrence_rules = self._convert_recurrence_to_caldav(
                        local_reminder.recurrence_rules
                    )

                updated = await self.caldav_adapter.update_todo(
                    caldav_url=remote_todo.caldav_url,
                    summary=local_reminder.title,
                    description=local_reminder.notes,
                    completed=local_reminder.completed,
                    priority=caldav_priority,
                    due_date=local_reminder.due_date,
                    is_all_day=local_reminder.is_all_day,
                    url=local_reminder.url,
                    alarms=alarms,
                    recurrence_rules=recurrence_rules,
                    modification_date=local_reminder.modification_date,
                )

                if updated:
                    # Update database timestamp
                    # Use the Apple reminder's modification date as last_sync
                    # This represents the source modification time we synced to CalDAV
                    await self.db.update_mapping(
                        local_uuid=local_reminder.uuid,
                        remote_uid=remote_uid,
                        remote_caldav_url=updated.caldav_url,
                        last_sync=local_reminder.modification_date,
                        sync_fingerprints=json.dumps(
                            self._sync_fingerprints(local_reminder, updated)
                        ),
                    )

                    stats["updated_remote"] += 1
                    logger.info(f"Updated in CalDAV: {updated.summary}")
                else:
                    # Option D: Update database even on failure to prevent infinite retries
                    # Log the failure prominently so it's visible in logs
                    logger.error(
                        f"⚠️  CALDAV UPDATE FAILED for '{local_reminder.title}' ⚠️\n"
                        f"    CalDAV URL: {remote_todo.caldav_url}\n"
                        f"    Updating database last_sync to prevent infinite retries\n"
                        f"    Apple modification date: {local_reminder.modification_date}"
                    )

                    # Update database to prevent this reminder from being retried every sync
                    await self.db.update_mapping(
                        local_uuid=local_reminder.uuid,
                        remote_uid=remote_uid,
                        remote_caldav_url=remote_todo.caldav_url,
                        last_sync=local_reminder.modification_date,
                    )

                    stats["errors"] += 1

            except Exception as e:
                msg = f"Failed to update remote TODO '{local_reminder.title}': {e}"
                logger.error(msg)
                error_messages.append(msg)
                stats["errors"] += 1

        # Unchanged pairs with no fingerprints (synced before they existed, or before
        # FINGERPRINT_VERSION changed) get them now, so later edits to their alarms and
        # recurrence rules are recognised
        baseline = {}
        for local_uuid in plan["unchanged"]:
            mapping = db_mappings.get(local_uuid)
            if mapping is None or self._stored_fingerprints(mapping) is not None:
                continue
            local_reminder = local_by_uuid.get(local_uuid)
            remote_todo = remote_by_uid.get(mapping["remote_uid"])
            if local_reminder and remote_todo:
                baseline[local_uuid] = json.dumps(
                    self._sync_fingerprints(local_reminder, remote_todo)
                )
        if baseline:
            try:
                await self.db.set_sync_fingerprints(baseline)
            except Exception as e:
                logger.warning(f"Could not record fingerprints for unchanged reminders: {e}")

        # Delete from Apple Reminders
        for local_uuid, local_reminder in plan["delete_local"]:
            try:
                success = await self.reminders_adapter.delete_reminder(local_uuid)
                if success:
                    # Remove from database
                    await self.db.delete_mapping(local_uuid=local_uuid)
                    stats["deleted_local"] += 1
                    logger.info(f"Deleted from Apple Reminders: {local_reminder.title}")
                else:
                    msg = f"Failed to delete local reminder '{local_reminder.title}'"
                    logger.error(msg)
                    error_messages.append(msg)
                    stats["errors"] += 1

            except Exception as e:
                msg = f"Failed to delete local reminder '{local_reminder.title}': {e}"
                logger.error(msg)
                error_messages.append(msg)
                stats["errors"] += 1

        # Delete from CalDAV
        for remote_uid, remote_todo in plan["delete_remote"]:
            try:
                success = await self.caldav_adapter.delete_todo(remote_todo.caldav_url)
                if success:
                    # Remove from database
                    await self.db.delete_mapping(remote_uid=remote_uid)
                    stats["deleted_remote"] += 1
                    logger.info(f"Deleted from CalDAV: {remote_todo.summary}")
                else:
                    msg = f"Failed to delete remote TODO '{remote_todo.summary}'"
                    logger.error(msg)
                    error_messages.append(msg)
                    stats["errors"] += 1

            except Exception as e:
                msg = f"Failed to delete remote TODO '{remote_todo.summary}': {e}"
                logger.error(msg)
                error_messages.append(msg)
                stats["errors"] += 1

        stats["error_messages"] = error_messages
        return stats

    def _convert_priority_to_apple(self, caldav_priority: int) -> int:
        """
        Convert CalDAV priority to Apple priority.

        CalDAV: 0=undefined, 1=highest, 9=lowest
        Apple: 0=none, 1-4=high, 5-9=medium/low

        Mapping:
        - 0 (undefined) → 0 (none)
        - 1-3 (high) → 1-3 (high)
        - 4-6 (medium) → 5-7 (medium)
        - 7-9 (low) → 8-9 (low)
        """
        if caldav_priority == 0:
            return 0
        elif caldav_priority <= 3:
            return caldav_priority
        elif caldav_priority <= 6:
            return caldav_priority + 1
        else:
            return min(caldav_priority + 1, 9)

    def _convert_priority_to_caldav(self, apple_priority: int) -> int:
        """
        Convert Apple priority to CalDAV priority.

        Apple: 0=none, 1-4=high, 5-9=medium/low
        CalDAV: 0=undefined, 1=highest, 9=lowest

        Mapping:
        - 0 (none) → 0 (undefined)
        - 1-4 (high) → 1-4 (high)
        - 5-7 (medium) → 5-6 (medium)
        - 8-9 (low) → 7-9 (low)
        """
        if apple_priority == 0:
            return 0
        elif apple_priority <= 4:
            return apple_priority
        elif apple_priority <= 7:
            return apple_priority - 1
        else:
            return min(apple_priority - 1, 9)

    def _sync_fingerprints(
        self, local_reminder: EventKitReminder, remote_todo: CalDAVReminder
    ) -> dict:
        """
        Fingerprint each side's alarms and recurrence rules, as they sync to the other side.

        Stored with the mapping after each sync, so the next one can tell whether a side
        changed them in the meantime.
        """
        return {
            "version": FINGERPRINT_VERSION,
            "apple": {
                "alarms": _fingerprint(self._convert_alarms_to_caldav(local_reminder.alarms)),
                "recurrence": _fingerprint(
                    self._convert_recurrence_to_caldav(local_reminder.recurrence_rules)
                ),
            },
            "caldav": {
                "alarms": _fingerprint(self._convert_alarms_to_eventkit(remote_todo.alarms)),
                "recurrence": _fingerprint(
                    self._convert_recurrence_to_eventkit(remote_todo.recurrence_rules)
                ),
            },
        }

    @staticmethod
    def _stored_fingerprints(mapping: dict | None) -> dict | None:
        """A mapping's stored fingerprints, or None if it has none from this version."""
        try:
            stored = json.loads(mapping["sync_fingerprints"])
        except (TypeError, KeyError, ValueError):
            return None
        return stored if stored.get("version") == FINGERPRINT_VERSION else None

    @staticmethod
    def _changed_since_sync(stored: dict | None, current: dict, side: str, field: str) -> bool:
        """
        Whether one side's alarms or recurrence rules changed since the pair last synced.

        With nothing stored to compare with, this says no. The field is then left alone,
        rather than overwritten from a side that may never have been sent it.
        """
        return stored is not None and stored[side][field] != current[side][field]

    def _convert_alarms_to_caldav(self, eventkit_alarms: list[ReminderAlarm]) -> list[CalDAVAlarm]:
        """
        Convert EventKit alarms to CalDAV alarms.

        Args:
            eventkit_alarms: List of EventKit ReminderAlarm objects

        Returns:
            List of CalDAVAlarm objects
        """
        if not eventkit_alarms:
            return []

        caldav_alarms = []
        for alarm in eventkit_alarms:
            if alarm.trigger_date is not None:
                caldav_alarms.append(CalDAVAlarm(trigger_date=alarm.trigger_date))
            elif alarm.relative_offset is not None:
                # EventKit alarm relative_offset is in seconds, negative = before due date
                # CalDAV alarm trigger_minutes is positive = before due date
                trigger_minutes = int(-alarm.relative_offset / 60)
                caldav_alarms.append(CalDAVAlarm(trigger_minutes=trigger_minutes))
            # Location alarms have neither and aren't synced
        return caldav_alarms

    def _convert_alarms_to_eventkit(self, caldav_alarms: list[CalDAVAlarm]) -> list[ReminderAlarm]:
        """
        Convert CalDAV alarms to EventKit alarms.

        Args:
            caldav_alarms: List of CalDAVAlarm objects

        Returns:
            List of EventKit ReminderAlarm objects
        """
        if not caldav_alarms:
            return []

        eventkit_alarms = []
        for alarm in caldav_alarms:
            if alarm.trigger_date is not None:
                eventkit_alarms.append(ReminderAlarm(trigger_date=alarm.trigger_date))
                continue
            # CalDAV alarm trigger_minutes is positive = before due date
            # EventKit alarm relative_offset is in seconds, negative = before due date
            relative_offset = -(alarm.trigger_minutes or 0) * 60
            eventkit_alarms.append(ReminderAlarm(relative_offset=relative_offset))
        return eventkit_alarms

    def _convert_recurrence_to_caldav(
        self, eventkit_recurrence: list[ReminderRecurrence]
    ) -> list[CalDAVRecurrence]:
        """
        Convert EventKit recurrence rules to CalDAV recurrence rules.

        Args:
            eventkit_recurrence: List of EventKit ReminderRecurrence objects

        Returns:
            List of CalDAVRecurrence objects
        """
        if not eventkit_recurrence:
            return []

        caldav_rules = []
        for rule in eventkit_recurrence:
            # Map EventKit frequency to CalDAV frequency
            frequency_map = {
                "daily": "DAILY",
                "weekly": "WEEKLY",
                "monthly": "MONTHLY",
                "yearly": "YEARLY",
            }
            frequency = frequency_map.get(rule.frequency.lower(), "DAILY")

            # Days of week, with their position if any: (3, 2) is 2TU, the second Tuesday
            by_day = [
                f"{position or ''}{WEEKDAY_CODES[day]}"
                for day, position in rule.days_of_week or []
                if day in WEEKDAY_CODES
            ]

            caldav_rules.append(
                CalDAVRecurrence(
                    frequency=frequency,
                    interval=rule.interval,
                    count=rule.occurrence_count,
                    until=rule.end_date,
                    by_day=by_day or None,
                    by_month_day=rule.days_of_month or None,
                    by_month=rule.months_of_year or None,
                    by_week_no=rule.weeks_of_year or None,
                    by_year_day=rule.days_of_year or None,
                    by_set_pos=rule.set_positions or None,
                )
            )
        return caldav_rules

    def _convert_recurrence_to_eventkit(
        self, caldav_recurrence: list[CalDAVRecurrence]
    ) -> list[ReminderRecurrence]:
        """
        Convert CalDAV recurrence rules to EventKit recurrence rules.

        Args:
            caldav_recurrence: List of CalDAVRecurrence objects

        Returns:
            List of EventKit ReminderRecurrence objects
        """
        if not caldav_recurrence:
            return []

        eventkit_rules = []
        for rule in caldav_recurrence:
            # ReminderRecurrence keeps the RRULE name; the EventKit adapter maps it
            frequency = rule.frequency.upper()
            if frequency not in RECURRENCE_FREQUENCIES:
                frequency = "DAILY"

            # Days of week, with their position if any: 2TU is (3, 2), the second Tuesday
            weekdays = {code: day for day, code in WEEKDAY_CODES.items()}
            days_of_week = []
            for by_day in rule.by_day or []:
                match = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", by_day.upper())
                if match and match.group(2) in weekdays:
                    days_of_week.append((weekdays[match.group(2)], int(match.group(1) or 0)))

            eventkit_rules.append(
                ReminderRecurrence(
                    frequency=frequency,
                    interval=rule.interval,
                    occurrence_count=rule.count,
                    end_date=rule.until,
                    days_of_week=days_of_week,
                    days_of_month=rule.by_month_day or None,
                    months_of_year=rule.by_month or None,
                    weeks_of_year=rule.by_week_no or None,
                    days_of_year=rule.by_year_day or None,
                    set_positions=rule.by_set_pos or None,
                )
            )
        return eventkit_rules

    async def reset_database(self) -> None:
        """Reset the database by clearing all mappings."""
        await self.db.clear_all_mappings()
        logger.info("Database reset complete")

    @staticmethod
    def _find_list_pair(
        pairs: list[dict],
        apple_calendar: ReminderCalendar | None,
        caldav_calendar: dict | None,
        apple_name: str,
        caldav_name: str,
    ) -> dict | None:
        """The recorded pair a sync is about: matched by identity first, then by name."""
        for pair in pairs:
            if (apple_calendar and pair["apple_uuid"] == apple_calendar.uuid) or (
                caldav_calendar and pair["caldav_url"] == caldav_calendar["url"]
            ):
                return pair
        for pair in pairs:
            if pair["apple_title"].lower() == apple_name.lower() or pair["caldav_name"].lower() == caldav_name.lower():
                return pair
        return None

    @staticmethod
    def _list_presence(
        pair: dict, apple_calendars: list[ReminderCalendar], caldav_calendars: list[dict]
    ) -> tuple[bool, bool]:
        """Whether each side of a pair still has its list.

        Identity comes first, so a renamed list still counts as present. The
        name is the fallback because Apple may issue a list a new identifier
        after a full resync.
        """
        apple_present = any(
            cal.uuid == pair["apple_uuid"] or cal.title.lower() == pair["apple_title"].lower()
            for cal in apple_calendars
        )
        caldav_present = any(
            cal["url"] == pair["caldav_url"] or cal["name"].lower() == pair["caldav_name"].lower()
            for cal in caldav_calendars
        )
        return apple_present, caldav_present

    @classmethod
    def _deleted_side(
        cls, pair: dict, apple_calendars: list[ReminderCalendar], caldav_calendars: list[dict]
    ) -> str | None:
        """Return the side ("apple" or "caldav") that alone has lost its list, else None."""
        apple_present, caldav_present = cls._list_presence(pair, apple_calendars, caldav_calendars)
        if apple_present == caldav_present:
            return None
        return "caldav" if apple_present else "apple"

    async def _handle_deleted_list(
        self,
        apple_calendars: list[ReminderCalendar],
        target_apple_calendar: ReminderCalendar | None,
        target_caldav_calendar: dict | None,
        apple_calendar_name: str,
        caldav_calendar_name: str,
        stats: dict,
        dry_run: bool,
        skip_deletions: bool,
    ) -> bool:
        """
        Deal with a pair whose list was deleted on one side since it last synced.

        By default the deletion waits for the user to confirm it, and the pair
        is not synced meanwhile. With auto_delete_lists, the other side's list
        is deleted too, unless every synced list vanished from the same side
        at once, which looks more like a fault than a decision.

        Returns:
            True if the pair must not be synced this run
        """
        pairs = await self.db.get_list_pairs()
        pair = self._find_list_pair(
            pairs, target_apple_calendar, target_caldav_calendar, apple_calendar_name, caldav_calendar_name
        )
        if pair is None:
            return False

        caldav_calendars = await self.caldav_adapter.list_calendar_urls()
        deleted_side = self._deleted_side(pair, apple_calendars, caldav_calendars)

        if deleted_side is None:
            if pair["deleted_side"] and not dry_run:
                logger.info(f"List '{pair['apple_title']}' is back on both sides; syncing it again")
                await self.db.set_list_pair_deleted_side(pair["id"], None)
            return False

        # A list can also vanish because its account was signed out, or the
        # server could not be read. Neither is the user deleting it.
        if deleted_side == "apple" and pair["apple_source_id"] not in await self.reminders_adapter.list_source_ids():
            raise SourceUnavailableError(
                f"The Apple Reminders account that holds '{pair['apple_title']}' is not available, "
                "so the list cannot be synced. Check that the account is signed in and Reminders "
                "is turned on for it."
            )
        if deleted_side == "caldav" and not caldav_calendars:
            raise SourceUnavailableError(
                f"The CalDAV server returned no calendars, so '{pair['caldav_name']}' cannot be "
                "synced. Check that the server is reachable and your credentials are correct."
            )

        where = "Apple Reminders" if deleted_side == "apple" else "CalDAV"
        delete_now = self.auto_delete_lists and not skip_deletions
        if delete_now and len(pairs) > 1 and all(
            self._deleted_side(other, apple_calendars, caldav_calendars) == deleted_side for other in pairs
        ):
            logger.warning(f"Every synced list is missing from {where} at once; asking before deleting any")
            delete_now = False

        if dry_run:
            logger.info(
                f"Dry run: '{pair['apple_title']}' was deleted in {where} and would be "
                f"{'deleted on the other side' if delete_now else 'held until you confirm'}"
            )
        elif delete_now:
            logger.info(f"'{pair['apple_title']}' was deleted in {where}; deleting it on the other side")
            await self._delete_list(pair, "caldav" if deleted_side == "apple" else "apple")
        elif pair["deleted_side"] != deleted_side:
            logger.info(f"'{pair['apple_title']}' was deleted in {where}; waiting for you to confirm")
            await self.db.set_list_pair_deleted_side(pair["id"], deleted_side)

        stats["lists_deleted" if delete_now else "lists_pending"] = 1
        return True

    async def _delete_list(self, pair: dict, side: str) -> None:
        """Delete a pair's list on one side, then forget the pair and its reminders."""
        if side == "caldav":
            calendars = await self.caldav_adapter.list_calendar_urls()
            target = next((c for c in calendars if c["url"] == pair["caldav_url"]), None) or next(
                (c for c in calendars if c["name"].lower() == pair["caldav_name"].lower()), None
            )
            deleted = target is not None and await self.caldav_adapter.delete_calendar(target["url"])
            name, where = pair["caldav_name"], "the CalDAV server"
        else:
            calendars = await self.reminders_adapter.list_calendars()
            target = next((c for c in calendars if c.uuid == pair["apple_uuid"]), None) or next(
                (c for c in calendars if c.title.lower() == pair["apple_title"].lower()), None
            )
            deleted = target is not None and await self.reminders_adapter.delete_calendar(target.uuid)
            name, where = pair["apple_title"], "Apple Reminders"

        if not deleted:
            raise RuntimeError(f"Could not delete '{name}' from {where}")

        await self.db.forget_list_pair(pair)

    async def confirm_list_deletion(self, pair_id: int) -> dict:
        """
        Delete the copy left behind by a list the user deleted on the other side.

        Checks again first: the list may have come back since the last sync.

        Returns:
            The pair that was deleted
        """
        pair = await self.db.get_list_pair(pair_id)
        if pair is None or not pair["deleted_side"]:
            raise LookupError("No deleted list with that ID is waiting to be confirmed.")

        apple_calendars = await self.reminders_adapter.list_calendars()
        if not apple_calendars:
            raise SourceUnavailableError(
                "Apple Reminders returned no lists, so nothing was deleted. Restart iCloudBridge "
                "and re-grant Reminders access if prompted."
            )
        caldav_calendars = await self.caldav_adapter.list_calendar_urls()
        apple_present, caldav_present = self._list_presence(pair, apple_calendars, caldav_calendars)

        if apple_present and caldav_present:
            await self.db.set_list_pair_deleted_side(pair_id, None)
            raise ValueError(f"'{pair['apple_title']}' exists on both sides again, so nothing was deleted.")

        if apple_present or caldav_present:
            await self._delete_list(pair, "apple" if apple_present else "caldav")
        else:
            # Already gone from both sides; only the records are left
            await self.db.forget_list_pair(pair)
        return pair

    async def _record_list_pair(self, apple_name: str, caldav_name: str) -> None:
        """Remember that two lists synced, so that a later deletion can be recognised."""
        apple = next(
            (c for c in await self.reminders_adapter.list_calendars() if c.title.lower() == apple_name.lower()),
            None,
        )
        caldav = next(
            (c for c in await self.caldav_adapter.list_calendar_urls() if c["name"].lower() == caldav_name.lower()),
            None,
        )
        if apple is None or caldav is None:
            logger.warning(f"Could not record {apple_name} → {caldav_name} as synced: a list was not found")
            return
        await self.db.save_list_pair(apple.uuid, apple.title, apple.source_id, caldav["url"], caldav["name"])

    async def sync_all_calendars(
        self,
        calendar_mappings: dict[str, str],
        dry_run: bool = False,
        skip_deletions: bool = False,
        deletion_threshold: int = 5,
    ) -> dict[str, dict[str, int]]:
        """
        Sync multiple calendar pairs based on mappings.

        Args:
            calendar_mappings: Dict mapping Apple calendar names → CalDAV calendar names
                              e.g., {"Reminders": "tasks", "Work": "work-tasks"}
            dry_run: If True, preview changes without applying them
            skip_deletions: If True, skip all deletion operations
            deletion_threshold: Prompt user if deletions exceed this count

        Returns:
            Dict mapping calendar pairs to their sync statistics
        """
        # Set up file logging for this sync operation (all calendars)
        log_dir = Path.home() / ".icloudbridge" / "log" / "reminder_sync"
        file_handler = setup_sync_file_logging(log_dir)

        try:
            all_stats = {}

            for apple_cal, caldav_cal in calendar_mappings.items():
                logger.info(f"Syncing: {apple_cal} → {caldav_cal}")
                try:
                    stats = await self.sync_calendar(
                        apple_calendar_name=apple_cal,
                        caldav_calendar_name=caldav_cal,
                        dry_run=dry_run,
                        skip_deletions=skip_deletions,
                        deletion_threshold=deletion_threshold,
                    )
                    stats.setdefault("error_messages", [])
                    all_stats[f"{apple_cal} → {caldav_cal}"] = stats
                except Exception as e:
                    logger.error(f"Failed to sync {apple_cal} → {caldav_cal}: {e}")
                    all_stats[f"{apple_cal} → {caldav_cal}"] = {
                        "created_local": 0,
                        "created_remote": 0,
                        "updated_local": 0,
                        "updated_remote": 0,
                        "deleted_local": 0,
                        "deleted_remote": 0,
                        "unchanged": 0,
                        "errors": 1,
                        "error_messages": [str(e)],
                    }

            return all_stats

        finally:
            # Clean up file logging handler
            file_handler.flush()  # Ensure all logs are written to disk
            logging.getLogger().removeHandler(file_handler)
            file_handler.close()
            logger.debug("File logging handler removed")

    async def discover_and_sync_all(
        self,
        base_mappings: dict[str, str] | None = None,
        dry_run: bool = False,
        skip_deletions: bool = False,
        deletion_threshold: int = 5,
    ) -> dict[str, dict[str, int]]:
        """
        Auto-discover calendars on both sides and sync them.

        This creates a unified view where:
        - Base mappings are used first (e.g., "Reminders" → "tasks")
        - Additional Apple calendars get CalDAV calendars created with matching names
        - Additional CalDAV calendars get Apple calendars created with matching names

        Args:
            base_mappings: Base calendar mappings (default: {"Reminders": "tasks"})
            dry_run: If True, preview changes without applying them
            skip_deletions: If True, skip all deletion operations
            deletion_threshold: Prompt user if deletions exceed this count

        Returns:
            Dict mapping calendar pairs to their sync statistics
        """
        if base_mappings is None:
            base_mappings = {"Reminders": "tasks"}

        # Fetch all calendars from both sides
        apple_calendars = await self.reminders_adapter.list_calendars()
        caldav_calendars_list = await self.caldav_adapter.list_calendars()

        apple_cal_names = {cal.title for cal in apple_calendars}

        # Filter CalDAV calendars to only include TODO-capable calendars
        # Skip VEVENT-only calendars (regular event calendars, not task calendars)
        caldav_todo_calendars = []
        for cal_dict in caldav_calendars_list:
            cal_name = cal_dict["name"]
            # Get the actual calendar object to check supported components
            cal_obj = None
            for c in self.caldav_adapter.calendars:
                if c.name == cal_name:
                    cal_obj = c
                    break

            if cal_obj:
                try:
                    supported = cal_obj.get_supported_components()
                    # Only include calendars that support VTODO
                    if "VTODO" in supported:
                        caldav_todo_calendars.append(cal_name)
                        logger.debug(f"CalDAV calendar '{cal_name}' supports TODO: {supported}")
                    else:
                        logger.info(f"Skipping CalDAV calendar '{cal_name}' (VEVENT-only, not a task calendar)")
                except Exception as e:
                    # If we can't determine, include it (fail-safe)
                    logger.warning(f"Could not determine supported components for '{cal_name}': {e}")
                    caldav_todo_calendars.append(cal_name)

        caldav_cal_names = set(caldav_todo_calendars)

        logger.info(f"Discovered {len(apple_cal_names)} Apple calendars: {apple_cal_names}")
        logger.info(f"Discovered {len(caldav_cal_names)} TODO-capable CalDAV calendars: {caldav_cal_names}")

        # Build complete mapping
        mappings: dict[str, str] = {}
        mapped_caldav_lower: set[str] = set()

        # Create a case-insensitive lookup for CalDAV calendars
        caldav_cal_lookup = {name.lower(): name for name in caldav_cal_names}
        # Create a case-insensitive lookup for Apple calendars
        apple_cal_lookup = {name.lower(): name for name in apple_cal_names}

        # Normalize base mappings using discovered CalDAV calendars (case-insensitive match)
        for apple_name, caldav_name in base_mappings.items():
            canonical = caldav_cal_lookup.get(caldav_name.lower(), caldav_name)
            mappings[apple_name] = canonical
            mapped_caldav_lower.add(canonical.lower())

        # Add unmapped Apple calendars (try to match existing CalDAV calendar first)
        for apple_name in apple_cal_names:
            if apple_name not in mappings:
                # Try exact match first (case-sensitive)
                if apple_name in caldav_cal_names:
                    caldav_name = apple_name
                    logger.info(f"Auto-mapping (exact): {apple_name} → {caldav_name}")
                # Try case-insensitive match
                elif apple_name.lower() in caldav_cal_lookup:
                    caldav_name = caldav_cal_lookup[apple_name.lower()]
                    logger.info(f"Auto-mapping (case-insensitive): {apple_name} → {caldav_name}")
                # Try with spaces replaced by dashes (case-insensitive)
                elif apple_name.lower().replace(" ", "-") in caldav_cal_lookup:
                    caldav_name = caldav_cal_lookup[apple_name.lower().replace(" ", "-")]
                    logger.info(f"Auto-mapping (normalized): {apple_name} → {caldav_name}")
                else:
                    # No match found - keep Apple name as-is for CalDAV
                    # This preserves spaces and capitalization
                    caldav_name = apple_name
                    logger.info(f"Auto-mapping (new): {apple_name} → {caldav_name}")

                mappings[apple_name] = caldav_name
                mapped_caldav_lower.add(caldav_name.lower())

        # Add unmapped CalDAV calendars (create corresponding Apple calendar)
        # Skip system calendars like "Deck: Welcome to Nextcloud Deck!"
        for caldav_name in caldav_cal_names:
            # Check if this CalDAV calendar is already mapped (as a value in mappings)
            already_mapped = caldav_name.lower() in mapped_caldav_lower

            # Skip system/special calendars (Deck, etc.)
            is_system_calendar = caldav_name.startswith("Deck:")

            if not already_mapped and not is_system_calendar:
                # Try exact match first (case-sensitive)
                if caldav_name in apple_cal_names:
                    apple_name = caldav_name
                    logger.info(f"Auto-mapping (exact reverse): {apple_name} → {caldav_name}")
                # Try case-insensitive match
                elif caldav_name.lower() in apple_cal_lookup:
                    apple_name = apple_cal_lookup[caldav_name.lower()]
                    logger.info(f"Auto-mapping (case-insensitive reverse): {apple_name} → {caldav_name}")
                else:
                    # No match found - keep CalDAV name as-is for Apple
                    apple_name = caldav_name
                    logger.info(f"Auto-mapping (new reverse): {apple_name} ← {caldav_name}")

                mappings[apple_name] = caldav_name
                mapped_caldav_lower.add(caldav_name.lower())

        # Sync all mapped calendars
        return await self.sync_all_calendars(
            calendar_mappings=mappings,
            dry_run=dry_run,
            skip_deletions=skip_deletions,
            deletion_threshold=deletion_threshold,
        )
