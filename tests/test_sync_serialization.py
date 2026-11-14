"""Writing syncs of one service must not overlap.

A manual bidirectional Notes sync and the hourly scheduled one ran at the same
time; both created the same new Markdown note in Apple Notes, leaving an
orphaned duplicate. The command line, a separate process, could overlap them
the same way, and so could Passwords and Photos syncs.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from typer.testing import CliRunner

from icloudbridge.api import scheduler as scheduler_module
from icloudbridge.api.routes import notes as notes_routes
from icloudbridge.api.routes import passwords as passwords_routes
from icloudbridge.api.routes import photos as photos_routes
from icloudbridge.api.routes import reminders as reminders_routes
from icloudbridge.core import sync_locks
from icloudbridge.core.sync_locks import SyncAlreadyRunning, exclusive_sync, lock_file_path, service_lock

TIMEOUT = 5


@pytest.fixture
def config(tmp_path):
    return SimpleNamespace(general=SimpleNamespace(data_dir=tmp_path))


@pytest.fixture(autouse=True)
def fresh_locks(monkeypatch):
    # Each test runs on its own event loop; the app has one for its lifetime.
    monkeypatch.setattr(sync_locks, "_locks", {})
    monkeypatch.setattr(sync_locks, "POLL_SECONDS", 0.05)


@pytest.fixture
def blocking(monkeypatch):
    """Replace the real sync bodies with ones that wait until released."""
    release = asyncio.Event()
    started = asyncio.Event()
    calls = []

    async def fake_sync(*args, **kwargs):
        first = args[0] if args else None
        calls.append(first.dry_run if hasattr(first, "dry_run") else kwargs.get("simulate"))
        started.set()
        await release.wait()
        return {"status": "success"}

    for module, name in [
        (notes_routes, "_sync_notes"),
        (reminders_routes, "_sync_reminders"),
        (passwords_routes, "_run_passwords_sync_unlocked"),
        (photos_routes, "_sync_photos"),
    ]:
        monkeypatch.setattr(module, name, fake_sync)
    return SimpleNamespace(release=release, started=started, calls=calls)


@contextmanager
def held_by_another_process(lock_dir, service):
    """Hold the service's lock file from a separate Python process, as the command line would."""
    path = lock_file_path(lock_dir, service)
    path.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, os, sys\n"
            "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "print('locked', flush=True)\n"
            "sys.stdin.readline()\n",
            str(path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout.readline().strip() == "locked"
    try:
        yield holder
    finally:
        if holder.poll() is None:
            holder.stdin.write("\n")
            holder.stdin.flush()
        holder.wait(timeout=TIMEOUT)


def request(dry_run: bool):
    return SimpleNamespace(dry_run=dry_run)


async def started(blocking):
    await asyncio.wait_for(blocking.started.wait(), TIMEOUT)


async def test_second_writing_notes_sync_is_refused(blocking, config):
    first = asyncio.create_task(notes_routes.sync_notes(request(False), config=config))
    await started(blocking)

    with pytest.raises(HTTPException) as exc:
        await notes_routes.sync_notes(request(False), config=config)
    assert exc.value.status_code == 409

    blocking.release.set()
    assert (await asyncio.wait_for(first, TIMEOUT))["status"] == "success"
    assert not service_lock("notes").locked()


async def test_dry_runs_are_never_blocked(blocking, config):
    first = asyncio.create_task(notes_routes.sync_notes(request(False), config=config))
    await started(blocking)

    dry = asyncio.create_task(notes_routes.sync_notes(request(True), config=config))
    await asyncio.sleep(0)
    blocking.release.set()
    await asyncio.wait_for(asyncio.gather(first, dry), TIMEOUT)
    assert blocking.calls == [False, True]


async def test_reminders_lock_is_separate_from_notes(blocking, config):
    notes = asyncio.create_task(notes_routes.sync_notes(request(False), config=config))
    await started(blocking)

    reminders = asyncio.create_task(reminders_routes.sync_reminders(request(False), engine=None, config=config))
    await asyncio.sleep(0.1)
    assert not reminders.done(), "a reminders sync must not be refused because notes is running"
    blocking.release.set()
    await asyncio.wait_for(asyncio.gather(notes, reminders), TIMEOUT)


async def test_second_passwords_sync_and_import_are_refused(blocking, config):
    first = asyncio.create_task(
        passwords_routes._run_passwords_sync(config=config, simulate=False, engine=None)
    )
    await started(blocking)

    with pytest.raises(HTTPException) as exc:
        await passwords_routes._run_passwords_sync(config=config, simulate=False, engine=None)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await passwords_routes.import_apple_csv(file=None, engine=None, config=config)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await passwords_routes.reset_database(passwords_db=None, config=config)
    assert exc.value.status_code == 409
    # Simulate still runs.
    simulated = asyncio.create_task(passwords_routes._run_passwords_sync(config=config, simulate=True, engine=None))

    blocking.release.set()
    await asyncio.wait_for(asyncio.gather(first, simulated), TIMEOUT)
    assert blocking.calls == [False, True]


async def test_second_photos_sync_and_export_are_refused(blocking, config):
    first = asyncio.create_task(photos_routes.sync_photos(request(False), config=config, engine=None))
    await started(blocking)

    with pytest.raises(HTTPException) as exc:
        await photos_routes.sync_photos(request(False), config=config, engine=None)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await photos_routes.export_photos(request(False), config=config, photos_db=None)
    assert exc.value.status_code == 409

    blocking.release.set()
    await asyncio.wait_for(first, TIMEOUT)


@pytest.mark.parametrize("service", ["notes", "reminders", "passwords", "photos"])
async def test_scheduled_sync_waits_for_a_running_manual_sync(config, service):
    order = []
    sched = object.__new__(scheduler_module.SchedulerManager)
    sched.config = config

    async def scheduled(_config):
        order.append("scheduled")
        return {"status": "success"}

    setattr(sched, f"_sync_{service}", scheduled)

    async with exclusive_sync(service, config.general.data_dir, wait=False):  # the manual sync
        task = asyncio.create_task(sched._run_service_sync(service, {}))
        await asyncio.sleep(0.2)
        assert order == [], "the scheduled sync must wait, not run alongside"
        order.append("manual done")
    await asyncio.wait_for(task, TIMEOUT)
    assert order == ["manual done", "scheduled"]


async def test_another_process_blocks_a_manual_sync(blocking, config):
    with held_by_another_process(config.general.data_dir, "notes"):
        with pytest.raises(HTTPException) as exc:
            await notes_routes.sync_notes(request(False), config=config)
        assert exc.value.status_code == 409
    # Released: it runs.
    task = asyncio.create_task(notes_routes.sync_notes(request(False), config=config))
    await started(blocking)
    blocking.release.set()
    await asyncio.wait_for(task, TIMEOUT)


async def test_a_waiting_sync_runs_once_the_other_process_finishes(config):
    with held_by_another_process(config.general.data_dir, "notes"):
        waiting = asyncio.create_task(_hold("notes", config, wait=True))
        await asyncio.sleep(0.3)
        assert not waiting.done()
    await asyncio.wait_for(waiting, TIMEOUT)


async def test_a_crashed_process_leaves_no_stale_lock(config):
    with held_by_another_process(config.general.data_dir, "notes") as holder:
        holder.kill()
        holder.wait(timeout=TIMEOUT)
        await asyncio.wait_for(_hold("notes", config, wait=False), TIMEOUT)


async def _hold(service, config, *, wait):
    async with exclusive_sync(service, config.general.data_dir, wait=wait):
        pass


async def test_lock_is_released_when_a_sync_fails(config):
    with pytest.raises(ValueError):
        async with exclusive_sync("notes", config.general.data_dir, wait=False):
            raise ValueError("boom")
    async with exclusive_sync("notes", config.general.data_dir, wait=False):
        pass


async def test_non_waiting_acquire_raises_while_held(config):
    async with exclusive_sync("notes", config.general.data_dir, wait=False):
        with pytest.raises(SyncAlreadyRunning):
            async with exclusive_sync("notes", config.general.data_dir, wait=False):
                pass


def test_command_line_sync_stops_while_the_app_syncs(tmp_path, monkeypatch):
    from icloudbridge.cli.main import notes_app
    from icloudbridge.core.sync import NotesSyncEngine

    ran = []

    async def initialize(self):
        ran.append("initialize")

    monkeypatch.setattr(NotesSyncEngine, "initialize", initialize)
    cfg = SimpleNamespace(
        general=SimpleNamespace(data_dir=tmp_path),
        notes=SimpleNamespace(enabled=True, remote_folder=tmp_path / "md", folder_mappings={},
                              use_shortcuts_for_push=False),
        notes_db_path=tmp_path / "notes.db",
    )

    with held_by_another_process(tmp_path, "notes"):  # the app's sync
        result = CliRunner().invoke(notes_app, ["sync"], obj={"config": cfg})

    assert result.exit_code == 1
    assert "A notes sync is already running" in result.output
    assert ran == []
