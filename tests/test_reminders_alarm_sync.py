"""A sync must only rewrite alarms and recurrence rules that changed (issue #24)."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from EventKit import EKAlarm, EKAlarmProximityEnter, EKStructuredLocation

from icloudbridge.core import reminders_sync
from icloudbridge.sources.reminders.caldav_adapter import (
    CalDAVAlarm,
    CalDAVRecurrence,
    CalDAVReminder,
)
from icloudbridge.sources.reminders.eventkit import (
    EventKitReminder,
    ReminderAlarm,
    ReminderRecurrence,
    RemindersAdapter,
    alarm_from_eventkit,
)
from tests.reminders_fakes import make_engine

SYNCED = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
EDITED = SYNCED + timedelta(hours=1)
DUE = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)

AT_DUE_TIME = ReminderAlarm(relative_offset=0)
DAY_BEFORE = ReminderAlarm(relative_offset=-86400)
FIXED_TIME = ReminderAlarm(trigger_date=DUE)
LOCATION = ReminderAlarm()  # how a location alarm reads

APPLE = EventKitReminder(
    uuid="apple-reminder",
    title="Pay rent",
    notes=None,
    completed=False,
    priority=0,
    due_date=DUE,
    creation_date=SYNCED,
    modification_date=SYNCED,
    completion_date=None,
    calendar_id="apple-1",
    calendar_name="Reminders",
    alarms=[AT_DUE_TIME, DAY_BEFORE, FIXED_TIME, LOCATION],
    recurrence_rules=[ReminderRecurrence(frequency="MONTHLY")],
)
CALDAV = CalDAVReminder(
    uid="apple-reminder",
    summary="Pay rent",
    description=None,
    completed=False,
    priority=0,
    due_date=DUE,
    created=SYNCED,
    last_modified=SYNCED,
    completed_date=None,
    url=None,
    caldav_url="https://dav.example.com/calendars/me/reminders/pay-rent.ics",
    icalendar_data="",
    alarms=[
        CalDAVAlarm(trigger_minutes=0),
        CalDAVAlarm(trigger_minutes=1440),
        CalDAVAlarm(trigger_date=DUE),
    ],
    recurrence_rules=[CalDAVRecurrence("MONTHLY", 1, None, None, None, None)],
)


async def synced_pair(tmp_path, apple=APPLE, caldav=CALDAV, sync_fingerprints="current"):
    """A pair that last synced as APPLE and CALDAV and is now as given."""
    engine, reminders, server = await make_engine(tmp_path, ["Reminders"], ["Reminders"])
    reminders.reminders["Reminders"] = [apple]
    server.todos["Reminders"] = [caldav]
    if sync_fingerprints == "current":
        sync_fingerprints = json.dumps(engine._sync_fingerprints(APPLE, CALDAV))
    await engine.db.add_mapping(
        local_uuid=APPLE.uuid,
        remote_uid=CALDAV.uid,
        local_title=APPLE.title,
        remote_caldav_url=CALDAV.caldav_url,
        last_sync=SYNCED,
        sync_fingerprints=sync_fingerprints,
    )
    return engine, reminders, server


async def stored_fingerprints(engine):
    [mapping] = await engine.db.get_all_mappings()
    return engine._stored_fingerprints(mapping)


async def test_caldav_title_edit_leaves_apple_alarms_and_recurrence_alone(tmp_path):
    engine, reminders, _ = await synced_pair(
        tmp_path, caldav=replace(CALDAV, summary="Pay the rent", last_modified=EDITED)
    )

    await engine.sync_calendar("Reminders", "Reminders")

    [(_, fields)] = reminders.updates
    assert fields["title"] == "Pay the rent"
    assert fields["alarms"] is None and fields["recurrence_rules"] is None


async def test_caldav_alarm_edit_is_applied(tmp_path):
    engine, reminders, _ = await synced_pair(
        tmp_path,
        caldav=replace(CALDAV, alarms=[CalDAVAlarm(trigger_minutes=30)], last_modified=EDITED),
    )

    await engine.sync_calendar("Reminders", "Reminders")

    [(_, fields)] = reminders.updates
    # update_reminder keeps the location alarm (see below)
    assert fields["alarms"] == [ReminderAlarm(relative_offset=-1800)]
    assert fields["recurrence_rules"] is None


async def test_caldav_recurrence_edit_is_applied(tmp_path):
    weekly = [CalDAVRecurrence("WEEKLY", 2, None, None, None, None)]
    engine, reminders, _ = await synced_pair(
        tmp_path, caldav=replace(CALDAV, recurrence_rules=weekly, last_modified=EDITED)
    )

    await engine.sync_calendar("Reminders", "Reminders")

    [(_, fields)] = reminders.updates
    assert fields["alarms"] is None
    assert fields["recurrence_rules"] == [ReminderRecurrence(frequency="WEEKLY", interval=2)]


async def test_apple_title_edit_leaves_caldav_alarms_and_recurrence_alone(tmp_path):
    engine, _, server = await synced_pair(
        tmp_path, apple=replace(APPLE, title="Pay the rent", modification_date=EDITED)
    )

    await engine.sync_calendar("Reminders", "Reminders")

    [(_, fields)] = server.updates
    assert fields["summary"] == "Pay the rent"
    assert fields["alarms"] is None and fields["recurrence_rules"] is None


async def test_apple_alarm_edit_is_sent_to_caldav(tmp_path):
    alarms = [AT_DUE_TIME, FIXED_TIME, LOCATION]
    engine, _, server = await synced_pair(
        tmp_path, apple=replace(APPLE, alarms=alarms, modification_date=EDITED)
    )

    await engine.sync_calendar("Reminders", "Reminders")

    [(_, fields)] = server.updates
    assert fields["alarms"] == [CalDAVAlarm(trigger_minutes=0), CalDAVAlarm(trigger_date=DUE)]
    assert fields["recurrence_rules"] is None


async def test_fingerprints_follow_each_update(tmp_path):
    engine, _, server = await synced_pair(
        tmp_path,
        caldav=replace(CALDAV, alarms=[CalDAVAlarm(trigger_minutes=30)], last_modified=EDITED),
    )
    await engine.sync_calendar("Reminders", "Reminders")

    # A later title-only edit on CalDAV compares with the alarms synced above
    [todo] = server.todos["Reminders"]
    server.todos["Reminders"] = [
        replace(todo, summary="Pay the rent", last_modified=datetime.now(timezone.utc))
    ]
    await engine.sync_calendar("Reminders", "Reminders")

    reminders = engine.reminders_adapter
    assert [fields["alarms"] is None for _, fields in reminders.updates] == [False, True]


@pytest.mark.parametrize("sync_fingerprints", [None, json.dumps({"version": 0})])
async def test_pair_without_fingerprints_is_left_alone_and_recorded(tmp_path, sync_fingerprints):
    """Pairs synced before this version can't tell a CalDAV edit from an alarm never sent."""
    engine, reminders, _ = await synced_pair(
        tmp_path,
        caldav=replace(CALDAV, alarms=[], summary="Pay the rent", last_modified=EDITED),
        sync_fingerprints=sync_fingerprints,
    )

    await engine.sync_calendar("Reminders", "Reminders")

    [(_, fields)] = reminders.updates
    assert fields["alarms"] is None and fields["recurrence_rules"] is None
    assert (await stored_fingerprints(engine))["version"] == reminders_sync.FINGERPRINT_VERSION


