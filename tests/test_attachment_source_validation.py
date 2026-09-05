"""Regression tests: a non-file attachment source must not fail the whole note.

Some Apple Notes embedded objects carry incomplete media metadata and resolve
to the account's ``Media`` directory rather than a payload file. A directory
passes ``exists()`` but raises ``[Errno 21] Is a directory`` from both
``shutil.copy2()`` and ``Path.read_bytes()``.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from icloudbridge.sources.notes.applescript import NotesAdapter
from icloudbridge.sources.notes.markdown import MarkdownAdapter


@pytest.fixture
def adapter(tmp_path) -> MarkdownAdapter:
    return MarkdownAdapter(tmp_path / "notes")


@pytest.fixture
def a_directory(tmp_path) -> Path:
    """What the ripper handed back for the crochet note: an account Media dir."""
    media = tmp_path / "Accounts" / "45FB8D26" / "Media"
    media.mkdir(parents=True)
    return media


@pytest.fixture
def a_file(tmp_path) -> Path:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"jpegdata")
    return photo


def test_directory_source_is_skipped_not_copied(adapter, a_directory, tmp_path, caplog):
    target = tmp_path / "out"
    target.mkdir()

    with patch("icloudbridge.sources.notes.markdown.shutil.copy2") as copy2:
        adapter._sync_attachments(target, {".attachments.x/Media": a_directory})

    copy2.assert_not_called()
    assert "expected a regular file" in caplog.text


def test_valid_source_is_still_copied(adapter, a_file, tmp_path):
    target = tmp_path / "out"
    target.mkdir()

    adapter._sync_attachments(target, {".attachments.x/photo.jpg": a_file})

    assert (target / ".attachments.x" / "photo.jpg").read_bytes() == b"jpegdata"


def test_mixed_sources_export_the_valid_ones(adapter, a_file, a_directory, tmp_path):
    """Graceful degradation: one bad object must not lose the good ones."""
    target = tmp_path / "out"
    target.mkdir()
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    adapter._sync_attachments(
        target,
        {
            ".attachments.x/photo.jpg": a_file,
            ".attachments.x/doc.pdf": pdf,
            ".attachments.x/Media": a_directory,
        },
    )

    assert (target / ".attachments.x" / "photo.jpg").is_file()
    assert (target / ".attachments.x" / "doc.pdf").is_file()
    assert not (target / ".attachments.x" / "Media").exists()


def test_inline_attachments_skip_a_directory(adapter, a_directory):
    """The base64 inline path raises the same Errno 21 and needs the same guard."""
    markdown = "![img](ref-to-media)"

    result = adapter._inline_markdown_attachments(markdown, {"ref-to-media": a_directory})

    assert result == markdown  # left untouched, and no exception


def test_inline_attachments_still_embed_a_real_file(adapter, a_file):
    markdown = "![img](ref-to-photo)"

    result = adapter._inline_markdown_attachments(markdown, {"ref-to-photo": a_file})

    assert "data:image/jpeg;base64," in result


def _entry(source: str, *, thumbnails: list | None = None) -> dict:
    return {
        "embedded_objects": [
            {
                "uuid": "OBJ-1",
                "backup_location": source,
                "filename": "whatever.jpg",
                "type": "public.jpeg",
                "thumbnails": thumbnails or [],
            }
        ]
    }


def test_embedded_object_resolving_to_a_directory_is_not_an_attachment(a_directory):
    notes = NotesAdapter()

    with patch.object(
        notes._rich_capture, "resolve_attachment_path", return_value=a_directory
    ):
        attachments = notes._rich_attachments_for_entry(_entry("Accounts/x/Media"))

    assert attachments == []


def test_embedded_object_resolving_to_a_file_is_an_attachment(a_file):
    notes = NotesAdapter()

    with patch.object(
        notes._rich_capture, "resolve_attachment_path", return_value=a_file
    ):
        attachments = notes._rich_attachments_for_entry(_entry("Accounts/x/Media/photo.jpg"))

    assert len(attachments) == 1
    assert attachments[0].source_path == a_file


def test_thumbnail_resolving_to_a_directory_is_skipped(a_directory, a_file):
    """The object itself is fine; only its thumbnail is bad."""
    notes = NotesAdapter()
    entry = _entry(
        "Accounts/x/Media/photo.jpg",
        thumbnails=[{"uuid": "THUMB-1", "backup_location": "Accounts/x/Media"}],
    )

    def resolve(path):
        return a_directory if path == "Accounts/x/Media" else a_file

    with patch.object(notes._rich_capture, "resolve_attachment_path", side_effect=resolve):
        attachments = notes._rich_attachments_for_entry(entry)

    assert [a.uuid for a in attachments] == ["OBJ-1"]
