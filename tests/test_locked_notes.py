"""Locked (password-protected) notes can't be read or written, so the sync leaves them alone."""
from __future__ import annotations

from dataclasses import replace

from tests.test_notes_folder_moves import make_engine, markdown_files

from icloudbridge.core.rich_notes_export import RichNotesExporter
from icloudbridge.scripts.rich_notes import _runtime_hint


def lock(note):
    # What get_notes returns for a locked note: no body, flagged.
    return replace(note, body_html="", locked=True)


async def test_locked_note_is_not_exported(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u1", "u2"]})
    fake.notes["u2"] = lock(fake.notes["u2"])

    stats = await engine.sync_folder("Notes", markdown_subfolder="Notes")

    assert markdown_files(tmp_path) == ["Notes/Groceries.md"]
    assert stats["locked"] == 1 and stats["details"]["apple"]["locked"] == ["Plain"]


async def test_note_locked_after_syncing_keeps_its_markdown(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u1", "u2"]})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    copy = tmp_path / "md/Notes/Plain.md"
    original = copy.read_text()

    fake.notes["u2"] = lock(fake.notes["u2"])
    stats = await engine.sync_folder("Notes", markdown_subfolder="Notes")
    assert copy.read_text() == original
    assert stats["locked_markdown"] == 1

    # Editing the Markdown copy must not create, update or delete Apple notes.
    copy.write_text(original + "\nEdited while locked\n")
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    assert fake.created == [] and fake.deleted == []
    assert fake.notes["u2"].locked and "Edited while locked" in copy.read_text()
    assert len(await engine.db.get_all_mappings()) == 2


async def test_unsynced_markdown_named_like_a_locked_note_is_left_alone(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u2"]})
    fake.notes["u2"] = lock(fake.notes["u2"])
    (tmp_path / "md/Notes").mkdir(parents=True)
    (tmp_path / "md/Notes/Plain.md").write_text("# Plain\n\nOld copy\n")

    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    assert fake.created == []
    assert (tmp_path / "md/Notes/Plain.md").read_text() == "# Plain\n\nOld copy\n"


async def test_rich_export_skips_locked_notes(tmp_path, monkeypatch):
    remote = tmp_path / "md"
    (remote / "Notes").mkdir(parents=True)
    exporter = RichNotesExporter(tmp_path / "notes.db", remote)
    mappings = [
        {"local_uuid": "x-coredata://S/ICNote/p1", "remote_path": str(remote / "Notes/Open.md")},
        {"local_uuid": "x-coredata://S/ICNote/p2", "remote_path": str(remote / "Notes/Locked.md")},
    ]
    entries = {
        "x-coredata://S/ICNote/p1": {"html": '<div class="note-content"><p>Open</p></div>'},
        "x-coredata://S/ICNote/p2": {
            "html": '<div class="note-content">{Contents not decrypted}</div>',
            "is_password_protected": True,
        },
    }
    monkeypatch.setattr(exporter, "_gather_mappings", lambda: mappings)
    monkeypatch.setattr(
        exporter._capture, "capture_indexes", lambda: {"by_uuid": entries, "by_note_id": {}}
    )

    exporter.export()

    written = sorted(p.name for p in (remote / "RichNotes").rglob("*_rich.md"))
    assert written == ["Open_rich.md"]


def test_gem_load_failure_explains_the_fix():
    output = "rubygems.rb:305:in 'Kernel#load': cannot load such file -- bundler/exe/bundle (LoadError)"
    app = _runtime_hint(output, {"ICLOUDBRIDGE_BUNDLE_PATH": "/x/bundle"})
    dev = _runtime_hint(output, {"BUNDLE_GEMFILE": "/repo/tools/notes_cloud_ripper/Gemfile"})
    assert "reinstalls them" in app
    assert "BUNDLE_GEMFILE=/repo/tools/notes_cloud_ripper/Gemfile bundle install" in dev
    assert _runtime_hint("some unrelated ripper crash", {}) is None


def swap_id(fake, folder, old_uuid, new_uuid, *, locked):
    """What Notes does when you lock (or unlock) a note: same note, new ID."""
    note = fake.notes.pop(old_uuid)
    note = replace(note, uuid=new_uuid, locked=locked, body_html="" if locked else note.body_html)
    fake.notes[new_uuid] = note
    fake.folders[folder] = [new_uuid if u == old_uuid else u for u in fake.folders[folder]]


async def test_locking_with_a_new_id_keeps_the_markdown(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u1", "u2"]})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    copy = tmp_path / "md/Notes/Plain.md"
    original = copy.read_text()

    swap_id(fake, "Notes", "u2", "u2-locked", locked=True)
    for dry_run in (True, False):
        stats = await engine.sync_folder("Notes", markdown_subfolder="Notes", dry_run=dry_run)
        assert stats["locked_markdown"] == 1
        assert stats.get("would_delete_remote", 0) == 0 and stats["deleted_remote"] == 0
    assert copy.read_text() == original
    assert {m["local_uuid"] for m in await engine.db.get_all_mappings()} == {"u1", "u2-locked"}


async def test_unlocking_with_a_new_id_resumes_syncing(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u2"]})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    swap_id(fake, "Notes", "u2", "u2-locked", locked=True)
    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    swap_id(fake, "Notes", "u2-locked", "u2-open", locked=False)
    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    assert markdown_files(tmp_path) == ["Notes/Plain.md"]
    assert fake.created == [] and fake.deleted == []
    assert [m["local_uuid"] for m in await engine.db.get_all_mappings()] == ["u2-open"]


async def test_a_deleted_note_is_not_mistaken_for_a_new_id(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u2"]})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    # Deleted (to Recently Deleted), then a new note with the same title.
    fake.trash.append(fake.notes.pop("u2"))
    fake.notes["u9"] = replace(fake.trash[0], uuid="u9")
    fake.folders["Notes"] = ["u9"]

    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    # Not repointed: a repoint would have moved u2's mapping onto u9.
    assert "u2" in {m["local_uuid"] for m in await engine.db.get_all_mappings()}


async def test_a_note_moved_elsewhere_is_not_mistaken_for_a_new_id(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u2"], "Archive": []})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    # u2 moved to Archive; an unrelated new note named "Plain" appears in Notes.
    fake.folders = {"Notes": ["u9"], "Archive": ["u2"]}
    fake.notes["u9"] = replace(fake.notes["u2"], uuid="u9")

    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    # Not repointed: u2 still exists (in Archive), so u9 is a different note.
    assert "u2" in {m["local_uuid"] for m in await engine.db.get_all_mappings()}


async def test_a_folder_of_only_locked_notes_still_syncs(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u2"]})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    swap_id(fake, "Notes", "u2", "u2-locked", locked=True)

    stats = await engine.sync_folder("Notes", markdown_subfolder="Notes")

    assert stats["locked"] == 1 and stats["locked_markdown"] == 1
    assert markdown_files(tmp_path) == ["Notes/Plain.md"]
