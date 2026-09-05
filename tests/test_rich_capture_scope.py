"""The rich Notes snapshot is captured once per sync operation, not per folder."""
from __future__ import annotations

from icloudbridge.core.sync import NotesSyncEngine


class FakeNotesAdapter:
    """Records the capture calls a sync operation makes."""

    def __init__(self) -> None:
        self.refreshes = 0
        self.ensures = 0
        self.cleanups = 0
        self._cached = False

    async def refresh_rich_cache(self) -> None:
        self.refreshes += 1
        self._cached = True

    async def ensure_rich_cache(self) -> None:
        self.ensures += 1
        if not self._cached:
            self.refreshes += 1
            self._cached = True

    def clear_rich_cache(self, *, cleanup_workspace: bool = False) -> None:
        self._cached = False
        if cleanup_workspace:
            self.cleanups += 1

    @property
    def captures(self) -> int:
        return self.refreshes


def make_engine(tmp_path) -> NotesSyncEngine:
    engine = NotesSyncEngine(
        markdown_base_path=tmp_path / "md",
        db_path=tmp_path / "notes.db",
    )
    engine.notes_adapter = FakeNotesAdapter()
    return engine


async def test_scope_captures_once_across_folders(tmp_path):
    engine = make_engine(tmp_path)
    adapter = engine.notes_adapter

    async with engine.rich_capture_scope():
        for _ in range(6):
            await adapter.ensure_rich_cache()

    assert adapter.captures == 1, "the ripper should run once per operation"
    assert adapter.cleanups == 1, "the workspace should be torn down once"


async def test_scope_recaptures_after_an_invalidating_write(tmp_path):
    """A Shortcuts write inside the operation must still force a fresh snapshot."""
    engine = make_engine(tmp_path)
    adapter = engine.notes_adapter

    async with engine.rich_capture_scope():
        await adapter.ensure_rich_cache()
        adapter.clear_rich_cache()  # what the Shortcuts path does after a write
        await adapter.ensure_rich_cache()

    assert adapter.captures == 2


async def test_nested_scopes_tear_down_only_once(tmp_path):
    """sync_with_mappings opening a scope inside a caller's must not end it early."""
    engine = make_engine(tmp_path)
    adapter = engine.notes_adapter

    async with engine.rich_capture_scope():
        async with engine.rich_capture_scope():
            await adapter.ensure_rich_cache()
        # The inner scope has exited; the snapshot must still be live.
        assert adapter.cleanups == 0
        await adapter.ensure_rich_cache()

    assert adapter.captures == 1
    assert adapter.cleanups == 1


async def test_scope_state_is_reset_when_a_folder_raises(tmp_path):
    engine = make_engine(tmp_path)

    try:
        async with engine.rich_capture_scope():
            raise RuntimeError("folder blew up")
    except RuntimeError:
        pass

    assert engine._rich_capture_scoped is False
    assert engine.notes_adapter.cleanups == 1
