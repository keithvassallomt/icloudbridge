"""Simulate (a dry run) must not write to the notes database; it can run alongside a real sync."""
from __future__ import annotations

from tests.test_notes_folder_moves import make_engine


async def test_dry_run_pairing_by_title_writes_no_mapping(tmp_path):
    # An Apple note and a Markdown file with the same title, never synced.
    engine, _fake = await make_engine(tmp_path, {"Notes": ["u1"]})
    folder = tmp_path / "md/Notes"
    folder.mkdir(parents=True)
    (folder / "Groceries.md").write_text("# Groceries\n\nMilk\n")

    stats = await engine.sync_folder("Notes", markdown_subfolder="Notes", dry_run=True)

    assert stats["details"]["apple"]["unchanged"] == ["Groceries"]  # paired in the preview
    assert await engine.db.get_all_mappings() == []

    # A real sync then pairs them as before.
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    [mapping] = await engine.db.get_all_mappings()
    assert mapping["local_uuid"] == "u1" and mapping["remote_path"].endswith("Notes/Groceries.md")
