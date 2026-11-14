"""Writing syncs of one service must not overlap.

A manual bidirectional Notes sync and the hourly scheduled one ran at the same
time; both created the same new Markdown note in Apple Notes, leaving an
orphaned duplicate.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from icloudbridge.api import scheduler as scheduler_module
from icloudbridge.api.routes import notes as notes_routes
from icloudbridge.api.routes import reminders as reminders_routes
from icloudbridge.core.sync_locks import SyncAlreadyRunning, exclusive_sync, service_lock


@pytest.fixture
def blocking(monkeypatch):
    """Replace the real sync bodies with ones that wait until released."""
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def fake_sync(*args):
        calls.append(args[0].dry_run if hasattr(args[0], "dry_run") else args)
        started.set()
        await release.wait()
        return {"status": "success"}

    monkeypatch.setattr(notes_routes, "_sync_notes", fake_sync)
    monkeypatch.setattr(reminders_routes, "_sync_reminders", fake_sync)
    return SimpleNamespace(release=release, started=started, calls=calls)


def request(dry_run: bool):
    return SimpleNamespace(dry_run=dry_run)


async def test_second_writing_notes_sync_is_refused(blocking):
    first = asyncio.create_task(notes_routes.sync_notes(request(False), config=None))
    await blocking.started.wait()

    with pytest.raises(HTTPException) as exc:
        await notes_routes.sync_notes(request(False), config=None)
    assert exc.value.status_code == 409

    blocking.release.set()
    assert (await first)["status"] == "success"
    assert not service_lock("notes").locked()


async def test_dry_runs_are_never_blocked(blocking):
    first = asyncio.create_task(notes_routes.sync_notes(request(False), config=None))
    await blocking.started.wait()

    dry = asyncio.create_task(notes_routes.sync_notes(request(True), config=None))
    await asyncio.sleep(0)
    blocking.release.set()
    await asyncio.gather(first, dry)
    assert blocking.calls == [False, True]


async def test_reminders_lock_is_separate_from_notes(blocking):
    notes = asyncio.create_task(notes_routes.sync_notes(request(False), config=None))
    await blocking.started.wait()

    reminders = asyncio.create_task(reminders_routes.sync_reminders(request(False), engine=None, config=None))
    await asyncio.sleep(0)
    assert not reminders.done(), "a reminders sync must not be refused because notes is running"
    blocking.release.set()
    await asyncio.gather(notes, reminders)


async def test_scheduled_sync_waits_for_a_running_manual_sync(blocking):
    order = []
    sched = object.__new__(scheduler_module.SchedulerManager)

    async def scheduled_notes(config):
        order.append("scheduled")
        return {"status": "success"}

    sched._sync_notes = scheduled_notes

    manual = asyncio.create_task(notes_routes.sync_notes(request(False), config=None))
    await blocking.started.wait()
    scheduled = asyncio.create_task(sched._run_service_sync("notes", {}))
    await asyncio.sleep(0.05)
    assert order == [], "the scheduled sync must wait, not run concurrently"

    order.append("manual done")
    blocking.release.set()
    await asyncio.gather(manual, scheduled)
    assert order == ["manual done", "scheduled"]


async def test_lock_is_released_when_a_sync_fails():
    with pytest.raises(ValueError):
        async with exclusive_sync("notes", wait=False):
            raise ValueError("boom")
    async with exclusive_sync("notes", wait=False):
        pass


async def test_non_waiting_acquire_raises_while_held():
    async with exclusive_sync("notes", wait=False):
        with pytest.raises(SyncAlreadyRunning):
            async with exclusive_sync("notes", wait=False):
                pass
