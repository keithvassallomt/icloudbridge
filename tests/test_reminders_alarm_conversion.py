"""Alarms keep their kind and time between Apple Reminders and CalDAV (issue #25)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from icloudbridge.sources.reminders.caldav_adapter import CalDAVAdapter, CalDAVAlarm, _valarm
from icloudbridge.sources.reminders.eventkit import ReminderAlarm
from tests.reminders_fakes import make_engine

DUE = "DUE:20261001T090000Z"
START = "DTSTART:20261001T080000Z"
# Reminders.app, and many other apps, start a task at its due time
START_AT_DUE = "DTSTART:20261001T090000Z"
NINE = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)


def parse_alarms(*lines: str) -> list[CalDAVAlarm]:
    """Parse a task made of the given lines, e.g. its DUE and its VALARMs."""
    ical = "\r\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "BEGIN:VTODO",
            "UID:pay-rent",
            "SUMMARY:Pay rent",
            "DTSTAMP:20260901T120000Z",
            *lines,
            "END:VTODO",
            "END:VCALENDAR",
            "",
        ]
    )
    adapter = CalDAVAdapter("https://dav.example.com", "user", "secret")
    todo = SimpleNamespace(data=ical, url="https://dav.example.com/pay-rent.ics")
    return adapter._parse_todo(todo).alarms


def valarm(trigger: str) -> list[str]:
    return ["BEGIN:VALARM", "ACTION:DISPLAY", trigger, "END:VALARM"]


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ([DUE, *valarm("TRIGGER;RELATED=END:-PT15M")], CalDAVAlarm(trigger_minutes=15)),
        # After the due date, not before it
        ([DUE, *valarm("TRIGGER;RELATED=END:PT15M")], CalDAVAlarm(trigger_minutes=-15)),
        ([DUE, *valarm("TRIGGER;RELATED=END:PT0S")], CalDAVAlarm(trigger_minutes=0)),
        # How iCloudBridge used to write them: no RELATED and no DTSTART
        ([DUE, *valarm("TRIGGER:-PT15M")], CalDAVAlarm(trigger_minutes=15)),
        # Relative to DTSTART, the default when there is one
        ([DUE, START, *valarm("TRIGGER:-PT1H")], CalDAVAlarm(trigger_date=NINE.replace(hour=7))),
        ([DUE, START, *valarm("TRIGGER;RELATED=END:-PT1H")], CalDAVAlarm(trigger_minutes=60)),
        # From a start that is the due time: the same, so it stays an early reminder
        ([DUE, START_AT_DUE, *valarm("TRIGGER:-PT45M")], CalDAVAlarm(trigger_minutes=45)),
        (
            [DUE, START_AT_DUE, *valarm("TRIGGER;RELATED=START:-PT45M")],
            CalDAVAlarm(trigger_minutes=45),
        ),
        (
            [DUE, "DTSTART;TZID=Europe/London:20261001T100000", *valarm("TRIGGER:-PT45M")],
            CalDAVAlarm(trigger_minutes=45),
        ),
        (
            ["DUE;VALUE=DATE:20261001", "DTSTART:20261001T000000", *valarm("TRIGGER:-P1D")],
            CalDAVAlarm(trigger_minutes=1440),
        ),
        # A fixed time, which needs no due date
        (valarm("TRIGGER;VALUE=DATE-TIME:20261001T090000Z"), CalDAVAlarm(trigger_date=NINE)),
    ],
)
def test_parse_alarm(lines, expected):
    assert parse_alarms(*lines) == [expected]


@pytest.mark.parametrize(
    "alarm",
    [
        CalDAVAlarm(trigger_minutes=15),
        CalDAVAlarm(trigger_minutes=-15),
        CalDAVAlarm(trigger_minutes=0),
        CalDAVAlarm(trigger_date=NINE),
    ],
)
def test_written_alarm_reads_back_the_same(alarm):
    written = _valarm(alarm, "Pay rent").to_ical().decode()

    assert parse_alarms(DUE, *written.splitlines()) == [alarm]


def test_written_triggers_say_what_they_are():
    relative = _valarm(CalDAVAlarm(trigger_minutes=15), "Pay rent").to_ical().decode()
    fixed = _valarm(CalDAVAlarm(trigger_date=NINE), "Pay rent").to_ical().decode()

    assert "TRIGGER;RELATED=END:-PT15M" in relative
    assert "TRIGGER;VALUE=DATE-TIME:20261001T090000Z" in fixed


async def test_apple_alarms_survive_round_trip(tmp_path):
    engine, _, _ = await make_engine(tmp_path, [], [])
    time_alarms = [
        ReminderAlarm(relative_offset=0),
        ReminderAlarm(relative_offset=-86400),
        ReminderAlarm(relative_offset=900),
        ReminderAlarm(trigger_date=NINE),
    ]
    location = ReminderAlarm()

    caldav_alarms = engine._convert_alarms_to_caldav([*time_alarms, location])

    assert engine._convert_alarms_to_eventkit(caldav_alarms) == time_alarms
