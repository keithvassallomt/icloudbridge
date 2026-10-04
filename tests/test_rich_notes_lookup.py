"""Rich note lookups must not confuse note IDs with content-record IDs."""

import pytest

from icloudbridge.core.rich_notes_capture import build_note_indexes, lookup_note_entry


@pytest.fixture
def entries():
    # Mirrors the Ruby ripper: primary_key is ZICNOTEDATA.Z_PK, while
    # note_id is ZICNOTEDATA.ZNOTE = ZICCLOUDSYNCINGOBJECT.Z_PK.
    return {
        "note-a": {
            "uuid": "note-a",
            "primary_key": 4,
            "note_id": 12,
            "title": "Note A",
            "html": '<div class="note-content">Body A</div>',
            "embedded_objects": [{"uuid": "attachment-a"}],
        },
        "note-b": {
            "uuid": "note-b",
            "primary_key": 12,
            "note_id": 16,
            "title": "Note B",
            "html": '<div class="note-content">Body B</div>',
            "embedded_objects": [{"uuid": "attachment-b"}],
        },
    }


@pytest.mark.parametrize("identifier", ["x-coredata://store/ICNote/p12", "12"])
def test_note_id_wins_over_unrelated_content_record(identifier, entries):
    result = lookup_note_entry(identifier, build_note_indexes(entries))

    # Whole-entry identity also protects rich HTML and attachment association.
    assert result is entries["note-a"]
    assert result is not entries["note-b"]


@pytest.mark.parametrize("identifier", ["x-coredata://store/ICNote/p12", "12"])
def test_missing_note_does_not_fall_back_to_content_record(identifier, entries):
    del entries["note-a"]

    assert lookup_note_entry(identifier, build_note_indexes(entries)) is None


def test_uuid_lookup_is_preserved(entries):
    assert lookup_note_entry("note-b", build_note_indexes(entries)) is entries["note-b"]


def test_exact_identifier_match_takes_precedence(entries):
    identifier = "x-coredata://store/ICNote/p12"
    entries["note-b"]["uuid"] = identifier

    assert lookup_note_entry(identifier, build_note_indexes(entries)) is entries["note-b"]


@pytest.mark.parametrize("identifier", ["unknown", "x-coredata://store/ICNote/pbad", "999"])
def test_unknown_identifiers_return_no_match(identifier, entries):
    assert lookup_note_entry(identifier, build_note_indexes(entries)) is None
