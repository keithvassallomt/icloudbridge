"""Notes sharing a title must each keep their own file, and edits must reach the right note.

On 0.3.3 two notes titled "Dup test" shared one Markdown file. Editing it
overwrote the second note with the first one's text (Google), or deleted both
notes (iCloud): the Shortcuts pipeline finds notes by title and removed every
"old" note with that title.
"""
from __future__ import annotations

import os
import time
from dataclasses import replace

from icloudbridge.sources.notes.markdown import MarkdownAdapter
from tests.test_notes_folder_moves import make_engine, make_note, markdown_files

FIRST, SECOND = "x-coredata://S/ICNote/p1", "x-coredata://S/ICNote/p2"


async def two_todos(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": [FIRST, SECOND]})
    fake.notes[FIRST] = replace(make_note(FIRST, "TODO"), body_html="<div>TODO</div><div>first</div>")
    fake.notes[SECOND] = replace(make_note(SECOND, "TODO"), body_html="<div>TODO</div><div>second</div>")
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    return engine, fake


def edit(path, text):
    path.write_text(path.read_text() + f"\n{text}\n")
    later = time.time() + 5
    os.utime(path, (later, later))


async def file_of(engine, uuid):
    from pathlib import Path

    return Path((await engine.db.get_mapping(uuid))["remote_path"])


async def test_same_title_notes_get_their_own_files(tmp_path):
    engine, _fake = await two_todos(tmp_path)

    assert markdown_files(tmp_path) == ["Notes/TODO (2).md", "Notes/TODO.md"]
    first, second = await file_of(engine, FIRST), await file_of(engine, SECOND)
    assert first != second
    assert "first" in first.read_text() and "second" in second.read_text()


async def test_a_numbered_file_keeps_the_real_title(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    await adapter.write_note("TODO", "<div>TODO</div><div>one</div>")
    path = await adapter.write_note("TODO", "<div>TODO</div><div>two</div>")
    assert path.name == "TODO (2).md"

    # Otherwise pushing the note back would rename it "TODO (2)" in Apple Notes.
    assert (await adapter.read_note(path)).name == "TODO"
    adapter._remove_metadata_file(path)
    assert (await adapter.read_note(path)).name == "TODO"  # from its heading


async def test_markdown_edit_reaches_only_its_own_note(tmp_path):
    engine, fake = await two_todos(tmp_path)
    second = await file_of(engine, SECOND)
    edit(second, "Edited in Markdown")

    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    assert "Edited in Markdown" in fake.notes[SECOND].body_html
    assert "Edited in Markdown" not in fake.notes[FIRST].body_html
    assert fake.deleted == [] and fake.created == []


async def test_deleting_one_file_deletes_only_its_note(tmp_path):
    engine, fake = await two_todos(tmp_path)
    (await file_of(engine, SECOND)).unlink()

    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    assert SECOND not in fake.notes and FIRST in fake.notes


class RecordingShortcuts:
    def __init__(self) -> None:
        self.upserts: list[str] = []

    def supports_tags(self) -> bool:
        return False

    async def upsert_note(self, folder, title, tags=None) -> None:
        self.upserts.append(title)


async def test_shortcuts_are_not_used_when_another_note_shares_the_title(tmp_path, caplog):
    engine, fake = await two_todos(tmp_path)
    engine.use_shortcut_pipeline = True
    engine.shortcuts = RecordingShortcuts()
    second = await file_of(engine, SECOND)
    edit(second, "- [ ] Milk")

    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    # Shortcuts find notes by title: they would have written to, or deleted, the other TODO.
    assert engine.shortcuts.upserts == []
    assert "Milk" in fake.notes[SECOND].body_html and "Milk" not in fake.notes[FIRST].body_html
    assert FIRST in fake.notes and fake.deleted == []
    assert "its checklist arrives as text" in caplog.text
