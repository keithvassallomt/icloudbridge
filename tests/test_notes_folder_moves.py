"""Notes folder handling: moves, Smart Folders (issue #18) and folders created from markdown."""
from __future__ import annotations

from datetime import datetime

import pytest

from icloudbridge.core.sync import NotesSyncEngine
from icloudbridge.sources.notes import applescript
from icloudbridge.sources.notes.applescript import (
    AppleScriptFolder,
    AppleScriptNote,
    NotesAdapter,
    folder_primary_key,
)

SYNCED = datetime(2026, 9, 1)


def make_note(uuid: str, name: str) -> AppleScriptNote:
    return AppleScriptNote(
        uuid=uuid,
        name=name,
        created_date=SYNCED,
        modified_date=SYNCED,
        body_html=f"<div>{name}</div>",
        attachments=[],
    )


class FakeNotesAdapter:
    """Notes.app stand-in: each note lives once but may be listed by several folders."""

    def __init__(self, folders: dict[str, list[str]], notes: dict[str, AppleScriptNote]) -> None:
        self.folders = folders
        self.notes = notes
        self.trash: list[AppleScriptNote] = []
        self.deleted: list[str] = []
        self.created: list[str] = []

    def is_ignored_folder(self, name: str) -> bool:
        return name == "Recently Deleted"

    async def refresh_rich_cache(self) -> None:
        pass

    async def ensure_rich_cache(self) -> None:
        pass

    def clear_rich_cache(self, *, cleanup_workspace: bool = False) -> None:
        pass

    async def get_recently_deleted_notes(self) -> list[AppleScriptNote]:
        return list(self.trash)

    async def note_exists(self, uuid: str) -> bool:
        return uuid in self.notes

    async def get_notes(self, folder: str) -> list[AppleScriptNote]:
        return [self.notes[uuid] for uuid in self.folders[folder] if uuid in self.notes]

    async def list_folders(self, include_unsyncable: bool = False):
        return [AppleScriptFolder(uuid=name, name=name) for name in self.folders]

    async def list_accounts(self) -> tuple[str, list[str]]:
        return "iCloud", ["iCloud"]

    async def create_folder(self, folder_path: str) -> None:
        self.folders[folder_path] = []

    async def create_note(self, folder_name: str, note_title: str, body_html: str):
        self.created.append(note_title)
        uuid = f"new-{len(self.created)}"
        self.notes[uuid] = make_note(uuid, note_title)
        self.folders[folder_name].append(uuid)
        return uuid, SYNCED

    async def delete_note(self, folder: str, name: str) -> bool:
        uuid = next(u for u in self.folders[folder] if u in self.notes and self.notes[u].name == name)
        self.deleted.append(name)
        self.trash.append(self.notes.pop(uuid))
        return True


async def make_engine(tmp_path, folders) -> tuple[NotesSyncEngine, FakeNotesAdapter]:
    engine = NotesSyncEngine(
        markdown_base_path=tmp_path / "md",
        db_path=tmp_path / "notes.db",
        prefer_shortcuts=False,
    )
    await engine.initialize()
    notes = {"u1": make_note("u1", "Groceries"), "u2": make_note("u2", "Plain")}
    engine.notes_adapter = FakeNotesAdapter(folders, notes)
    return engine, engine.notes_adapter


def markdown_files(tmp_path) -> list[str]:
    return sorted(str(p.relative_to(tmp_path / "md")) for p in (tmp_path / "md").rglob("*.md"))


@pytest.mark.parametrize("order", [["Notes", "Archive"], ["Archive", "Notes"]])
async def test_moved_note_follows_its_folder(tmp_path, order):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u1", "u2"], "Archive": []})
    for folder in order:
        await engine.sync_folder(folder, markdown_subfolder=folder)

    fake.folders = {"Notes": ["u2"], "Archive": ["u1"]}
    for folder in order:
        await engine.sync_folder(folder, markdown_subfolder=folder)

    assert fake.deleted == [] and fake.created == []
    assert markdown_files(tmp_path) == ["Archive/Groceries.md", "Notes/Plain.md"]


