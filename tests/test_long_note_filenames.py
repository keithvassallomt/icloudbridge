"""Note titles longer than the file-name limit must still produce a file.

A Gmail note titled with a tracking URL failed with ENAMETOOLONG (errno 63):
sanitize_filename capped characters, not bytes, at 255, and the writer then
appended ".md". The whole folder's sync aborted on that one note.
"""
from __future__ import annotations

import pytest

from icloudbridge.sources.notes.markdown import MarkdownAdapter
from icloudbridge.utils.converters import (
    FILENAME_MAX_BYTES,
    NOTE_STEM_MAX_BYTES,
    note_filename_stem,
    sanitize_filename,
)

URL_TITLE = "https://docker.events.example.net/docker/dockercon?mkt_tok=" + "eyJpIjoiWVdaak9ESmxaakZ" * 12 + "%3D%3D"


def nbytes(name: str) -> int:
    return len(name.encode("utf-8"))


@pytest.mark.parametrize(
    "title",
    ["Linux permission", "Mission control", "v1.2 release notes", "Café ☕ plans", "notes.txt", "a" * 241],
)
def test_names_that_fit_are_unchanged(title):
    assert note_filename_stem(title) == sanitize_filename(title, 10_000)


@pytest.mark.parametrize("title", [URL_TITLE, "ü" * 200, "日本語のメモ" * 30, "word " * 80])
def test_long_titles_fit_the_byte_budget(title):
    stem = note_filename_stem(title)
    assert nbytes(stem) <= NOTE_STEM_MAX_BYTES
    assert nbytes(f".{stem}.md.meta.json") <= FILENAME_MAX_BYTES
    stem.encode("utf-8").decode("utf-8")  # cut on a character boundary


def test_long_titles_with_a_shared_prefix_stay_distinct():
    a, b = URL_TITLE + "A", URL_TITLE + "B"
    assert note_filename_stem(a) != note_filename_stem(b)
    assert note_filename_stem(a) == note_filename_stem(a), "the mapping must be deterministic"


def test_attachment_names_keep_a_real_extension():
    name = sanitize_filename("scan " * 80 + ".jpeg")
    assert name.endswith(".jpeg") and nbytes(name) <= FILENAME_MAX_BYTES


def test_dotted_title_does_not_keep_a_long_tail_as_extension():
    name = sanitize_filename(URL_TITLE)
    assert nbytes(name) <= FILENAME_MAX_BYTES
    assert name.startswith("https--docker.events")


async def test_markdown_adapter_writes_and_reads_back_a_url_titled_note(tmp_path):
    adapter = MarkdownAdapter(tmp_path)

    path = await adapter.write_note(
        URL_TITLE, "<div>link</div>", folder_name="Mxv", metadata={"apple_note_uuid": "x-coredata://M/IMAPNote/p1"}
    )

    assert path.exists() and nbytes(path.name) <= FILENAME_MAX_BYTES
    assert (path.parent / f".{path.name}.meta.json").exists(), "the metadata sidecar must fit too"
    note = await adapter.read_note(path)
    # The sync matcher compares titles through note_filename_stem, so the
    # stem read back must equal the stem the title maps to.
    assert note_filename_stem(note.name) == note_filename_stem(URL_TITLE)
