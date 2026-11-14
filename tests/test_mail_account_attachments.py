"""A Markdown edit must never delete attachments the sync couldn't copy from a mail-account note."""
from __future__ import annotations

import os
import time
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tests.test_notes_folder_moves import make_engine, make_note

IMAP_ID = "x-coredata://CF60F1B5/IMAPNote/p24"
ICLOUD_ID = "x-coredata://9A3E2C11/ICNote/p310"


async def synced_engine(tmp_path, monkeypatch, uuid, attachments):
    engine, fake = await make_engine(tmp_path, {"Google/Notes": [uuid]})
    fake.notes[uuid] = make_note(uuid, "Photo note")
    fake.updated = []
    fake.counted = []

    async def update_note(folder_name, note_name, body_html):
        fake.updated.append(note_name)

    async def attachment_count(note_uuid):
        fake.counted.append(note_uuid)
        if attachments is None:
            raise RuntimeError("Notes got an error")
        return attachments

    monkeypatch.setattr(fake, "update_note", update_note, raising=False)
    monkeypatch.setattr(fake, "attachment_count", attachment_count, raising=False)
    await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes")
    return engine, fake


def edit_markdown(tmp_path):
    path = tmp_path / "md/Google/Notes/Photo note.md"
    path.write_text(path.read_text() + "\nEdited in Markdown\n")
    later = time.time() + 5
    os.utime(path, (later, later))
    return path


@pytest.mark.parametrize("apple_also_changed", [False, True])
async def test_markdown_edit_leaves_a_mail_account_note_with_attachments_alone(
    tmp_path, monkeypatch, apple_also_changed
):
    engine, fake = await synced_engine(tmp_path, monkeypatch, IMAP_ID, attachments=1)
    edit_markdown(tmp_path)
    if apple_also_changed:  # a conflict the Markdown side wins
        fake.notes[IMAP_ID] = replace(fake.notes[IMAP_ID], modified_date=datetime.now() + timedelta(seconds=1))

    for _ in range(2):  # still held on the next sync, not forgotten
        stats = await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes")
        assert fake.updated == []
        assert stats["attachments_kept"] == 1
        assert stats["details"]["apple"]["attachments"] == ["Photo note"]

    # Nothing was deleted or duplicated, and the Markdown edit is still there.
    assert fake.deleted == [] and fake.created == []
    assert "Edited in Markdown" in (tmp_path / "md/Google/Notes/Photo note.md").read_text()


async def test_simulate_shows_the_note_will_be_left_alone(tmp_path, monkeypatch):
    engine, fake = await synced_engine(tmp_path, monkeypatch, IMAP_ID, attachments=2)
    edit_markdown(tmp_path)

    stats = await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes", dry_run=True)

    assert stats["attachments_kept"] == 1 and stats["updated_local"] == 0


async def test_mail_account_note_without_attachments_gets_the_edit(tmp_path, monkeypatch):
    engine, fake = await synced_engine(tmp_path, monkeypatch, IMAP_ID, attachments=0)
    edit_markdown(tmp_path)

    stats = await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes")

    assert fake.updated == ["Photo note"] and stats["attachments_kept"] == 0


async def test_note_is_left_alone_when_its_attachments_cant_be_checked(tmp_path, monkeypatch):
    engine, fake = await synced_engine(tmp_path, monkeypatch, IMAP_ID, attachments=None)
    edit_markdown(tmp_path)

    stats = await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes")

    assert fake.updated == [] and stats["attachments_kept"] == 1


async def test_icloud_notes_are_not_checked(tmp_path, monkeypatch):
    # iCloud attachments come through the sync, so their notes need no check.
    engine, fake = await synced_engine(tmp_path, monkeypatch, ICLOUD_ID, attachments=1)
    edit_markdown(tmp_path)

    stats = await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes")

    assert fake.counted == [] and fake.updated == ["Photo note"] and stats["attachments_kept"] == 0
