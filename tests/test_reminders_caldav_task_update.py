"""Updating a task keeps the dates the update isn't about (issue #32)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from icalendar import Calendar

from icloudbridge.sources.reminders import caldav_adapter
from icloudbridge.sources.reminders.caldav_adapter import CalDAVAdapter
from tests.test_reminders_caldav_alarm_update import FakeServer, FakeTodo

NINE = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)


def task(*lines: str):
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
    [vtodo] = Calendar.from_ical(ical).walk("VTODO")
    return vtodo


async def update(monkeypatch, vtodo, **changes) -> dict:
    """Update a task with update_todo and return its date properties afterwards."""
    server = FakeServer(vtodo)
    adapter = CalDAVAdapter("https://dav.example.com", "user", "secret")
    adapter.client = server
    monkeypatch.setattr(caldav_adapter.caldav, "Todo", FakeTodo)

    await adapter.update_todo("https://dav.example.com/pay-rent.ics", **changes)

    [updated] = Calendar.from_ical(server.data).walk("VTODO")
    return {
        name: updated[name].dt
        for name in ("DTSTART", "DUE", "COMPLETED", "CREATED")
        if name in updated
    }


async def test_update_keeps_start_creation_and_completion_dates(monkeypatch):
    vtodo = task(
        "CREATED:20260801T120000Z",
        "DTSTART:20261001T080000Z",
        "DUE:20261001T090000Z",
        "STATUS:COMPLETED",
        "COMPLETED:20260915T100000Z",
    )

    dates = await update(monkeypatch, vtodo, summary="Pay the rent", completed=True, due_date=NINE)

    assert dates == {
        "CREATED": datetime(2026, 8, 1, 12, tzinfo=timezone.utc),
        "DTSTART": datetime(2026, 10, 1, 8, tzinfo=timezone.utc),
        "DUE": NINE,
        "COMPLETED": datetime(2026, 9, 15, 10, tzinfo=timezone.utc),
    }


async def test_completion_date_is_set_and_cleared_with_completion(monkeypatch):
    completed = await update(monkeypatch, task("DUE:20261001T090000Z"), completed=True)
    reopened = await update(
        monkeypatch,
        task("DUE:20261001T090000Z", "STATUS:COMPLETED", "COMPLETED:20260915T100000Z"),
        completed=False,
    )

    assert "COMPLETED" in completed and "COMPLETED" not in reopened


@pytest.mark.parametrize(
    ("lines", "due_date", "is_all_day", "expected"),
    [
        # A start at the due time moves with it
        (
            ["DTSTART:20261001T090000Z", "DUE:20261001T090000Z"],
            NINE.replace(hour=11),
            False,
            {"DTSTART": NINE.replace(hour=11), "DUE": NINE.replace(hour=11)},
        ),
        # All-day, as Reminders.app starts it: at midnight on the due date
        (
            ["DTSTART:20261001T000000", "DUE;VALUE=DATE:20261001"],
            datetime(2026, 10, 5, tzinfo=timezone.utc),
            True,
            {"DTSTART": date(2026, 10, 5), "DUE": date(2026, 10, 5)},
        ),
        # A start of its own stays where it is
        (
            ["DTSTART:20261001T080000Z", "DUE:20261001T090000Z"],
            NINE.replace(hour=11),
            False,
            {"DTSTART": NINE.replace(hour=8), "DUE": NINE.replace(hour=11)},
        ),
        # ...unless the due date moves before it
        (
            ["DTSTART:20261001T080000Z", "DUE:20261001T090000Z"],
            NINE.replace(hour=7),
            False,
            {"DUE": NINE.replace(hour=7)},
        ),
        # No due date in Apple Reminders: the task loses its due date, not its start
        (
            ["DTSTART:20261001T080000Z", "DUE:20261001T090000Z"],
            None,
            None,
            {"DTSTART": NINE.replace(hour=8)},
        ),
    ],
    ids=["start-at-due", "all-day-start-at-due", "own-start", "due-before-start", "no-due"],
)
async def test_start_follows_the_due_date(monkeypatch, lines, due_date, is_all_day, expected):
    dates = await update(monkeypatch, task(*lines), due_date=due_date, is_all_day=is_all_day)

    assert dates == expected
