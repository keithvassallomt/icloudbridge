"""Regression tests: cloud-only export must not lose assets sharing a filename.

Filename is not a unique Photos identifier - a library can legitimately hold
several independent assets called ``IMG_1234.HEIC``. The cloud-only export
path used to index the files Photos wrote into a ``dict[str, Path]`` keyed by
lowercased filename, so N same-named assets collapsed to a single entry. The
first asset moved that file and every later one then resolved to a path that
no longer existed, failing with an unrelated error. In a library with 6,156
duplicated filenames that silently lost thousands of photos.

The pool must instead hand out each exported file exactly once.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from icloudbridge.core.photos_export_engine import PhotoExportEngine


def claim(pool: dict[str, list[Path]], *names: str) -> Path | None:
    return PhotoExportEngine._claim_exported_file(pool, *names)


def build_pool(tmp_path: Path, filenames: list[str]) -> dict[str, list[Path]]:
    """Mimic the temp-folder index built from what Photos actually exported."""
    pool: dict[str, list[Path]] = {}
    for name in filenames:
        f = tmp_path / name
        f.write_bytes(b"x")
        pool.setdefault(f.name.lower(), []).append(f)
    return pool


def test_three_assets_sharing_a_filename_claim_three_distinct_files(tmp_path):
    """The bug: A, B and C all named IMG_1234.HEIC must get three files."""
    # Photos renames collisions on export.
    pool = build_pool(
        tmp_path, ["IMG_1234.HEIC", "IMG_1234 2.HEIC", "IMG_1234 3.HEIC"]
    )

    claimed = [claim(pool, "IMG_1234.HEIC") for _ in range(3)]

    assert all(c is not None for c in claimed), "an asset was left unmatched"
    assert len({c.name for c in claimed}) == 3, "same file handed out twice"


def test_exhausted_pool_returns_none_rather_than_a_stale_path(tmp_path):
    """A 4th asset with no file left must fail cleanly, not reuse a claimed one."""
    pool = build_pool(tmp_path, ["IMG_1234.HEIC"])

    first = claim(pool, "IMG_1234.HEIC")
    second = claim(pool, "IMG_1234.HEIC")

    assert first is not None
    assert second is None


def test_claim_is_case_insensitive(tmp_path):
    pool = build_pool(tmp_path, ["IMG_0077.JPG"])
    assert claim(pool, "img_0077.jpg") is not None


def test_falls_back_to_database_filename(tmp_path):
    """Second name argument is the ZFILENAME fallback the engine passes."""
    pool = build_pool(tmp_path, ["ABC-123.jpeg"])
    assert claim(pool, "IMG_9999.JPG", "ABC-123.jpeg") is not None


def test_genuine_parenthesised_original_is_not_stolen_by_prefix_match(tmp_path):
    """"IMG_0465 (1).JPG" is a real original filename in the wild, not a collision.

    Exact matches must be preferred so the asset actually named
    ``IMG_0465.JPG`` does not consume the file belonging to the distinct
    asset named ``IMG_0465 (1).JPG``.
    """
    pool = build_pool(tmp_path, ["IMG_0465 (1).JPG", "IMG_0465.JPG"])

    exact = claim(pool, "IMG_0465.JPG")
    other = claim(pool, "IMG_0465 (1).JPG")

    assert exact.name == "IMG_0465.JPG"
    assert other.name == "IMG_0465 (1).JPG"


def test_prefix_fallback_only_matches_same_extension(tmp_path):
    """A Live Photo .mov must not be claimed in place of a missing .HEIC."""
    pool = build_pool(tmp_path, ["IMG_1234.mov"])
    assert claim(pool, "IMG_1234.HEIC") is None


def test_asset_without_its_own_mov_does_not_steal_a_same_named_one(tmp_path):
    """Live Photo pairing is exact-only.

    Two assets share the name IMG_1234.HEIC; only the second is a Live Photo,
    so only "IMG_1234 2.mov" exists. The first asset must not claim it, or the
    video would be filed against the wrong photo.
    """
    pool = build_pool(tmp_path, ["IMG_1234.HEIC", "IMG_1234 2.HEIC", "IMG_1234 2.mov"])

    first = claim(pool, "IMG_1234.HEIC")
    stolen = PhotoExportEngine._claim_exported_file(
        pool, Path(first.name).stem + ".mov", exact_only=True
    )

    assert first.name == "IMG_1234.HEIC"
    assert stolen is None, "claimed a Live Photo video belonging to another asset"


def test_live_photo_mov_is_claimed_separately_from_the_image(tmp_path):
    pool = build_pool(tmp_path, ["IMG_1234.HEIC", "IMG_1234.mov"])

    image = claim(pool, "IMG_1234.HEIC")
    mov = claim(pool, Path(image.name).stem + ".mov")

    assert image.name == "IMG_1234.HEIC"
    assert mov.name == "IMG_1234.mov"


@pytest.mark.asyncio
async def test_batch_with_duplicate_filenames_exports_every_asset(tmp_path):
    """End-to-end over _process_exported_batch: 3 duplicates -> 3 exports, 0 errors."""
    from dataclasses import dataclass

    from icloudbridge.core.photos_export_engine import ExportConfig

    @dataclass
    class FakeAsset:
        uuid: str
        filename: str
        original_filename: str | None
        media_type: str = "image"
        created_date: datetime = datetime(2026, 2, 1)

    class FakeDB:
        def __init__(self):
            self.records = []

        async def get_export_by_hash(self, h):
            return None

        async def get_by_hash(self, h):
            return None

        async def record_export(self, **kw):
            self.records.append(kw)

    export_folder = tmp_path / "out"
    export_folder.mkdir()
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    # Photos exported three distinct files for three same-named assets.
    for name, content in [
        ("IMG_1234.HEIC", b"aaa"),
        ("IMG_1234 2.HEIC", b"bbb"),
        ("IMG_1234 3.HEIC", b"ccc"),
    ]:
        (temp_dir / name).write_bytes(content)

    db = FakeDB()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")
    engine.db = db

    batch = [
        FakeAsset(uuid=f"uuid-{i}", filename=f"{i}.heic", original_filename="IMG_1234.HEIC")
        for i in range(3)
    ]

    exported, errors, skipped = await engine._process_exported_batch(batch, temp_dir)

    assert (exported, errors, skipped) == (3, 0, 0)
    assert len(db.records) == 3
    # Three distinct source files, so three distinct content hashes.
    assert len({r["content_hash"] for r in db.records}) == 3


# ------------------------------------------------- destination-path collisions


@pytest.mark.asyncio
async def test_different_photos_sharing_a_name_do_not_overwrite_each_other(tmp_path):
    """Same filename + same capture month = same destination folder.

    Before the fix, whichever photo was written last replaced the others on
    disk while every row still pointed at that one surviving file. A real
    library showed 3 distinct photos all recorded as 2026/06/image000000.jpg.
    """
    from icloudbridge.core.photos_export_engine import ExportConfig

    export_folder = tmp_path / "out"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")

    occupied = export_folder / "IMG_1234.HEIC"
    occupied.write_bytes(b"the first photo")

    resolved = await engine._resolve_dest_path(occupied, "hash-of-a-different-photo")

    assert resolved != occupied, "would have overwritten a different photo"
    assert not resolved.exists()
    assert occupied.read_bytes() == b"the first photo"


@pytest.mark.asyncio
async def test_resuming_reuses_the_path_when_content_is_identical(tmp_path):
    """A run that copied the file but died before recording it must not fork.

    Re-exporting the same photo should land on the same path, not create
    "IMG_1234 2.HEIC" beside an identical file.
    """
    from icloudbridge.core.photos_export_engine import ExportConfig

    export_folder = tmp_path / "out"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")

    existing = export_folder / "IMG_1234.HEIC"
    existing.write_bytes(b"same content")
    same_hash = await engine._hash_file(existing)

    resolved = await engine._resolve_dest_path(existing, same_hash)

    assert resolved == existing


@pytest.mark.asyncio
async def test_collision_suffixes_keep_climbing(tmp_path):
    from icloudbridge.core.photos_export_engine import ExportConfig

    export_folder = tmp_path / "out"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")

    (export_folder / "IMG_1234.HEIC").write_bytes(b"one")
    (export_folder / "IMG_1234 2.HEIC").write_bytes(b"two")

    resolved = await engine._resolve_dest_path(
        export_folder / "IMG_1234.HEIC", "a-third-distinct-hash"
    )

    assert resolved.name == "IMG_1234 3.HEIC"


@pytest.mark.asyncio
async def test_placement_is_atomic_so_a_kill_leaves_no_partial_at_dest(tmp_path):
    """A failure mid-write must not leave a truncated file at the final path."""
    from icloudbridge.core.photos_export_engine import ExportConfig

    export_folder = tmp_path / "out"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")

    dest = export_folder / "IMG_1234.HEIC"
    missing_source = tmp_path / "does-not-exist.HEIC"

    with pytest.raises((FileNotFoundError, OSError)):
        engine._place(missing_source, dest, move=False)

    assert not dest.exists(), "a partial file was left at the destination"
    assert list(export_folder.iterdir()) == [], "staging file was not cleaned up"


@pytest.mark.asyncio
async def test_successful_placement_lands_at_the_exact_path(tmp_path):
    from icloudbridge.core.photos_export_engine import ExportConfig

    export_folder = tmp_path / "out"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")

    src = tmp_path / "src.HEIC"
    src.write_bytes(b"payload")
    dest = export_folder / "IMG_1234.HEIC"

    engine._place(src, dest, move=True)

    assert dest.read_bytes() == b"payload"
    assert [p.name for p in export_folder.iterdir()] == ["IMG_1234.HEIC"]


# ------------------------------------------------------- disk space guardrails


def _engine_with_folder(tmp_path):
    from icloudbridge.core.photos_export_engine import ExportConfig

    export_folder = tmp_path / "out"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")
    return engine


def _asset(size):
    from dataclasses import dataclass

    @dataclass
    class A:
        uuid: str = "u"
        filename: str = "f.heic"
        original_filename: str = "F.HEIC"
        media_type: str = "image"
        file_size: int = 0
        file_path: Path | None = None
        created_date: datetime = datetime(2026, 2, 1)

    return A(file_size=size)


@pytest.mark.asyncio
async def test_export_refuses_to_start_when_it_would_fill_the_disk(tmp_path, monkeypatch):
    """Gail's run filled a boot disk and took the whole sync down with it."""
    import shutil as shutil_mod

    from icloudbridge.core.photos_export_engine import InsufficientDiskSpace

    engine = _engine_with_folder(tmp_path)

    class Usage:
        free = 1 * 1024**3  # 1 GB free

    monkeypatch.setattr(shutil_mod, "disk_usage", lambda p: Usage())

    huge = [(_asset(50 * 1024**3), "hash", tmp_path / "x")]  # 50 GB wanted

    with pytest.raises(InsufficientDiskSpace) as excinfo:
        await engine._check_disk_space(huge, [])

    message = str(excinfo.value)
    assert "50.0 GB" in message and "1.0 GB" in message, message


