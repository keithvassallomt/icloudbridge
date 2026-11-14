"""Markdown edits to a note that was moved in Apple Notes must not be lost.

Syncing the folder a note was moved out of deleted its Markdown copy as if
the note had been deleted. The new folder's sync then recreated the copy
from Apple Notes, without any Markdown edits made since the last sync.
"""
from __future__ import annotations

import os
import time

from tests.test_notes_folder_moves import make_engine, markdown_files

MAPPINGS = {"iCloud/Notes": {"markdown_folder": "Notes", "mode": "bidirectional"}}


def edit(path, text="Edited in Markdown"):
    path.write_text(path.read_text() + f"\n{text}\n")
    later = time.time() + 5
    os.utime(path, (later, later))


async def test_edits_follow_a_note_moved_to_a_synced_folder(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u2"], "Archive": []})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    edit(tmp_path / "md/Notes/Plain.md")
    fake.folders = {"Notes": [], "Archive": ["u2"]}

    # The folder it left syncs first: its copy, with the edit, stays for now.
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    assert markdown_files(tmp_path) == ["Notes/Plain.md"]

    await engine.sync_folder("Archive", markdown_subfolder="Archive")

    assert markdown_files(tmp_path) == ["Archive/Plain.md"]
    assert "Edited in Markdown" in (tmp_path / "md/Archive/Plain.md").read_text()
    assert "Edited in Markdown" in fake.notes["u2"].body_html
    assert fake.deleted == [] and fake.created == []


async def test_edits_reach_a_note_moved_out_of_the_sync(tmp_path):
    engine, fake = await make_engine(tmp_path, {"iCloud/Notes": ["u2"], "iCloud/Private": []})
    await engine.sync_with_mappings(MAPPINGS)
    edit(tmp_path / "md/Notes/Plain.md")
    fake.folders = {"iCloud/Notes": [], "iCloud/Private": ["u2"]}

    await engine.sync_with_mappings(MAPPINGS)

    # Private isn't synced, so the note leaves the sync, but with the edit.
    assert "Edited in Markdown" in fake.notes["u2"].body_html
    assert markdown_files(tmp_path) == []
    assert await engine.db.get_all_mappings() == []


async def test_a_note_moved_out_of_the_sync_without_edits_just_leaves(tmp_path):
    engine, fake = await make_engine(tmp_path, {"iCloud/Notes": ["u2"], "iCloud/Private": []})
    await engine.sync_with_mappings(MAPPINGS)
    fake.folders = {"iCloud/Notes": [], "iCloud/Private": ["u2"]}

    await engine.sync_with_mappings(MAPPINGS)

    assert fake.updated == []
    assert markdown_files(tmp_path) == []


async def test_a_note_that_is_gone_still_deletes_its_copy(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u2"]})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    del fake.notes["u2"]  # deleted for good: not in Recently Deleted either
    fake.folders = {"Notes": []}

    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    assert markdown_files(tmp_path) == []


async def test_simulate_does_not_report_a_moved_note_as_deleted(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u2"], "Archive": []})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    fake.folders = {"Notes": [], "Archive": ["u2"]}

    stats = await engine.sync_folder("Notes", markdown_subfolder="Notes", dry_run=True)

    assert stats["would_delete_remote"] == 0
    assert markdown_files(tmp_path) == ["Notes/Plain.md"]
