"""Moving root-level Markdown notes into 'Notes' must keep them paired with their Apple notes."""
from __future__ import annotations

from tests.test_notes_folder_moves import make_engine, markdown_files


async def test_synced_root_note_keeps_its_apple_note(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u1"]})
    # A note synced to the root of the destination (no subfolder).
    await engine.sync_folder("Notes", markdown_subfolder=None)
    assert markdown_files(tmp_path) == ["Groceries.md"]

    assert await engine.migrate_root_notes_to_folder() == 1
    mapping = (await engine.db.get_all_mappings())[0]
    assert mapping["remote_path"] == str(tmp_path / "md/Notes/Groceries.md")

    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    assert fake.deleted == [] and fake.created == []
    assert markdown_files(tmp_path) == ["Notes/Groceries.md"]


async def test_existing_note_with_the_same_name_is_not_overwritten(tmp_path):
    engine, _fake = await make_engine(tmp_path, {"Notes": []})
    (tmp_path / "md/Notes").mkdir(parents=True)
    (tmp_path / "md/Notes/Ideas.md").write_text("# Ideas\n\nThe one in Notes\n")
    (tmp_path / "md/Ideas.md").write_text("# Ideas\n\nThe one in the root\n")

    assert await engine.migrate_root_notes_to_folder() == 0

    assert (tmp_path / "md/Notes/Ideas.md").read_text() == "# Ideas\n\nThe one in Notes\n"
    assert (tmp_path / "md/Ideas.md").read_text() == "# Ideas\n\nThe one in the root\n"


async def test_attachments_move_with_their_note(tmp_path):
    engine, _fake = await make_engine(tmp_path, {"Notes": ["u1"]})
    await engine.sync_folder("Notes", markdown_subfolder=None)
    slug = await engine.markdown_adapter.get_attachment_slug(tmp_path / "md/Groceries.md")
    (tmp_path / f"md/.attachments.{slug}").mkdir()
    (tmp_path / f"md/.attachments.{slug}/photo.png").write_bytes(b"png")

    assert await engine.migrate_root_notes_to_folder() == 1

    assert (tmp_path / f"md/Notes/.attachments.{slug}/photo.png").read_bytes() == b"png"
    assert not (tmp_path / f"md/.attachments.{slug}").exists()
