"""Large folders: list notes in bulk and fetch bodies only when needed.

Listing a folder used to send five Apple Events per note (id, name, two dates
and the full HTML body) and grow one string by concatenation. A 1,725-note
folder kept Notes busy for about an hour until an event hit AppleScript's
timeout (-1712), leaving Notes unresponsive for every folder after it.

The body is only a fallback: the rich ripper snapshot supplies it for nearly
every note. So the listing script now reads each property for the whole folder
in one event, and the AppleScript body is requested per note only when the
snapshot has no entry for it.
"""
from __future__ import annotations

import pytest

from icloudbridge.sources.notes import applescript
from icloudbridge.sources.notes.applescript import (
    GET_NOTE_BODY_SCRIPT,
    GET_NOTES_SCRIPT,
    NotesAdapter,
)

DATE = "Friday, 13 November 2026 at 01:00:00"


async def _noop() -> None:
    return None


def _listing(*uuids: str) -> str:
    notes = "".join(
        f"{u}|||Title {u}|||{DATE}|||{DATE}|||~~~NEXT_NOTE~~~" for u in uuids
    )
    return "x-coredata://S/ICFolder/p9~~~FOLDER~~~" + notes


@pytest.fixture
def adapter(monkeypatch):
    notes = NotesAdapter()
    monkeypatch.setattr(notes, "ensure_notes_running", lambda: _noop())
    monkeypatch.setattr(notes, "ensure_rich_cache", lambda: _noop())
    monkeypatch.setattr(applescript, "special_folder_keys", lambda: (set(), set()))
    return notes


def test_listing_script_does_not_read_bodies_per_note():
    assert "body of" not in GET_NOTES_SCRIPT
    assert "repeat with theNote in" not in GET_NOTES_SCRIPT
    assert "with timeout of" in GET_NOTES_SCRIPT


async def test_snapshot_body_is_used_without_fetching_the_applescript_body(
    adapter, monkeypatch
):
    calls: list[str] = []

    async def fake_run(script, *args):
        calls.append(script)
        return _listing("A", "B")

    monkeypatch.setattr(adapter, "_run_applescript", fake_run)
    monkeypatch.setattr(adapter, "_rich_entry_for_uuid", lambda u: {"uuid": u})
    monkeypatch.setattr(applescript, "extract_note_content", lambda e: f"<p>{e['uuid']}</p>")
    monkeypatch.setattr(adapter, "_rich_attachments_for_entry", lambda e: [])

    notes = await adapter.get_notes("iCloud/Notes")

    assert [(n.uuid, n.name, n.body_html) for n in notes] == [
        ("A", "Title A", "<p>A</p>"),
        ("B", "Title B", "<p>B</p>"),
    ]
    assert calls == [GET_NOTES_SCRIPT]


async def test_body_is_fetched_by_id_only_for_a_snapshot_miss(adapter, monkeypatch):
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def fake_run(script, *args):
        calls.append((script, args))
        if script == GET_NOTE_BODY_SCRIPT:
            return f"<div>applescript body {args[0]}</div>"
        return _listing("HIT", "MISS")

    async def no_retry(uuid):
        return None

    monkeypatch.setattr(adapter, "_run_applescript", fake_run)
    monkeypatch.setattr(
        adapter, "_rich_entry_for_uuid", lambda u: {"uuid": u} if u == "HIT" else None
    )
    monkeypatch.setattr(adapter, "_retry_rich_entry", no_retry)
    monkeypatch.setattr(applescript, "extract_note_content", lambda e: "<p>rich</p>")
    monkeypatch.setattr(adapter, "_rich_attachments_for_entry", lambda e: [])

    notes = await adapter.get_notes("iCloud/Notes")

    bodies = {n.uuid: n.body_html for n in notes}
    assert bodies == {"HIT": "<p>rich</p>", "MISS": "<div>applescript body MISS</div>"}
    assert [c for c in calls if c[0] == GET_NOTE_BODY_SCRIPT] == [
        (GET_NOTE_BODY_SCRIPT, ("MISS",))
    ]


async def test_empty_folder_still_returns_no_notes(adapter, monkeypatch):
    async def fake_run(script, *args):
        return "x-coredata://S/ICFolder/p9~~~FOLDER~~~~~~NEXT_NOTE~~~"

    monkeypatch.setattr(adapter, "_run_applescript", fake_run)

    assert await adapter.get_notes("iCloud/Empty") == []
