"""EventKit adapter for interfacing with Apple Reminders.app."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import EventKit
from EventKit import (
    EKAlarm,
    EKEntityTypeReminder,
    EKEventStore,
    EKRecurrenceDayOfWeek,
    EKRecurrenceEnd,
    EKRecurrenceFrequency,
    EKRecurrenceFrequencyDaily,
    EKRecurrenceFrequencyWeekly,
    EKRecurrenceFrequencyMonthly,
    EKRecurrenceFrequencyYearly,
    EKRecurrenceRule,
    EKReminder,
)

logger = logging.getLogger(__name__)


def normalize_date(dt: Any) -> datetime | None:
    """
    Convert various date types to Python datetime with UTC timezone.

    Handles:
    - Python datetime objects (ensures UTC timezone)
    - Apple NSDate objects (uses timeIntervalSince1970())
    - Any object with timestamp() method
    - Any object with isoformat() method
    - None values

    Args:
        dt: Date object to normalize (datetime, NSDate, or other date-like object)

    Returns:
        Normalized datetime with UTC timezone, or None if conversion fails
    """
    if dt is None:
        return None

    # Already a Python datetime - just ensure UTC timezone
    if isinstance(dt, datetime):
        tz = getattr(dt, "tzinfo", None)
        if tz is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    # Try to get Unix timestamp
    timestamp = None

    # Try Python's timestamp() method first
    timestamp_getter = getattr(dt, "timestamp", None)
    if callable(timestamp_getter):
        try:
            timestamp = float(timestamp_getter())
        except Exception:
            timestamp = None

    # Try Apple's NSDate method timeIntervalSince1970()
    if timestamp is None:
        alt_getter = getattr(dt, "timeIntervalSince1970", None)
        if callable(alt_getter):
            try:
                timestamp = float(alt_getter())
            except Exception:
                timestamp = None

    # Convert timestamp to datetime
    if timestamp is not None:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)

    # Try isoformat parsing as last resort
    iso_getter = getattr(dt, "isoformat", None)
    if callable(iso_getter):
        try:
            parsed = datetime.fromisoformat(iso_getter())
            tz = getattr(parsed, "tzinfo", None)
            if tz is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            pass

    # Could not convert
    logger.warning(f"Could not normalize date of type {type(dt)}: {dt}")
    return None


@dataclass
class ReminderAlarm:
    """Represents an alarm/notification for a reminder."""

    trigger_date: datetime | None = None
    relative_offset: int | None = None  # Seconds before/after due date


@dataclass
class ReminderRecurrence:
    """Represents a recurrence rule for a reminder."""

    frequency: str  # DAILY, WEEKLY, MONTHLY, YEARLY
    interval: int = 1  # Every X days/weeks/months/years
    end_date: datetime | None = None
    occurrence_count: int | None = None
    days_of_week: list[int] = field(default_factory=list)  # 0=Sunday, 1=Monday, etc.
    days_of_month: list[int] | None = None  # Days of month (1-31) for monthly recurrence


@dataclass
class EventKitReminder:
    """Represents a reminder from Apple Reminders.app via EventKit."""

    uuid: str
    title: str
    notes: str | None
    completed: bool
    priority: int  # 0=none, 1-4=high, 5-9=medium/low
    due_date: datetime | None
    creation_date: datetime
    modification_date: datetime
    completion_date: datetime | None
    calendar_id: str  # Calendar/List UUID
    calendar_name: str
    alarms: list[ReminderAlarm] = field(default_factory=list)
    recurrence_rules: list[ReminderRecurrence] = field(default_factory=list)
    url: str | None = None


@dataclass
class ReminderCalendar:
    """Represents a calendar/list in Apple Reminders.app."""

    uuid: str
    title: str
    reminder_count: int = 0


class RemindersAdapter:
    """Adapter for interfacing with Apple Reminders via EventKit."""

    # Class-level shared EventKit store to avoid hitting Apple's instance limit
    _shared_store: EKEventStore | None = None
    _access_granted: bool = False
    _store_lock = asyncio.Lock()

    def __init__(self):
        """Initialize the EventKit store (reuses shared instance)."""
        # Use the shared store instance to avoid creating too many EKEventStore instances
        # Apple limits the number of EKEventStore instances per process
        if RemindersAdapter._shared_store is None:
            RemindersAdapter._shared_store = EKEventStore.alloc().init()
            logger.debug("Created shared EKEventStore instance")

        self.store = RemindersAdapter._shared_store

    @classmethod
    def reset_shared_store(cls) -> None:
        """Reset the shared EventKit store. Useful for cleanup or testing."""
        cls._shared_store = None
        cls._access_granted = False
        logger.debug("Reset shared EKEventStore instance")

    async def request_access(self) -> bool:
        """Request access to Reminders. Returns True if granted."""
        if RemindersAdapter._access_granted:
            return True

        # Create a future to wait for the callback
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        def callback(granted: bool, error: Any) -> None:
            if not future.done():
                # Call from ObjC thread - must use call_soon_threadsafe
                def set_result():
                    if granted:
                        logger.info("EventKit access to Reminders granted")
                        future.set_result(True)
                    else:
                        logger.error(f"EventKit access denied: {error}")
                        future.set_result(False)

                loop.call_soon_threadsafe(set_result)

        self.store.requestFullAccessToRemindersWithCompletion_(callback)

        # Wait for callback to complete
        RemindersAdapter._access_granted = await future
        return RemindersAdapter._access_granted

    async def list_calendars(self) -> list[ReminderCalendar]:
        """List all reminder calendars/lists."""
        if not RemindersAdapter._access_granted:
            await self.request_access()

        calendars = self.store.calendarsForEntityType_(EKEntityTypeReminder)
        result = []

        for cal in calendars:
            result.append(
                ReminderCalendar(
                    uuid=cal.calendarIdentifier(),
                    title=cal.title(),
                )
            )

        logger.info(f"Found {len(result)} reminder calendars")
        return result

    async def create_calendar(self, calendar_name: str) -> ReminderCalendar | None:
        """
        Create a new reminder calendar/list.

        Args:
            calendar_name: Name of the calendar to create

        Returns:
            ReminderCalendar object if created successfully, None otherwise
        """
        if not RemindersAdapter._access_granted:
            await self.request_access()

        try:
            logger.info(f"Creating Apple Reminders calendar: {calendar_name}")

            # Get the default source for reminders (usually iCloud)
            sources = self.store.sources()
            default_source = None
            for source in sources:
                if source.sourceType() == 1:  # EKSourceTypeCalDAV (iCloud)
                    default_source = source
                    break

            # Fallback to local source if no CalDAV source found
            if not default_source and sources:
                default_source = sources[0]

            if not default_source:
                logger.error("No source available for creating calendar")
                return None

            # Create new calendar
            new_calendar = EventKit.EKCalendar.calendarForEntityType_eventStore_(
                EKEntityTypeReminder, self.store
            )
            new_calendar.setTitle_(calendar_name)
            new_calendar.setSource_(default_source)

            # Save to store
            error = None
            success = self.store.saveCalendar_commit_error_(new_calendar, True, None)

            if success:
                logger.info(f"Successfully created calendar: {calendar_name}")
                return ReminderCalendar(
                    uuid=new_calendar.calendarIdentifier(),
                    title=new_calendar.title(),
                )
            else:
                logger.error(f"Failed to create calendar: {calendar_name}")
                return None

        except Exception as e:
            logger.error(f"Failed to create calendar '{calendar_name}': {e}", exc_info=True)
            return None

    async def get_reminders(
        self, calendar_id: str | None = None, calendar_name: str | None = None
    ) -> list[EventKitReminder]:
        """
        Get all reminders from a specific calendar.

        Args:
            calendar_id: Calendar UUID to fetch from
            calendar_name: Calendar name to fetch from (alternative to calendar_id)

        Returns:
            List of EventKitReminder objects
        """
        if not RemindersAdapter._access_granted:
            await self.request_access()

        # Find the calendar
        calendars = self.store.calendarsForEntityType_(EKEntityTypeReminder)

        target_calendar = None
        if calendar_id:
            for cal in calendars:
                if cal.calendarIdentifier() == calendar_id:
                    target_calendar = cal
                    break
        elif calendar_name:
            for cal in calendars:
                if cal.title() == calendar_name:
                    target_calendar = cal
                    break

        if not target_calendar:
            logger.warning(f"Calendar not found: {calendar_id or calendar_name}")
            return []

        # Create predicate for fetching reminders
        predicate = self.store.predicateForRemindersInCalendars_([target_calendar])

        # Create future for async fetch
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        def fetch_callback(reminders: list) -> None:
            if not future.done():
                # Call from ObjC thread - must use call_soon_threadsafe
                loop.call_soon_threadsafe(future.set_result, reminders)

        self.store.fetchRemindersMatchingPredicate_completion_(predicate, fetch_callback)

        # Wait for fetch to complete
        ek_reminders = await future

        # Convert to our dataclass format
        result = []
        for r in ek_reminders:
            result.append(self._convert_from_eventkit(r))

        logger.info(
            f"Fetched {len(result)} reminders from calendar '{target_calendar.title()}'"
        )
        return result

    def _convert_from_eventkit(self, ek_reminder: EKReminder) -> EventKitReminder:
        """Convert an EKReminder to our EventKitReminder dataclass."""
        # Extract due date from NSDateComponents
        due_date = None
        if ek_reminder.dueDateComponents():
            dc = ek_reminder.dueDateComponents()
            # Convert NSDateComponents to datetime
            # This is a simplification - may need better handling
            try:
                due_date = datetime(
                    year=dc.year() if dc.year() else 1,
                    month=dc.month() if dc.month() else 1,
                    day=dc.day() if dc.day() else 1,
                    hour=dc.hour() if dc.hour() else 0,
                    minute=dc.minute() if dc.minute() else 0,
                    second=dc.second() if dc.second() else 0,
                    tzinfo=timezone.utc,
                )
            except (ValueError, AttributeError) as e:
                logger.warning(f"Could not parse due date: {e}")
                due_date = None

        # Extract alarms
        alarms = []
        if ek_reminder.hasAlarms():
            for alarm in ek_reminder.alarms():
                alarm_obj = ReminderAlarm()
                if alarm.absoluteDate():
                    alarm_obj.trigger_date = normalize_date(alarm.absoluteDate())
                elif alarm.relativeOffset():
                    alarm_obj.relative_offset = int(alarm.relativeOffset())
                alarms.append(alarm_obj)

        # Extract recurrence rules
        recurrence_rules = []
        if ek_reminder.hasRecurrenceRules():
            for rule in ek_reminder.recurrenceRules():
                freq_map = {
                    EKRecurrenceFrequencyDaily: "DAILY",
                    EKRecurrenceFrequencyWeekly: "WEEKLY",
                    EKRecurrenceFrequencyMonthly: "MONTHLY",
                    EKRecurrenceFrequencyYearly: "YEARLY",
                }
                frequency = freq_map.get(rule.frequency(), "DAILY")

                rec_obj = ReminderRecurrence(
                    frequency=frequency,
                    interval=rule.interval(),
                )

                # Extract end date or occurrence count
                if rule.recurrenceEnd():
                    end = rule.recurrenceEnd()
                    if end.endDate():
                        rec_obj.end_date = normalize_date(end.endDate())
                    elif end.occurrenceCount():
                        rec_obj.occurrence_count = end.occurrenceCount()

                # Extract days of week
                if rule.daysOfTheWeek():
                    rec_obj.days_of_week = [day.dayOfTheWeek() for day in rule.daysOfTheWeek()]

                recurrence_rules.append(rec_obj)

        return EventKitReminder(
            uuid=ek_reminder.calendarItemIdentifier(),
            title=ek_reminder.title() or "",
            notes=ek_reminder.notes(),
            completed=ek_reminder.isCompleted(),
            priority=ek_reminder.priority(),
            due_date=due_date,
            creation_date=normalize_date(ek_reminder.creationDate()),
            modification_date=normalize_date(ek_reminder.lastModifiedDate()),
            completion_date=normalize_date(ek_reminder.completionDate()),
            calendar_id=ek_reminder.calendar().calendarIdentifier(),
            calendar_name=ek_reminder.calendar().title(),
            alarms=alarms,
            recurrence_rules=recurrence_rules,
            url=str(ek_reminder.URL()) if ek_reminder.URL() else None,
        )

    async def create_reminder(
        self,
        calendar_id: str,
        title: str,
        notes: str | None = None,
        completed: bool = False,
        priority: int = 0,
        due_date: datetime | None = None,
        alarms: list[ReminderAlarm] | None = None,
        recurrence_rules: list[ReminderRecurrence] | None = None,
        url: str | None = None,
    ) -> EventKitReminder:
        """
        Create a new reminder in the specified calendar.

        Returns:
            The created EventKitReminder object
        """
        if not RemindersAdapter._access_granted:
            await self.request_access()

        # Find the calendar
        calendars = self.store.calendarsForEntityType_(EKEntityTypeReminder)
        target_calendar = None
        for cal in calendars:
            if cal.calendarIdentifier() == calendar_id:
                target_calendar = cal
                break

        if not target_calendar:
            raise ValueError(f"Calendar not found: {calendar_id}")

        # Create the reminder
        reminder = EKReminder.reminderWithEventStore_(self.store)
        reminder.setTitle_(title)
        reminder.setCalendar_(target_calendar)

        if notes:
            reminder.setNotes_(notes)

        reminder.setCompleted_(completed)
        reminder.setPriority_(priority)

        # Set due date
        if due_date:
            from Foundation import NSCalendar, NSDateComponents

            components = NSDateComponents.alloc().init()
            components.setYear_(due_date.year)
            components.setMonth_(due_date.month)
            components.setDay_(due_date.day)
            components.setHour_(due_date.hour)
            components.setMinute_(due_date.minute)
            components.setSecond_(due_date.second)
            components.setCalendar_(NSCalendar.currentCalendar())
            reminder.setDueDateComponents_(components)

        # Add alarms
        if alarms:
            for alarm_data in alarms:
                alarm = EKAlarm.alloc().init()
                if alarm_data.trigger_date:
                    alarm.setAbsoluteDate_(alarm_data.trigger_date)
                elif alarm_data.relative_offset:
                    alarm.setRelativeOffset_(alarm_data.relative_offset)
                reminder.addAlarm_(alarm)

        # Add recurrence rules
        if recurrence_rules:
            for rec_data in recurrence_rules:
                freq_map = {
                    "DAILY": EKRecurrenceFrequencyDaily,
                    "WEEKLY": EKRecurrenceFrequencyWeekly,
                    "MONTHLY": EKRecurrenceFrequencyMonthly,
                    "YEARLY": EKRecurrenceFrequencyYearly,
                }
                frequency = freq_map.get(rec_data.frequency, EKRecurrenceFrequencyDaily)

                # Create recurrence end
                rec_end = None
                if rec_data.end_date:
                    rec_end = EKRecurrenceEnd.recurrenceEndWithEndDate_(rec_data.end_date)
                elif rec_data.occurrence_count:
                    rec_end = EKRecurrenceEnd.recurrenceEndWithOccurrenceCount_(
                        rec_data.occurrence_count
                    )

                # Create days of week
                days_of_week = None
                if rec_data.days_of_week:
                    days_of_week = [
                        EKRecurrenceDayOfWeek.dayOfWeek_(day) for day in rec_data.days_of_week
                    ]

                rule = EKRecurrenceRule.alloc().initRecurrenceWithFrequency_interval_daysOfTheWeek_daysOfTheMonth_monthsOfTheYear_weeksOfTheYear_daysOfTheYear_setPositions_end_(
                    frequency,
                    rec_data.interval,
                    days_of_week,
                    None,  # daysOfTheMonth
                    None,  # monthsOfTheYear
                    None,  # weeksOfTheYear
                    None,  # daysOfTheYear
                    None,  # setPositions
                    rec_end,
                )
                reminder.addRecurrenceRule_(rule)

        # Set URL
        if url:
            from Foundation import NSURL

            reminder.setURL_(NSURL.URLWithString_(url))

        # Save to store
        error = self.store.saveReminder_commit_error_(reminder, True, None)
        if error[0] is False:
            raise RuntimeError(f"Failed to create reminder: {error[2]}")

        logger.info(f"Created reminder: {title}")
        return self._convert_from_eventkit(reminder)

    async def update_reminder(
        self,
        uuid: str,
        title: str | None = None,
        notes: str | None = None,
        completed: bool | None = None,
        priority: int | None = None,
        due_date: datetime | None = None,
        alarms: list[ReminderAlarm] | None = None,
        recurrence_rules: list[ReminderRecurrence] | None = None,
        url: str | None = None,
    ) -> EventKitReminder:
        """
        Update an existing reminder by UUID.

        Returns:
            The updated EventKitReminder object
        """
        if not RemindersAdapter._access_granted:
            await self.request_access()

        # Fetch the reminder by UUID
        reminder = self.store.calendarItemWithIdentifier_(uuid)
        if not reminder:
            raise ValueError(f"Reminder not found: {uuid}")

        # Update fields
        if title is not None:
            reminder.setTitle_(title)
        if notes is not None:
            reminder.setNotes_(notes)
        if completed is not None:
            reminder.setCompleted_(completed)
        if priority is not None:
            reminder.setPriority_(priority)

        # Update due date
        if due_date is not None:
            from Foundation import NSCalendar, NSDateComponents

            components = NSDateComponents.alloc().init()
            components.setYear_(due_date.year)
            components.setMonth_(due_date.month)
            components.setDay_(due_date.day)
            components.setHour_(due_date.hour)
            components.setMinute_(due_date.minute)
            components.setSecond_(due_date.second)
            components.setCalendar_(NSCalendar.currentCalendar())
            reminder.setDueDateComponents_(components)

        # Update alarms (replace all)
        if alarms is not None:
            # Remove existing alarms
            for alarm in reminder.alarms():
                reminder.removeAlarm_(alarm)
            # Add new alarms
            for alarm_data in alarms:
                alarm = EKAlarm.alloc().init()
                if alarm_data.trigger_date:
                    alarm.setAbsoluteDate_(alarm_data.trigger_date)
                elif alarm_data.relative_offset:
                    alarm.setRelativeOffset_(alarm_data.relative_offset)
                reminder.addAlarm_(alarm)

        # Update recurrence rules (replace all)
        if recurrence_rules is not None:
            # Remove existing rules
            for rule in reminder.recurrenceRules():
                reminder.removeRecurrenceRule_(rule)
            # Add new rules
            for rec_data in recurrence_rules:
                freq_map = {
                    "DAILY": EKRecurrenceFrequencyDaily,
                    "WEEKLY": EKRecurrenceFrequencyWeekly,
                    "MONTHLY": EKRecurrenceFrequencyMonthly,
                    "YEARLY": EKRecurrenceFrequencyYearly,
                }
                frequency = freq_map.get(rec_data.frequency, EKRecurrenceFrequencyDaily)

                rec_end = None
                if rec_data.end_date:
                    rec_end = EKRecurrenceEnd.recurrenceEndWithEndDate_(rec_data.end_date)
                elif rec_data.occurrence_count:
                    rec_end = EKRecurrenceEnd.recurrenceEndWithOccurrenceCount_(
                        rec_data.occurrence_count
                    )

                days_of_week = None
                if rec_data.days_of_week:
                    days_of_week = [
                        EKRecurrenceDayOfWeek.dayOfWeek_(day) for day in rec_data.days_of_week
                    ]

                rule = EKRecurrenceRule.alloc().initWithRecurrenceWithFrequency_interval_daysOfTheWeek_daysOfTheMonth_monthsOfTheYear_weeksOfTheYear_daysOfTheYear_setPositions_end_(
                    frequency,
                    rec_data.interval,
                    days_of_week,
                    None,
                    None,
                    None,
                    None,
                    None,
                    rec_end,
                )
                reminder.addRecurrenceRule_(rule)

        # Update URL
        if url is not None:
            from Foundation import NSURL

            reminder.setURL_(NSURL.URLWithString_(url))

        # Save changes
        error = self.store.saveReminder_commit_error_(reminder, True, None)
        if error[0] is False:
            raise RuntimeError(f"Failed to update reminder: {error[2]}")

        logger.info(f"Updated reminder: {uuid}")
        return self._convert_from_eventkit(reminder)

    async def delete_reminder(self, uuid: str) -> bool:
        """
        Delete a reminder by UUID.

        Returns:
            True if deleted successfully, False otherwise
        """
        if not RemindersAdapter._access_granted:
            await self.request_access()

        # Fetch the reminder by UUID
        reminder = self.store.calendarItemWithIdentifier_(uuid)
        if not reminder:
            logger.warning(f"Reminder not found for deletion: {uuid}")
            return False

        # Delete the reminder
        error = self.store.removeReminder_commit_error_(reminder, True, None)
        if error[0] is False:
            logger.error(f"Failed to delete reminder: {error[2]}")
            return False

        logger.info(f"Deleted reminder: {uuid}")
        return True
