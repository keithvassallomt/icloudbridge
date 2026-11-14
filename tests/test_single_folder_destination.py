"""Syncing one folder must use the folder mappings, like a full sync does."""
from __future__ import annotations

import pytest

from tests.test_notes_folder_moves import make_engine, markdown_files

MAPPINGS = {
    "iCloud/Recipes": {"markdown_folder": "Recipes", "mode": "bidirectional"},
    "iCloud/Work": {"markdown_folder": "Office", "mode": "export"},
    "Linux Hacks": {"markdown_folder": "Linux Hacks", "mode": "bidirectional"},
    "iCloud/Private": {},
}


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("iCloud/Recipes", ("Recipes", "bidirectional")),
        # The mapping's mode wins: an export-only folder stays export-only.
        ("iCloud/Work", ("Office", "export")),
        ("iCloud/Work/Projects", ("Office/Projects", "export")),
        # A key without an account is in the default account, as in a full sync.
        ("iCloud/Linux Hacks", ("Linux Hacks", "bidirectional")),
        # Not mapped: synced to its own path, as before.
        ("iCloud/Scratch", ("iCloud/Scratch", "import")),
    ],
)
async def test_single_folder_goes_where_the_mappings_put_it(tmp_path, folder, expected):
    engine, _fake = await make_engine(tmp_path, {"iCloud/Notes": []})
    assert await engine.single_folder_destination(folder, MAPPINGS, "import") == expected


async def test_excluded_folder_is_refused(tmp_path):
    engine, _fake = await make_engine(tmp_path, {"iCloud/Notes": []})
    with pytest.raises(ValueError, match="excluded"):
        await engine.single_folder_destination("iCloud/Private", MAPPINGS, "bidirectional")


async def test_without_mappings_nothing_changes(tmp_path):
    engine, _fake = await make_engine(tmp_path, {"iCloud/Notes": []})
    assert await engine.single_folder_destination("iCloud/Notes", {}, "bidirectional") == (
        "iCloud/Notes",
        "bidirectional",
    )


async def test_syncing_one_mapped_folder_leaves_its_files_in_place(tmp_path):
    engine, _fake = await make_engine(tmp_path, {"iCloud/Recipes": ["u1", "u2"]})
    await engine.sync_with_mappings({"iCloud/Recipes": MAPPINGS["iCloud/Recipes"]})
    assert markdown_files(tmp_path) == ["Recipes/Groceries.md", "Recipes/Plain.md"]

    folder, mode = await engine.single_folder_destination("iCloud/Recipes", MAPPINGS, "bidirectional")
    await engine.sync_folder("iCloud/Recipes", markdown_subfolder=folder, sync_mode=mode)

    # Before, the files moved to iCloud/Recipes/.
    assert markdown_files(tmp_path) == ["Recipes/Groceries.md", "Recipes/Plain.md"]


async def test_config_folder_mapping_objects_work_too(tmp_path):
    # The real config holds FolderMapping objects, not dicts.
    from icloudbridge.core.config import FolderMapping

    engine, _fake = await make_engine(tmp_path, {"iCloud/Notes": []})
    mappings = {"iCloud/Recipes": FolderMapping(markdown_folder="Recipes", mode="export")}
    assert await engine.single_folder_destination("iCloud/Recipes", mappings, "bidirectional") == (
        "Recipes",
        "export",
    )