async def test_unchanged_pair_without_fingerprints_gets_them(tmp_path):
    engine, reminders, server = await synced_pair(tmp_path, sync_fingerprints=None)

    await engine.sync_calendar("Reminders", "Reminders")

    assert reminders.updates == [] and server.updates == []
    assert await stored_fingerprints(engine) == engine._sync_fingerprints(APPLE, CALDAV)


async def test_fingerprints_change_only_with_their_version(tmp_path):
    """
    If this fails, a conversion changed what alarms or recurrence rules sync as.

    Stored fingerprints would then all look like edits, so bump FINGERPRINT_VERSION
    and update the expected values here.
    """
    engine, _, _ = await make_engine(tmp_path, [], [])

    assert engine._sync_fingerprints(APPLE, CALDAV) == {
        "version": 4,
        "apple": {"alarms": "38fe450b0b3810f2", "recurrence": "b1206d2c1ab9ea49"},
        "caldav": {"alarms": "f7a650dccae223fe", "recurrence": "e3620dd34bf8ec05"},
    }


def location_alarm() -> EKAlarm:
    alarm = EKAlarm.alloc().init()
    alarm.setStructuredLocation_(EKStructuredLocation.locationWithTitle_("Home"))
    alarm.setProximity_(EKAlarmProximityEnter)
    return alarm


@pytest.mark.parametrize(
    ("alarm", "expected"),
    [
        (EKAlarm.alarmWithRelativeOffset_(0), AT_DUE_TIME),
        (EKAlarm.alarmWithRelativeOffset_(-86400), DAY_BEFORE),
        (EKAlarm.alarmWithAbsoluteDate_(DUE), FIXED_TIME),
        (location_alarm(), LOCATION),
    ],
)
def test_alarm_from_eventkit(alarm, expected):
    assert alarm_from_eventkit(alarm) == expected


class FakeEKReminder:
    """Just enough of an EKReminder for update_reminder to change its alarms."""

    def __init__(self, alarms):
        self._alarms = list(alarms)

    def alarms(self):
        return list(self._alarms)

    def addAlarm_(self, alarm):
        self._alarms.append(alarm)

    def removeAlarm_(self, alarm):
        self._alarms = [kept for kept in self._alarms if kept is not alarm]


async def test_update_reminder_keeps_location_and_matching_alarms(monkeypatch):
    at_due_time = EKAlarm.alarmWithRelativeOffset_(0)
    day_before = EKAlarm.alarmWithRelativeOffset_(-86400)
    location = location_alarm()
    ek_reminder = FakeEKReminder([at_due_time, day_before, location])
    store = SimpleNamespace(
        calendarItemWithIdentifier_=lambda uuid: ek_reminder,
        saveReminder_commit_error_=lambda reminder, commit, error: (True, None),
    )
    monkeypatch.setattr(RemindersAdapter, "_shared_store", store)
    monkeypatch.setattr(RemindersAdapter, "_access_granted", True)
    adapter = RemindersAdapter()
    monkeypatch.setattr(adapter, "_convert_from_eventkit", lambda reminder: reminder)

    await adapter.update_reminder(
        "apple-reminder", alarms=[AT_DUE_TIME, ReminderAlarm(relative_offset=-1800)]
    )

    alarms = ek_reminder.alarms()
    assert alarms[:2] == [at_due_time, location]
    assert all(alarm is not day_before for alarm in alarms)
    assert [alarm_from_eventkit(alarm) for alarm in alarms[2:]] == [
        ReminderAlarm(relative_offset=-1800)
    ]
