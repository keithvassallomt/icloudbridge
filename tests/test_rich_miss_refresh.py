"""Snapshot misses must not copy the Notes container again and again.

Each ripper capture copies the whole Notes container into a workspace that
stays on disk until the sync operation ends. A refresh per missing note filled
the disk when an IMAP account's notes (never in the snapshot) were synced.
"""
from __future__ import annotations

import pytest

from icloudbridge.sources.notes import applescript
from icloudbridge.sources.notes.applescript import NotesAdapter

DATE = "Monday, 18 February 2023 at 14:24:28"


def notes_output(*uuids: str) -> str:
    notes = [f"{uuid}|||Note {i}|||{DATE}|||{DATE}|||<div>body {i}</div>" for i, uuid in enumerate(uuids)]
    return "x-coredata://S/ICFolder/p1~~~FOLDER~~~" + "~~~NEXT_NOTE~~~".join(notes)


@pytest.fixture
def adapter(monkeypatch):
    adapter = NotesAdapter()
    adapter.refreshes = 0
    adapter.output = ""

    async def fake_applescript(script: str, *args: str) -> str:
        return adapter.output

    async def fake_refresh() -> None:
        adapter.refreshes += 1
        adapter._rich_indexes = {"by_uuid": {}, "by_note_id": {}}

    async def noop() -> None:
        return None

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(adapter, "_run_applescript", fake_applescript)
    monkeypatch.setattr(adapter, "ensure_notes_running", noop)
    monkeypatch.setattr(adapter, "refresh_rich_cache", fake_refresh)
    monkeypatch.setattr(applescript.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(applescript, "special_folder_keys", lambda: (set(), set()))
    return adapter


async def test_other_store_notes_never_trigger_a_capture(adapter):
    adapter.output = notes_output(*(f"x-coredata://M/IMAPNote/p{i}" for i in range(54)))

    notes = await adapter.get_notes("Mxv/Notes")

    assert len(notes) == 54
    assert adapter.refreshes == 1, "only the initial snapshot; IMAP misses must not re-capture"
    assert all(note.body_html.startswith("<div>body") for note in notes)


async def test_misses_share_one_refresh_per_snapshot(adapter):
    adapter.output = notes_output(*(f"x-coredata://S/ICNote/p{i}" for i in range(10)))

    await adapter.get_notes("iCloud/Notes")
    after_first_folder = adapter.refreshes
    await adapter.get_notes("iCloud/Other")

    # Initial capture plus one bounded retry (two attempts), for ten misses.
    assert after_first_folder == 3
    assert adapter.refreshes == after_first_folder, "a second folder must reuse the refreshed snapshot"


async def test_a_write_allows_a_new_miss_refresh(adapter):
    adapter.output = notes_output("x-coredata://S/ICNote/p1")
    await adapter.get_notes("iCloud/Notes")
    before = adapter.refreshes

    adapter.clear_rich_cache()  # e.g. after a Shortcuts upsert
    await adapter.get_notes("iCloud/Notes")

    assert adapter.refreshes == before + 3, "new snapshot plus one retry after an invalidating write"
