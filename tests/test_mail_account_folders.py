"""Folders in IMAP/Exchange accounts never go through Shortcuts, and shortcuts can't hang a sync."""
from __future__ import annotations

import os
import stat

import pytest

from icloudbridge.core.rich_notes_export import RichNotesExporter
from icloudbridge.sources.notes.applescript import AppleScriptFolder
from icloudbridge.sources.notes.shortcuts import NotesShortcutAdapter
from tests.test_notes_folder_moves import make_engine


async def test_mail_account_folders_are_never_given_to_shortcuts(tmp_path, monkeypatch):
    engine, fake = await make_engine(tmp_path, {"Google/Notes": [], "iCloud/Work": []})

    async def list_folders(include_unsyncable: bool = False):
        return [
            AppleScriptFolder(uuid="x-coredata://G/IMAPFolder/p7", name="Google/Notes"),
            AppleScriptFolder(uuid="x-coredata://I/ICFolder/p78", name="iCloud/Work"),
        ]

    monkeypatch.setattr(fake, "list_folders", list_folders)
    # Unique name, but a mail account: Shortcuts would ask "which one?".
    assert await engine._shortcut_folder_name("Google/Notes") is None
    assert "Google/Notes" in engine._mail_account_folders
    assert await engine._shortcut_folder_name("iCloud/Work") == "Work"


async def test_checklist_note_in_a_mail_account_folder_is_written(tmp_path, monkeypatch):
    engine, fake = await make_engine(tmp_path, {"Google/Notes": []})

    async def list_folders(include_unsyncable: bool = False):
        return [AppleScriptFolder(uuid="x-coredata://G/IMAPFolder/p7", name="Google/Notes")]

    monkeypatch.setattr(fake, "list_folders", list_folders)
    folder = tmp_path / "md/Google/Notes"
    folder.mkdir(parents=True)
    (folder / "Shopping.md").write_text("# Shopping\n\n- [ ] Milk\n- [x] Bread\n")

    await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes")

    # Before, a checklist note failed the whole folder ("rename one of the folders").
    assert fake.created == ["Shopping"]


def test_a_shortcut_that_waits_for_an_answer_times_out(tmp_path, monkeypatch):
    # A stand-in "shortcuts" command that never finishes, like one showing a prompt.
    fake = tmp_path / "shortcuts"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(NotesShortcutAdapter, "SHORTCUT_TIMEOUT_SECONDS", 1)

    with pytest.raises(RuntimeError, match="didn't finish within 1 seconds"):
        NotesShortcutAdapter._run_shortcut(["shortcuts", "run", "Upsert Note"], input="x")


def test_rich_export_handles_checklist_notes(tmp_path, monkeypatch):
    remote = tmp_path / "md"
    (remote / "Notes").mkdir(parents=True)
    (remote / "Notes/Tasks.md").write_text("# Tasks\n\n- [ ] One\n- [x] Two\n")
    exporter = RichNotesExporter(tmp_path / "notes.db", remote)
    mappings = [{"local_uuid": "x-coredata://S/ICNote/p1", "remote_path": str(remote / "Notes/Tasks.md")}]
    html = (
        '<div class="note-content"><p>Tasks</p><ul class="checklist">'
        '<li class="unchecked">One</li><li class="checked">Two</li></ul></div>'
    )
    entries = {"x-coredata://S/ICNote/p1": {"html": html}}
    monkeypatch.setattr(exporter, "_gather_mappings", lambda: mappings)
    monkeypatch.setattr(exporter._capture, "capture_indexes", lambda: {"by_uuid": entries, "by_note_id": {}})

    exporter.export()  # raised NameError: name 're' is not defined

    exported = (remote / "RichNotes/Notes/Tasks_rich.md").read_text()
    assert "- [ ] One" in exported and "- [x] Two" in exported