@pytest.mark.asyncio
async def test_export_proceeds_when_there_is_ample_room(tmp_path, monkeypatch):
    import shutil as shutil_mod

    engine = _engine_with_folder(tmp_path)

    class Usage:
        free = 500 * 1024**3

    monkeypatch.setattr(shutil_mod, "disk_usage", lambda p: Usage())

    await engine._check_disk_space([(_asset(1024**3), "hash", tmp_path / "x")], [])


@pytest.mark.asyncio
async def test_cloud_only_photos_count_toward_the_space_estimate(tmp_path, monkeypatch):
    """Cloud originals land on the same volume, so they must be counted too."""
    import shutil as shutil_mod

    from icloudbridge.core.photos_export_engine import InsufficientDiskSpace

    engine = _engine_with_folder(tmp_path)

    class Usage:
        free = 5 * 1024**3

    monkeypatch.setattr(shutil_mod, "disk_usage", lambda p: Usage())

    with pytest.raises(InsufficientDiskSpace):
        await engine._check_disk_space([], [_asset(10 * 1024**3)])


@pytest.mark.asyncio
async def test_real_file_sizes_are_measured_when_the_schema_lacks_zfilesize(
    tmp_path, monkeypatch
):
    """ZASSET has no ZFILESIZE on several macOS versions, so file_size is 0.

    Trusting it made the guard inert on exactly the machines that filled up.
    """
    import shutil as shutil_mod

    from icloudbridge.core.photos_export_engine import InsufficientDiskSpace

    engine = _engine_with_folder(tmp_path)

    original = tmp_path / "big.heic"
    original.write_bytes(b"x" * 4096)

    a = _asset(0)              # reader reported no size at all
    a.file_path = original

    class Usage:
        free = 1024  # less than the file we measured, plus the margin

    monkeypatch.setattr(shutil_mod, "disk_usage", lambda p: Usage())

    with pytest.raises(InsufficientDiskSpace):
        await engine._check_disk_space([(a, "hash", tmp_path / "d")], [])