async def test_moved_note_keeps_metadata_and_attachments(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u1"], "Archive": []})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    slug = await engine.markdown_adapter.get_attachment_slug(tmp_path / "md/Notes/Groceries.md")
    attachment = tmp_path / f"md/Notes/.attachments.{slug}/photo.png"
    attachment.parent.mkdir()
    attachment.write_bytes(b"png")

    fake.folders = {"Notes": [], "Archive": ["u1"]}
    await engine.sync_folder("Archive", markdown_subfolder="Archive")

    moved = tmp_path / "md/Archive/Groceries.md"
    assert await engine.markdown_adapter.get_attachment_slug(moved) == slug
    assert (tmp_path / f"md/Archive/.attachments.{slug}/photo.png").read_bytes() == b"png"
    assert [m["remote_path"] for m in await engine.db.get_all_mappings()] == [str(moved)]


async def test_note_listed_in_two_folders_is_not_deleted(tmp_path):
    """What a Smart Folder looked like to the engine before it was filtered out."""
    engine, fake = await make_engine(tmp_path, {"Notes": ["u1", "u2"], "Tagged": ["u1"]})
    for folder in ["Notes", "Tagged", "Notes"]:
        await engine.sync_folder(folder, markdown_subfolder=folder)

    assert fake.deleted == []
    assert sorted(fake.notes) == ["u1", "u2"]


async def test_deleted_markdown_still_deletes_the_note(tmp_path):
    engine, fake = await make_engine(tmp_path, {"Notes": ["u1", "u2"]})
    await engine.sync_folder("Notes", markdown_subfolder="Notes")
    (tmp_path / "md/Notes/Groceries.md").unlink()

    await engine.sync_folder("Notes", markdown_subfolder="Notes")

    assert fake.deleted == ["Groceries"]


def write_markdown(tmp_path, relative: str) -> None:
    path = tmp_path / "md" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {path.stem}\n\nBody\n", encoding="utf-8")


async def test_mapping_creates_missing_apple_folders(tmp_path):
    """A folder that only exists in markdown is created in the default account."""
    engine, fake = await make_engine(tmp_path, {"iCloud/Notes": []})
    write_markdown(tmp_path, "Linux Hacks/Wifi Bug.md")
    write_markdown(tmp_path, "Linux Hacks/Kernel/Modules.md")
    mapping = {"Linux Hacks": {"markdown_folder": "Linux Hacks", "mode": "bidirectional"}}

    results = await engine.sync_with_mappings(mapping)

    assert "error" not in results["Linux Hacks"]
    assert list(fake.folders) == ["iCloud/Notes", "iCloud/Linux Hacks", "iCloud/Linux Hacks/Kernel"]
    assert sorted(fake.created) == ["Modules", "Wifi Bug"]


async def test_mapping_does_not_create_folders_on_dry_run_or_export(tmp_path):
    engine, fake = await make_engine(tmp_path, {"iCloud/Notes": []})
    write_markdown(tmp_path, "Linux Hacks/Wifi Bug.md")

    await engine.sync_with_mappings(
        {"Linux Hacks": {"markdown_folder": "Linux Hacks", "mode": "bidirectional"}}, dry_run=True
    )
    results = await engine.sync_with_mappings(
        {"Linux Hacks": {"markdown_folder": "Linux Hacks", "mode": "export"}}
    )

    assert list(fake.folders) == ["iCloud/Notes"]
    assert "does not exist" in results["Linux Hacks"]["error"]


class RefusingShortcuts:
    """Fails the test if the Shortcuts pipeline is used at all."""

    def __getattr__(self, name):
        raise AssertionError(f"Shortcuts should not be used, but {name} was called")


async def test_shortcuts_only_get_folder_names_they_can_resolve(tmp_path):
    folders = {"iCloud/Archive": [], "iCloud/Work/Archive": [], "iCloud/Personal/Drafts": []}
    engine, _ = await make_engine(tmp_path, folders)

    assert await engine._shortcut_folder_name("iCloud/Personal/Drafts") == "Drafts"
    assert await engine._shortcut_folder_name("iCloud/Work/Archive") is None
    assert await engine._shortcut_folder_name("iCloud/Archive") is None


async def test_shared_folder_name_is_written_with_applescript(tmp_path):
    engine, fake = await make_engine(tmp_path, {"iCloud/Archive": [], "iCloud/Work/Archive": []})
    engine.use_shortcut_pipeline = True
    engine.shortcuts = RefusingShortcuts()
    write_markdown(tmp_path, "Work Archive/Old.md")

    await engine.sync_folder("iCloud/Work/Archive", markdown_subfolder="Work Archive")

    assert fake.created == ["Old"]
    assert fake.folders["iCloud/Work/Archive"] == ["new-1"]


async def test_checklist_into_shared_folder_name_fails_clearly(tmp_path):
    engine, fake = await make_engine(tmp_path, {"iCloud/Archive": [], "iCloud/Work/Archive": []})
    engine.shortcuts = RefusingShortcuts()
    path = tmp_path / "md/Work Archive/Todo.md"
    path.parent.mkdir(parents=True)
    path.write_text("# Todo\n\n- [ ] one\n- [x] two\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Rename one of the folders"):
        await engine.sync_folder("iCloud/Work/Archive", markdown_subfolder="Work Archive")
    assert fake.created == []


def test_folder_primary_key():
    assert folder_primary_key("x-coredata://54E41D42-AD9B/ICFolder/p80") == 80
    assert folder_primary_key("not-a-coredata-id") is None


async def test_smart_folders_are_not_listed_or_read(monkeypatch):
    async def fake_applescript(script: str, *args: str) -> str:
        if script == applescript.LIST_FOLDERS_SCRIPT:
            return "x-coredata://S/ICFolder/p1~~iCloud/Notes|x-coredata://S/ICFolder/p7~~iCloud/Tagged"
        return "x-coredata://S/ICFolder/p7~~~FOLDER~~~"

    adapter = NotesAdapter()
    monkeypatch.setattr(adapter, "_run_applescript", fake_applescript)
    monkeypatch.setattr(adapter, "ensure_notes_running", lambda: _noop())
    monkeypatch.setattr(adapter, "ensure_rich_cache", lambda: _noop())
    monkeypatch.setattr(applescript, "special_folder_keys", lambda: ({7}, set()))

    assert [f.name for f in await adapter.list_folders()] == ["iCloud/Notes"]
    with pytest.raises(RuntimeError, match="Smart Folder"):
        await adapter.get_notes("iCloud/Tagged")


async def _noop() -> None:
    return None
