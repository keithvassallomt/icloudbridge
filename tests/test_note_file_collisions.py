"""Notes must never share, overwrite or delete each other's Markdown file.

Seen on a real library: 1,725 notes mapped to 1,650 paths (ten notes titled
"TODO" wrote the same TODO.md), and updating a note whose title needs
sanitising ("Q&A: plans/ideas" -> "Q&A plans-ideas.md") wrote the file and
then deleted it as the "old" path of a rename.
"""
from __future__ import annotations

from icloudbridge.sources.notes.markdown import MarkdownAdapter
from icloudbridge.utils.converters import note_stem_matches_title

LONG = "https://example.net/track?tok=" + "x" * 300


async def write(adapter, title, body, folder="F"):
    return await adapter.write_note(title, f"<div>{body}</div>", folder_name=folder)


async def test_same_title_notes_get_their_own_files(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    paths = [await write(adapter, "TODO", f"body {i}") for i in range(3)]

    assert [p.name for p in paths] == ["TODO.md", "TODO (2).md", "TODO (3).md"]
    for i, path in enumerate(paths):
        assert f"body {i}" in path.read_text(encoding="utf-8")


async def test_names_differing_only_in_case_do_not_collide(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    first = await write(adapter, "Todo", "one")
    second = await write(adapter, "TODO", "two")

    assert first.name.casefold() != second.name.casefold()
    assert "one" in first.read_text(encoding="utf-8")


async def test_updating_a_sanitised_title_keeps_its_file(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    path = await write(adapter, "Q&A: plans/ideas", "v1")

    updated = await adapter.update_note(file_path=path, body_html="<div>v2</div>", note_name="Q&A: plans/ideas")

    assert updated == path and path.exists()
    assert "v2" in path.read_text(encoding="utf-8")


async def test_updating_a_long_title_keeps_its_file(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    path = await write(adapter, LONG, "v1")

    updated = await adapter.update_note(file_path=path, body_html="<div>v2</div>", note_name=LONG)

    assert updated == path and path.exists()


async def test_updating_a_numbered_duplicate_stays_in_place(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    first = await write(adapter, "TODO", "first")
    second = await write(adapter, "TODO", "second")

    updated = await adapter.update_note(file_path=second, body_html="<div>second v2</div>", note_name="TODO")

    assert updated == second and second.exists()
    assert "first" in first.read_text(encoding="utf-8"), "the other TODO must be untouched"


async def test_a_real_rename_still_moves_the_file(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    path = await write(adapter, "Old title", "v1")

    renamed = await adapter.update_note(file_path=path, body_html="<div>v1</div>", note_name="New title")

    assert renamed.name == "New title.md" and renamed.exists()
    assert not path.exists()


async def test_a_rename_onto_a_taken_name_does_not_overwrite(tmp_path):
    adapter = MarkdownAdapter(tmp_path)
    taken = await write(adapter, "Shared", "keep me")
    path = await write(adapter, "Mine", "mine")

    renamed = await adapter.update_note(file_path=path, body_html="<div>mine</div>", note_name="Shared")

    assert renamed.name == "Shared (2).md"
    assert "keep me" in taken.read_text(encoding="utf-8")


def test_stem_matching():
    assert note_stem_matches_title("TODO", "TODO")
    assert note_stem_matches_title("todo", "TODO")
    assert note_stem_matches_title("TODO (2)", "TODO")
    assert note_stem_matches_title("Q&A plans-ideas", "Q&A: plans/ideas")
    assert not note_stem_matches_title("TODO (1)", "TODO")
    assert not note_stem_matches_title("TODO list", "TODO")
