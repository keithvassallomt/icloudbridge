"""Notes whose file names were shortened or sanitized must keep their file and their title."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from icloudbridge.sources.notes.markdown import MarkdownAdapter
from icloudbridge.utils.converters import note_filename_stem
from tests.test_notes_folder_moves import make_engine, markdown_files

LONG_TITLE = "https://example.com/" + "/".join(f"segment-{i:02d}-with-words" for i in range(20))


async def test_apple_edit_of_a_long_titled_note_keeps_its_file(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u1"]})
    fake.notes["u1"] = replace(fake.notes["u1"], name=LONG_TITLE, body_html=f"<div>{LONG_TITLE}</div>")
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    [name] = markdown_files(tmp_path)
    assert name == f"Notes/{note_filename_stem(LONG_TITLE)}.md"

    # Edited in Apple Notes after the last sync.
    fake.notes["u1"] = replace(
        fake.notes["u1"],
        body_html=f"<div>{LONG_TITLE}</div><div>Edited in Apple Notes</div>",
        modified_date=datetime.now(),
    )
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    assert markdown_files(tmp_path) == [name]
    assert "Edited in Apple Notes" in (tmp_path / "md" / name).read_text()

    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    assert fake.deleted == [] and fake.created == []


async def test_full_title_survives_a_shortened_file_name(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    path = await adapter.write_note(LONG_TITLE, f"<div>{LONG_TITLE}</div><div>Body</div>")
    assert path.stem != LONG_TITLE
    assert (await adapter.read_note(path)).name == LONG_TITLE

    # Without the sidecar, the note's heading still gives the title.
    adapter._remove_metadata_file(path)
    assert (await adapter.read_note(path)).name == LONG_TITLE


async def test_sanitized_titles_round_trip(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    title = "Plans: Q3/Q4 notes"
    path = await adapter.write_note(title, f"<div>{title}</div><div>Body</div>")
    assert path.name == "Plans Q3-Q4 notes.md"
    assert (await adapter.read_note(path)).name == title


async def test_ordinary_titles_get_no_sidecar_title(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    path = await adapter.write_note("Groceries", "<div>Groceries</div><div>Milk</div>")
    assert "title" not in adapter._read_metadata_file(path)
    assert (await adapter.read_note(path)).name == "Groceries"


async def test_case_only_title_change_does_not_delete_the_file(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    path = await adapter.write_note("Groceries", "<div>Groceries</div><div>Milk</div>")

    updated = await adapter.update_note(
        path, "<div>groceries</div><div>Milk and eggs</div>", note_name="groceries"
    )

    assert updated.exists()
    assert "Milk and eggs" in updated.read_text()


async def test_file_named_by_an_older_release_is_renamed_on_update(tmp_path):
    # 0.3.3 wrote names up to 255 characters, too long for the metadata sidecar.
    adapter = MarkdownAdapter(tmp_path)
    title = "A" * 248  # fits as a .md (251), not with its sidecar (262)
    old = tmp_path / f"{title}.md"
    old.write_text(f"# {title}\n\nOld body\n")

    updated = await adapter.update_note(old, f"<div>{title}</div><div>New body</div>", note_name=title)

    assert updated == tmp_path / f"{note_filename_stem(title)}.md"
    assert not old.exists()
    assert "New body" in updated.read_text()
    assert (await adapter.read_note(updated)).name == title


async def test_editing_a_note_with_a_sanitized_title_keeps_its_file(tmp_path):
    # "Meeting: Q3/Q4" is stored as "Meeting Q3-Q4.md"; an update is not a rename.
    adapter = MarkdownAdapter(tmp_path)
    title = "Meeting: Q3/Q4"
    path = await adapter.write_note(title, f"<div>{title}</div><div>Agenda</div>")

    updated = await adapter.update_note(path, f"<div>{title}</div><div>New agenda</div>", note_name=title)

    assert updated == path and path.exists()
    assert "New agenda" in path.read_text()
