"""One failing folder must not stop `icloudbridge notes sync` from syncing the rest."""
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from typer.testing import CliRunner

from icloudbridge.cli.main import notes_app
from icloudbridge.core.sync import NotesSyncEngine


def test_a_failed_folder_is_reported_and_the_next_one_syncs(tmp_path, monkeypatch):
    synced: list[str] = []

    async def noop(self, *args, **kwargs):
        return 0

    async def list_folders(self):
        return [{"name": "Broken"}, {"name": "Fine"}]

    @asynccontextmanager
    async def rich_capture_scope(self):
        yield

    async def sync_folder(self, folder_name, *args, **kwargs):
        if folder_name == "Broken":
            raise RuntimeError("Sync failed for folder 'Broken': File name too long")
        synced.append(folder_name)
        keys = ("created_local", "created_remote", "updated_local", "updated_remote",
                "deleted_local", "deleted_remote", "would_delete_local", "would_delete_remote")
        return {**dict.fromkeys(keys, 0), "unchanged": 1, "pending_local_notes": []}

    monkeypatch.setattr(NotesSyncEngine, "initialize", noop)
    monkeypatch.setattr(NotesSyncEngine, "migrate_root_notes_to_folder", noop)
    monkeypatch.setattr(NotesSyncEngine, "list_folders", list_folders)
    monkeypatch.setattr(NotesSyncEngine, "rich_capture_scope", rich_capture_scope)
    monkeypatch.setattr(NotesSyncEngine, "sync_folder", sync_folder)
    config = SimpleNamespace(
        general=SimpleNamespace(data_dir=tmp_path),
        notes=SimpleNamespace(enabled=True, remote_folder=tmp_path / "md", folder_mappings={},
                              use_shortcuts_for_push=False),
        notes_db_path=tmp_path / "notes.db",
    )

    result = CliRunner().invoke(notes_app, ["sync"], obj={"config": config})

    # Before, reporting the error raised NameError: name 'logger' is not defined.
    assert not isinstance(result.exception, NameError), result.exception
    assert "Error syncing Broken" in result.output
    assert synced == ["Fine"]