# ------------------------------------------------------------------- staging


def test_staging_sits_beside_the_export_folder_not_inside_it(tmp_path):
    """Inside the export folder, NextCloud would sync half-written files.

    On the boot disk (tempfile's default) a big cloud export would fill the
    system volume while the destination sat empty.
    """
    from icloudbridge.core.photos_export_engine import ExportConfig

    export_folder = tmp_path / "nc-photos"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")

    root = engine._staging_root()

    assert export_folder not in root.parents and root != export_folder, (
        "staging must not live inside the synced export folder"
    )
    assert root.parent == export_folder.parent, "staging must share the volume"


def test_staging_dir_is_created_on_the_export_volume(tmp_path):
    from icloudbridge.core.photos_export_engine import ExportConfig

    export_folder = tmp_path / "nc-photos"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")

    staged = engine._make_staging_dir("photokit_")

    assert staged.exists()
    assert staged.parent == engine._staging_root()


def test_orphaned_staging_from_a_killed_run_is_cleared(tmp_path):
    from icloudbridge.core.photos_export_engine import ExportConfig

    export_folder = tmp_path / "nc-photos"
    export_folder.mkdir()
    engine = PhotoExportEngine.__new__(PhotoExportEngine)
    engine.config = ExportConfig(export_folder=export_folder, organize_by="flat")

    orphan = engine._make_staging_dir("photokit_")
    (orphan / "half-downloaded.HEIC").write_bytes(b"junk")

    engine._clear_orphaned_staging()

    assert not orphan.exists()
    assert engine._staging_root().exists(), "the root itself should survive"
