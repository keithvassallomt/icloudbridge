"""CalDAV adapter for syncing reminders to CalDAV servers (NextCloud, etc.)."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    is_all_day: bool = False  # True if due date is a DATE (not DATE-TIME)


class CalDAVAdapter:
    """Adapter for syncing reminders with CalDAV servers."""

    _truststore_injected = False

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        ssl_verify_cert: bool | str = True,
    ):
        """
        Initialize CalDAV adapter.

        Args:
            url: CalDAV server URL (e.g., https://nextcloud.example.com/remote.php/dav)
            username: CalDAV username
            password: CalDAV password
            ssl_verify_cert: SSL verification flag or CA bundle path (bool or str)
        """
        self.url = url
        self.username = username
        self.password = password
        self.ssl_verify_cert = ssl_verify_cert
        self.client: DAVClient | None = None
        self.principal: Any = None
        self.calendars: list[Any] = []

    async def connect(self) -> bool:
        """Connect to CalDAV server and discover calendars."""
        try:
            logger.debug(f"Connecting to CalDAV server: {self.url}")
            self._inject_truststore_if_available()
            # Run blocking caldav operations in thread pool
            def _connect():
                logger.debug("Creating DAVClient...")
                client = DAVClient(
                    url=self.url,
                    username=self.username,
                    password=self.password,
                    ssl_verify_cert=self.ssl_verify_cert,
                )
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

    def _inject_truststore_if_available(self) -> None:
        """Try to make requests use the system trust store via truststore."""
        if self.ssl_verify_cert is False:
            logger.debug("SSL verification disabled; skipping truststore injection")
            return
        if CalDAVAdapter._truststore_injected:
            return
        try:
            import truststore

            truststore.inject_into_ssl()
            CalDAVAdapter._truststore_injected = True
            logger.info("Using system trust store for CalDAV SSL verification via truststore")
        except ImportError:
            logger.debug("truststore not installed; using default cert bundle")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"Failed to inject system trust store: {exc}")

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

            # Due date - detect if it's all-day (DATE) vs specific time (DATE-TIME)
            # In iCalendar, DATE is stored as date object, DATE-TIME as datetime object
            due_date_raw = vtodo.get("DUE")
            due_date = None
            is_all_day = False
            if due_date_raw and hasattr(due_date_raw, "dt"):
                due_date_value = due_date_raw.dt
                # Check if it's a date (all-day) vs datetime (specific time)
                # date objects don't have hour/minute/second attributes
                from datetime import date as date_type
                if isinstance(due_date_value, date_type) and not isinstance(due_date_value, datetime):
                    # All-day: convert date to datetime at midnight UTC
                    is_all_day = True
                    due_date = datetime(
                        year=due_date_value.year,
                        month=due_date_value.month,
                        day=due_date_value.day,
                        hour=0,
                        minute=0,
                        second=0,
                        tzinfo=timezone.utc,
                    )
                    logger.debug(f"Parsed all-day due date: {due_date.date()}")
                else:
                    # Specific time
                    is_all_day = False
                    due_date = due_date_value
                    # Ensure timezone is set
                    if due_date and not due_date.tzinfo:
                        due_date = due_date.replace(tzinfo=timezone.utc)

            # Timestamps - strip microseconds as iCalendar doesn't support them
            # IMPORTANT: Never default to datetime.now() for timestamps used in sync!
            # Using current time breaks conflict resolution by making remote always appear "newer".
            # Fallback chain: LAST-MODIFIED → DTSTAMP → CREATED → epoch (very old date)

            # Parse DTSTAMP first (required field per iCalendar spec, best fallback)
            dtstamp = vtodo.get("DTSTAMP")
            dtstamp_dt = None
            if dtstamp and hasattr(dtstamp, "dt"):
                dtstamp_dt = dtstamp.dt
                if dtstamp_dt and not dtstamp_dt.tzinfo:
                    dtstamp_dt = dtstamp_dt.replace(tzinfo=timezone.utc)
                if dtstamp_dt and hasattr(dtstamp_dt, 'microsecond'):
                    dtstamp_dt = dtstamp_dt.replace(microsecond=0)

            # Parse CREATED
            created = vtodo.get("CREATED")
            if created and hasattr(created, "dt"):
                created = created.dt
                if created and not created.tzinfo:
                    created = created.replace(tzinfo=timezone.utc)
                if created and hasattr(created, 'microsecond'):
                    created = created.replace(microsecond=0)
            else:
                # Fallback: use DTSTAMP, then epoch
                created = dtstamp_dt or datetime(1970, 1, 1, tzinfo=timezone.utc)

            # Parse LAST-MODIFIED with fallback chain
            last_modified = vtodo.get("LAST-MODIFIED")
            if last_modified and hasattr(last_modified, "dt"):
                last_modified = last_modified.dt
                if last_modified and not last_modified.tzinfo:
                    last_modified = last_modified.replace(tzinfo=timezone.utc)
                if last_modified and hasattr(last_modified, 'microsecond'):
                    last_modified = last_modified.replace(microsecond=0)
            else:
                # Fallback: DTSTAMP → CREATED → epoch
                # Using epoch ensures local changes take precedence over items with no timestamp
                last_modified = dtstamp_dt or created or datetime(1970, 1, 1, tzinfo=timezone.utc)
                if last_modified != dtstamp_dt:
                    logger.warning(
                        f"TODO '{uid}' missing LAST-MODIFIED and DTSTAMP, "
                        f"using fallback timestamp: {last_modified}"
                    )

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

            # Normalize to an empty list when none were present (defensive against None downstream)
            alarms = alarms or []

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

            # Normalize to an empty list when no RRULE exists
            recurrence_rules = recurrence_rules or []

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
                is_all_day=is_all_day,
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
        is_all_day: bool = False,
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
            # Strip microseconds as iCalendar doesn't support them
            todo.add("completed", datetime.now(timezone.utc).replace(microsecond=0))
            todo.add("percent-complete", 100)
        todo.add("priority", priority)
        if due_date:
            if is_all_day:
                # All-day: use date object to produce DATE (not DATE-TIME) in iCalendar
                # This preserves the all-day semantics in CalDAV
                from datetime import date as date_type
                due_date_value = date_type(due_date.year, due_date.month, due_date.day)
                todo.add("due", due_date_value)
                logger.debug(f"Writing all-day due date: {due_date_value}")
            else:
                # Specific time: use datetime to produce DATE-TIME
                # Strip microseconds as iCalendar doesn't support them
                todo.add("due", due_date.replace(microsecond=0))
        if url:
            todo.add("url", url)

        # Use provided timestamps or default to now
        # This preserves Apple Reminder timestamps when syncing from Apple → CalDAV
        # Strip microseconds as iCalendar doesn't support them
        now_utc = datetime.now(timezone.utc).replace(microsecond=0)
        created_time = creation_date.replace(microsecond=0) if creation_date is not None else now_utc
        modified_time = modification_date.replace(microsecond=0) if modification_date is not None else now_utc

        todo.add("created", created_time)
        todo.add("last-modified", modified_time)
        todo.add("dtstamp", now_utc)

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
            logger.error(f"Failed to create TODO: {e}", exc_info=True)
            return None

    async def update_todo(
        self,
        caldav_url: str,
        summary: str | None = None,
        description: str | None = None,
        completed: bool | None = None,
        priority: int | None = None,
        due_date: datetime | None = None,
        is_all_day: bool | None = None,
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
            logger.debug(f"Loading TODO from CalDAV: {caldav_url}")
            todo = caldav.Todo(client=self.client, url=caldav_url)
            await asyncio.to_thread(todo.load)
            logger.debug(f"Successfully loaded TODO, parsing iCalendar data...")

            # Parse existing iCalendar data
            # Handle malformed dates that may have been created with NSDate or string values
            try:
                cal = Calendar.from_ical(todo.data)
            except ValueError as e:
                if "Expected datetime, date, or time" in str(e):
                    logger.warning(f"Detected malformed date in existing TODO, attempting to repair: {e}")
                    # Try to repair malformed dates by removing them from the raw data
                    # This allows the update to proceed and set correct dates
                    import re
                    repaired_data = re.sub(
                        r'(DUE|DTSTART|COMPLETED|CREATED|LAST-MODIFIED):([^T\r\n][^\r\n]*)',
                        r'',  # Remove malformed date lines
                        todo.data.decode('utf-8') if isinstance(todo.data, bytes) else todo.data
                    )
                    cal = Calendar.from_ical(repaired_data)
                    logger.info("Successfully repaired malformed dates in TODO")
                else:
                    raise

            vtodo = None
            for component in cal.walk():
                if component.name == "VTODO":
                    vtodo = component
                    break

            if not vtodo:
                logger.error("No VTODO component found")
                return None

            # After parsing, check for and remove any remaining malformed date fields
            # These might have been parsed as strings instead of datetime objects
            date_fields = ["DUE", "DTSTART", "COMPLETED", "CREATED", "LAST-MODIFIED", "DTSTAMP"]
            for field in date_fields:
                if field in vtodo:
                    value = vtodo[field]
                    # Check if the value is a string (malformed) instead of datetime
                    if isinstance(value, str):
                        logger.warning(f"Removing malformed {field} field: {value}")
                        del vtodo[field]

            logger.debug(f"Successfully parsed VTODO component, updating fields...")

            # Delete ALL date/time fields to ensure clean state
            # This prevents any malformed dates from surviving the update
            # We'll re-add them below with proper datetime objects
            date_fields_to_clean = ["DUE", "DTSTART", "COMPLETED", "CREATED", "LAST-MODIFIED", "DTSTAMP"]
            for field in date_fields_to_clean:
                if field in vtodo:
                    del vtodo[field]
            logger.debug(f"Cleaned all date fields from VTODO")

            # Update fields
            if summary is not None:
                vtodo["SUMMARY"] = summary
            if description is not None:
                vtodo["DESCRIPTION"] = description
            if completed is not None:
                if "STATUS" in vtodo:
                    del vtodo["STATUS"]
                vtodo.add("status", "COMPLETED" if completed else "NEEDS-ACTION")

                if completed:
                    # Strip microseconds as iCalendar doesn't support them
                    if "COMPLETED" in vtodo:
                        del vtodo["COMPLETED"]
                    vtodo.add("completed", datetime.now(timezone.utc).replace(microsecond=0))

                    if "PERCENT-COMPLETE" in vtodo:
                        del vtodo["PERCENT-COMPLETE"]
                    vtodo.add("percent-complete", 100)
                else:
                    if "COMPLETED" in vtodo:
                        del vtodo["COMPLETED"]
                    if "PERCENT-COMPLETE" in vtodo:
                        del vtodo["PERCENT-COMPLETE"]
                    vtodo.add("percent-complete", 0)
            if priority is not None:
                vtodo["PRIORITY"] = priority
            if due_date is not None:
                if "DUE" in vtodo:
                    del vtodo["DUE"]
                # Determine if all-day: use provided value, or default to False
                use_all_day = is_all_day if is_all_day is not None else False
                if use_all_day:
                    # All-day: use date object to produce DATE (not DATE-TIME)
                    from datetime import date as date_type
                    due_date_value = date_type(due_date.year, due_date.month, due_date.day)
                    vtodo.add("due", due_date_value)
                    logger.debug(f"Updating with all-day due date: {due_date_value}")
                else:
                    # Specific time: strip microseconds as iCalendar doesn't support them
                    clean_due = due_date.replace(microsecond=0)
                    vtodo.add("due", clean_due)
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

            # Always set required timestamp fields with proper datetime objects
            # Use provided timestamp or default to now
            # This preserves Apple Reminder timestamps when syncing from Apple → CalDAV
            # Strip microseconds as iCalendar doesn't support them
            now_utc = datetime.now(timezone.utc).replace(microsecond=0)
            modified_time = (modification_date.replace(microsecond=0) if modification_date is not None else now_utc)

            # Use .add() instead of assignment to ensure proper iCalendar serialization
            # First delete existing values, then add new ones
            if "LAST-MODIFIED" in vtodo:
                del vtodo["LAST-MODIFIED"]
            vtodo.add("last-modified", modified_time)

            if "DTSTAMP" in vtodo:
                del vtodo["DTSTAMP"]
            vtodo.add("dtstamp", now_utc)

            # Save changes - run blocking operation in thread pool
            logger.debug(f"Fields updated successfully, preparing to save...")
            ical_data = cal.to_ical().decode("utf-8")

            # Debug: Log what we're sending
            logger.debug(f"Setting LAST-MODIFIED to: {modified_time}")
            logger.debug(f"iCalendar data being sent:\n{ical_data}")

            logger.debug(f"Saving TODO to CalDAV server...")
            # Use direct PUT instead of save() since we don't have a parent calendar set
            # The caldav library's save() method needs parent.url which we don't have
            await asyncio.to_thread(
                self.client.put,
                caldav_url,
                ical_data,
                {"Content-Type": "text/calendar; charset=utf-8"}
            )
            logger.debug(f"Successfully saved TODO to CalDAV server")

            logger.info(f"Updated TODO: {caldav_url}")

            # Re-fetch the TODO to get the server's version
            logger.debug(f"Re-fetching TODO to verify update...")
            todo_refetch = caldav.Todo(client=self.client, url=caldav_url)
            await asyncio.to_thread(todo_refetch.load)
            updated_todo = self._parse_todo(todo_refetch)
            if updated_todo:
                logger.debug(f"Server returned LAST-MODIFIED: {updated_todo.last_modified}")

            return updated_todo

        except Exception as e:
            logger.error(f"Failed to update TODO: {e}", exc_info=True)
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
