"""CalDAV adapter for syncing reminders to CalDAV servers (NextCloud, etc.)."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import caldav
from caldav import DAVClient, Todo
from icalendar import Alarm, Calendar
from icalendar import Todo as VTodo

logger = logging.getLogger(__name__)


@dataclass
class CalDAVAlarm:
    """Represents a VALARM component in CalDAV."""

    trigger_minutes: int  # Minutes before due date (positive = before, negative = after)


@dataclass
class CalDAVRecurrence:
    """Represents an RRULE component in CalDAV."""

    frequency: str  # DAILY, WEEKLY, MONTHLY, YEARLY
    interval: int  # Every N days/weeks/months/years
    count: int | None  # Number of occurrences (None = infinite)
    until: datetime | None  # End date (None = infinite)
    by_day: list[str] | None  # Days of week (MO, TU, WE, TH, FR, SA, SU)
    by_month_day: list[int] | None  # Days of month (1-31)


@dataclass
class CalDAVReminder:
    """Represents a reminder/TODO from CalDAV (VTODO format)."""

    uid: str
    summary: str
    description: str | None
    completed: bool
    priority: int  # 0=undefined, 1=highest, 9=lowest (inverse of Apple)
    due_date: datetime | None
    created: datetime
    last_modified: datetime
    completed_date: datetime | None
    url: str | None
    caldav_url: str  # URL to the VTODO resource on the server
    icalendar_data: str  # Raw iCalendar data
    alarms: list[CalDAVAlarm]  # List of alarms
    recurrence_rules: list[CalDAVRecurrence]  # List of recurrence rules


class CalDAVAdapter:
    """Adapter for syncing reminders with CalDAV servers."""

    def __init__(self, url: str, username: str, password: str):
        """
        Initialize CalDAV adapter.

        Args:
            url: CalDAV server URL (e.g., https://nextcloud.example.com/remote.php/dav)
            username: CalDAV username
            password: CalDAV password
        """
        self.url = url
        self.username = username
        self.password = password
        self.client: DAVClient | None = None
        self.principal: Any = None
        self.calendars: list[Any] = []

    async def connect(self) -> bool:
        """Connect to CalDAV server and discover calendars."""
        try:
            logger.debug(f"Connecting to CalDAV server: {self.url}")
            # Run blocking caldav operations in thread pool
            def _connect():
                logger.debug("Creating DAVClient...")
                client = DAVClient(url=self.url, username=self.username, password=self.password)
                logger.debug("Getting principal...")
                principal = client.principal()
                logger.debug("Getting calendars...")
                calendars = principal.calendars()
                logger.debug(f"Found {len(calendars)} calendars")
                return client, principal, calendars

            self.client, self.principal, self.calendars = await asyncio.to_thread(_connect)
            logger.info(f"Connected to CalDAV server: {self.url}")
            logger.info(f"Found {len(self.calendars)} calendars")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to CalDAV server: {e}", exc_info=True)
            return False

    async def list_calendars(self) -> list[dict[str, str]]:
        """
        List all available todo/task calendars on the server.

        Only returns calendars that support VTODO components (tasks/reminders).
        Filters out event-only calendars (VEVENT).

        Returns:
            List of dicts with 'name' and 'url' keys
        """
        if not self.client:
            await self.connect()

        result = []
        for cal in self.calendars:
            # Check if this calendar supports VTODO (tasks/reminders)
            # We only want todo-capable calendars for reminders sync
            try:
                # Get supported calendar component set
                supported_components = await asyncio.to_thread(
                    lambda: getattr(cal, 'get_supported_components', lambda: None)()
                )

                # If we can't determine the supported components, try to check if it has todos
                if supported_components is None:
                    # Try to fetch todos to see if this is a todo-capable calendar
                    # Some servers don't advertise component types properly
                    todos = await asyncio.to_thread(lambda: cal.todos(include_completed=True))
                    # If we can fetch todos without error, assume it's todo-capable
                    result.append({"name": cal.name, "url": str(cal.url)})
                elif "VTODO" in supported_components or "vtodo" in str(supported_components).lower():
                    # Calendar explicitly supports todos
                    result.append({"name": cal.name, "url": str(cal.url)})
                # else: skip this calendar as it doesn't support todos

            except Exception as e:
                # If we can't determine, log and skip
                logger.debug(f"Skipping calendar '{cal.name}': {e}")
                continue

        logger.info(f"Found {len(result)} todo-capable calendars out of {len(self.calendars)} total")
        return result

    async def create_calendar(self, calendar_name: str) -> bool:
        """
        Create a new TODO-only calendar on the CalDAV server.

        Args:
            calendar_name: Name of the calendar to create

        Returns:
            True if created successfully, False otherwise
        """
        if not self.client or not self.principal:
            await self.connect()

        try:
            logger.info(f"Creating TODO calendar: {calendar_name}")

            # Run blocking operation in thread pool
            # Create a TODO-only calendar (VTODO component only)
            def _create():
                return self.principal.make_calendar(
                    name=calendar_name, supported_calendar_component_set=["VTODO"]
                )

            new_calendar = await asyncio.to_thread(_create)

            # Refresh calendars list
            self.calendars = await asyncio.to_thread(self.principal.calendars)

            logger.info(f"Successfully created TODO calendar: {calendar_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to create calendar '{calendar_name}': {e}", exc_info=True)
            return False

    async def get_todos(self, calendar_name: str | None = None) -> list[CalDAVReminder]:
        """
        Fetch all TODOs from a specific calendar.

        Args:
            calendar_name: Name of the calendar to fetch from (if None, uses first calendar)

        Returns:
            List of CalDAVReminder objects
        """
        if not self.client:
            await self.connect()

        # Find the target calendar
        target_calendar = None
        if calendar_name:
            for cal in self.calendars:
                if cal.name == calendar_name:
                    target_calendar = cal
                    break
        else:
            # Use first calendar if no name specified
            target_calendar = self.calendars[0] if self.calendars else None

        if not target_calendar:
            logger.warning(f"Calendar not found: {calendar_name}")
            return []

        # Fetch all TODOs including completed ones - run blocking operation in thread pool
        todos = await asyncio.to_thread(target_calendar.todos, include_completed=True)
        result = []

        for todo in todos:
            try:
                reminder = self._parse_todo(todo)
                if reminder:
                    result.append(reminder)
            except Exception as e:
                logger.error(f"Failed to parse TODO: {e}")
                continue

        logger.info(f"Fetched {len(result)} TODOs from calendar '{target_calendar.name}'")
        return result

    def _parse_todo(self, todo: Todo) -> CalDAVReminder | None:
        """Parse a CalDAV Todo object into our CalDAVReminder dataclass."""
        try:
            # Get iCalendar data
            ical_data = todo.data
            cal = Calendar.from_ical(ical_data)

            # Find the VTODO component
            vtodo = None
            for component in cal.walk():
                if component.name == "VTODO":
                    vtodo = component
                    break

            if not vtodo:
                logger.warning("No VTODO component found")
                return None

            # Extract fields
            uid = str(vtodo.get("UID", ""))
            summary = str(vtodo.get("SUMMARY", ""))
            description = str(vtodo.get("DESCRIPTION", "")) if vtodo.get("DESCRIPTION") else None

            # Status and completion
            status = str(vtodo.get("STATUS", "NEEDS-ACTION"))
            completed = status == "COMPLETED"
            completed_date = vtodo.get("COMPLETED")
            if completed_date and hasattr(completed_date, "dt"):
                completed_date = completed_date.dt
            else:
                completed_date = None

            # Priority (CalDAV uses 0=undefined, 1=highest, 9=lowest)
            priority = int(vtodo.get("PRIORITY", 0))

            # Due date
            due_date = vtodo.get("DUE")
            if due_date and hasattr(due_date, "dt"):
                due_date = due_date.dt
            else:
                due_date = None

            # Timestamps
            created = vtodo.get("CREATED")
            if created and hasattr(created, "dt"):
                created = created.dt
            else:
                created = datetime.now()

            last_modified = vtodo.get("LAST-MODIFIED")
            if last_modified and hasattr(last_modified, "dt"):
                last_modified = last_modified.dt
            else:
                last_modified = datetime.now()

            # URL
            url_field = vtodo.get("URL")
            url = str(url_field) if url_field else None

            # Parse alarms (VALARM components)
            alarms = []
            for component in vtodo.walk():
                if component.name == "VALARM":
                    trigger = component.get("TRIGGER")
                    if trigger:
                        # Parse trigger duration
                        if hasattr(trigger, "dt"):
                            # Absolute time - convert to relative minutes
                            if due_date:
                                delta = due_date - trigger.dt
                                trigger_minutes = int(delta.total_seconds() / 60)
                                alarms.append(CalDAVAlarm(trigger_minutes=trigger_minutes))
                        else:
                            # Relative duration (e.g., -PT15M for 15 minutes before)
                            trigger_str = str(trigger)
                            if trigger_str.startswith("-PT") or trigger_str.startswith("PT"):
                                # Parse simple duration format
                                # -PT15M = 15 minutes before
                                # PT15M = 15 minutes after (unusual for todos)
                                is_before = trigger_str.startswith("-PT")
                                trigger_str = trigger_str.replace("-PT", "").replace("PT", "")

                                minutes = 0
                                if "H" in trigger_str:
                                    hours_str = trigger_str.split("H")[0]
                                    minutes += int(hours_str) * 60
                                    trigger_str = trigger_str.split("H")[1] if "H" in trigger_str else ""
                                if "M" in trigger_str:
                                    minutes_str = trigger_str.split("M")[0]
                                    minutes += int(minutes_str)

                                trigger_minutes = minutes if is_before else -minutes
                                alarms.append(CalDAVAlarm(trigger_minutes=trigger_minutes))

            # Parse recurrence rules (RRULE)
            recurrence_rules = []
            rrule = vtodo.get("RRULE")
            if rrule:
                freq = rrule.get("FREQ", [""])[0] if isinstance(rrule.get("FREQ"), list) else str(rrule.get("FREQ", ""))
                interval = int(rrule.get("INTERVAL", [1])[0]) if isinstance(rrule.get("INTERVAL"), list) else int(rrule.get("INTERVAL", 1))
                count = rrule.get("COUNT")
                count = int(count[0]) if isinstance(count, list) and count else (int(count) if count else None)
                until = rrule.get("UNTIL")
                if until:
                    until = until[0] if isinstance(until, list) else until
                    if hasattr(until, "dt"):
                        until = until.dt
                else:
                    until = None

                by_day = rrule.get("BYDAY")
                if by_day:
                    by_day = [str(d) for d in by_day] if isinstance(by_day, list) else [str(by_day)]
                else:
                    by_day = None

                by_month_day = rrule.get("BYMONTHDAY")
                if by_month_day:
                    by_month_day = [int(d) for d in by_month_day] if isinstance(by_month_day, list) else [int(by_month_day)]
                else:
                    by_month_day = None

                if freq:
                    recurrence_rules.append(
                        CalDAVRecurrence(
                            frequency=freq,
                            interval=interval,
                            count=count,
                            until=until,
                            by_day=by_day,
                            by_month_day=by_month_day,
                        )
                    )

            return CalDAVReminder(
                uid=uid,
                summary=summary,
                description=description,
                completed=completed,
                priority=priority,
                due_date=due_date,
                created=created,
                last_modified=last_modified,
                completed_date=completed_date,
                url=url,
                caldav_url=str(todo.url),
                icalendar_data=ical_data,
                alarms=alarms,
                recurrence_rules=recurrence_rules,
            )

        except Exception as e:
            logger.error(f"Failed to parse TODO: {e}")
            return None

    async def create_todo(
        self,
        calendar_name: str,
        uid: str,
        summary: str,
        description: str | None = None,
        completed: bool = False,
        priority: int = 0,
        due_date: datetime | None = None,
        url: str | None = None,
        alarms: list[CalDAVAlarm] | None = None,
        recurrence_rules: list[CalDAVRecurrence] | None = None,
        creation_date: datetime | None = None,
        modification_date: datetime | None = None,
    ) -> CalDAVReminder | None:
        """
        Create a new TODO in the specified calendar.

        Args:
            calendar_name: Name of the calendar to create in
            uid: Unique identifier for the TODO
            summary: Title/summary of the TODO
            description: Description/notes
            completed: Whether the TODO is completed
            priority: Priority (0=undefined, 1=highest, 9=lowest)
            due_date: Due date
            url: URL to attach
            alarms: List of alarms to add
            recurrence_rules: List of recurrence rules to add
            creation_date: Creation date (defaults to now if not provided)
            modification_date: Last modification date (defaults to now if not provided)

        Returns:
            Created CalDAVReminder object or None if failed
        """
        if not self.client:
            await self.connect()

        # Find the target calendar
        target_calendar = None
        for cal in self.calendars:
            if cal.name == calendar_name:
                target_calendar = cal
                break

        # If calendar doesn't exist, create it
        if not target_calendar:
            logger.warning(f"Calendar '{calendar_name}' not found, creating it...")
            if await self.create_calendar(calendar_name):
                # Find the newly created calendar
                for cal in self.calendars:
                    if cal.name == calendar_name:
                        target_calendar = cal
                        break
            else:
                logger.error(f"Failed to create calendar: {calendar_name}")
                return None

        if not target_calendar:
            logger.error(f"Calendar not found after creation attempt: {calendar_name}")
            return None

        # Create VTODO
        cal = Calendar()
        cal.add("prodid", "-//iCloudBridge//EN")
        cal.add("version", "2.0")

        todo = VTodo()
        todo.add("uid", uid)
        todo.add("summary", summary)
        if description:
            todo.add("description", description)
        todo.add("status", "COMPLETED" if completed else "NEEDS-ACTION")
        if completed:
            todo.add("completed", datetime.now())
            todo.add("percent-complete", 100)
        todo.add("priority", priority)
        if due_date:
            todo.add("due", due_date)
        if url:
            todo.add("url", url)

        # Use provided timestamps or default to now
        # This preserves Apple Reminder timestamps when syncing from Apple → CalDAV
        created_time = creation_date if creation_date is not None else datetime.now()
        modified_time = modification_date if modification_date is not None else datetime.now()

        todo.add("created", created_time)
        todo.add("last-modified", modified_time)
        todo.add("dtstamp", datetime.now())

        # Add alarms
        if alarms:
            for alarm in alarms:
                valarm = Alarm()
                valarm.add("action", "DISPLAY")
                valarm.add("description", summary)
                # Convert trigger_minutes to duration format
                # Positive = before due date, negative = after
                if alarm.trigger_minutes >= 0:
                    trigger_duration = timedelta(minutes=-alarm.trigger_minutes)
                else:
                    trigger_duration = timedelta(minutes=abs(alarm.trigger_minutes))
                valarm.add("trigger", trigger_duration)
                todo.add_component(valarm)

        # Add recurrence rules
        if recurrence_rules:
            for rule in recurrence_rules:
                rrule_dict = {"FREQ": [rule.frequency], "INTERVAL": [rule.interval]}
                if rule.count is not None:
                    rrule_dict["COUNT"] = [rule.count]
                if rule.until is not None:
                    rrule_dict["UNTIL"] = [rule.until]
                if rule.by_day:
                    rrule_dict["BYDAY"] = rule.by_day
                if rule.by_month_day:
                    rrule_dict["BYMONTHDAY"] = rule.by_month_day
                todo.add("rrule", rrule_dict)

        cal.add_component(todo)

        # Save to CalDAV server - run blocking operation in thread pool
        try:
            ical_data = cal.to_ical().decode("utf-8")

            # Debug: Log what we're sending
            logger.debug(f"Creating TODO with CREATED={created_time}, LAST-MODIFIED={modified_time}")

            created_todo = await asyncio.to_thread(target_calendar.save_todo, ical_data)
            logger.info(f"Created TODO: {summary}")

            # Debug: Check what the server actually saved
            parsed = self._parse_todo(created_todo)
            if parsed:
                logger.debug(f"Server returned CREATED={parsed.created}, LAST-MODIFIED={parsed.last_modified}")

            return parsed
        except Exception as e:
            logger.error(f"Failed to create TODO: {e}")
            return None

    async def update_todo(
        self,
        caldav_url: str,
        summary: str | None = None,
        description: str | None = None,
        completed: bool | None = None,
        priority: int | None = None,
        due_date: datetime | None = None,
        url: str | None = None,
        alarms: list[CalDAVAlarm] | None = None,
        recurrence_rules: list[CalDAVRecurrence] | None = None,
        modification_date: datetime | None = None,
    ) -> CalDAVReminder | None:
        """
        Update an existing TODO by its CalDAV URL.

        Args:
            caldav_url: CalDAV URL of the TODO to update
            summary: New summary (if provided)
            description: New description (if provided)
            completed: New completion status (if provided)
            priority: New priority (if provided)
            due_date: New due date (if provided)
            url: New URL (if provided)
            alarms: New list of alarms (if provided, replaces existing)
            recurrence_rules: New list of recurrence rules (if provided, replaces existing)
            modification_date: Last modification date (defaults to now if not provided)

        Returns:
            Updated CalDAVReminder object or None if failed
        """
        if not self.client:
            await self.connect()

        try:
            # Fetch the existing TODO - run blocking operation in thread pool
            todo = caldav.Todo(client=self.client, url=caldav_url)
            await asyncio.to_thread(todo.load)

            # Parse existing iCalendar data
            cal = Calendar.from_ical(todo.data)
            vtodo = None
            for component in cal.walk():
                if component.name == "VTODO":
                    vtodo = component
                    break

            if not vtodo:
                logger.error("No VTODO component found")
                return None

            # Update fields
            if summary is not None:
                vtodo["SUMMARY"] = summary
            if description is not None:
                vtodo["DESCRIPTION"] = description
            if completed is not None:
                vtodo["STATUS"] = "COMPLETED" if completed else "NEEDS-ACTION"
                if completed:
                    vtodo["COMPLETED"] = datetime.now()
                    vtodo["PERCENT-COMPLETE"] = 100
                else:
                    if "COMPLETED" in vtodo:
                        del vtodo["COMPLETED"]
                    vtodo["PERCENT-COMPLETE"] = 0
            if priority is not None:
                vtodo["PRIORITY"] = priority
            if due_date is not None:
                vtodo["DUE"] = due_date
            if url is not None:
                vtodo["URL"] = url

            # Update alarms (replace all existing alarms)
            if alarms is not None:
                # Remove existing alarms
                vtodo.subcomponents = [c for c in vtodo.subcomponents if c.name != "VALARM"]
                # Add new alarms
                for alarm in alarms:
                    valarm = Alarm()
                    valarm.add("action", "DISPLAY")
                    valarm.add("description", vtodo.get("SUMMARY", ""))
                    # Convert trigger_minutes to duration format
                    if alarm.trigger_minutes >= 0:
                        trigger_duration = timedelta(minutes=-alarm.trigger_minutes)
                    else:
                        trigger_duration = timedelta(minutes=abs(alarm.trigger_minutes))
                    valarm.add("trigger", trigger_duration)
                    vtodo.add_component(valarm)

            # Update recurrence rules (replace existing)
            if recurrence_rules is not None:
                # Remove existing RRULEs
                if "RRULE" in vtodo:
                    del vtodo["RRULE"]
                # Add new RRULEs
                for rule in recurrence_rules:
                    rrule_dict = {"FREQ": [rule.frequency], "INTERVAL": [rule.interval]}
                    if rule.count is not None:
                        rrule_dict["COUNT"] = [rule.count]
                    if rule.until is not None:
                        rrule_dict["UNTIL"] = [rule.until]
                    if rule.by_day:
                        rrule_dict["BYDAY"] = rule.by_day
                    if rule.by_month_day:
                        rrule_dict["BYMONTHDAY"] = rule.by_month_day
                    vtodo.add("rrule", rrule_dict)

            # Update last-modified timestamp
            # Use provided timestamp or default to now
            # This preserves Apple Reminder timestamps when syncing from Apple → CalDAV
            modified_time = modification_date if modification_date is not None else datetime.now()
            vtodo["LAST-MODIFIED"] = modified_time

            # Save changes - run blocking operation in thread pool
            ical_data = cal.to_ical().decode("utf-8")

            # Debug: Log what we're sending
            logger.debug(f"Setting LAST-MODIFIED to: {modified_time}")
            logger.debug(f"iCalendar data being sent:\n{ical_data}")

            todo.data = ical_data
            await asyncio.to_thread(todo.save)

            logger.info(f"Updated TODO: {caldav_url}")

            # Debug: Re-fetch to see what the server actually saved
            updated_todo = self._parse_todo(todo)
            if updated_todo:
                logger.debug(f"Server returned LAST-MODIFIED: {updated_todo.last_modified}")

            return updated_todo

        except Exception as e:
            logger.error(f"Failed to update TODO: {e}")
            return None

    async def delete_todo(self, caldav_url: str) -> bool:
        """
        Delete a TODO by its CalDAV URL.

        Args:
            caldav_url: CalDAV URL of the TODO to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        if not self.client:
            await self.connect()

        try:
            # Run blocking operation in thread pool
            todo = caldav.Todo(client=self.client, url=caldav_url)
            await asyncio.to_thread(todo.delete)
            logger.info(f"Deleted TODO: {caldav_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete TODO: {e}")
            return False
