"""Attachment folder names must fit the 255-byte file-name limit."""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import replace
from datetime import datetime

from icloudbridge.core import sync as sync_module
from icloudbridge.sources.notes.applescript import AppleNoteAttachment
from icloudbridge.utils.slugs import MAX_SLUG_BASE_LENGTH, generate_attachment_slug
from tests.test_notes_folder_moves import make_engine

URL_TITLE = "https://docker.events.example.net/docker/dockercon?mkt_tok=" + "eyJpIjoiWVdaak9ESmxaakZ" * 12 + "%3D%3D"


def test_long_title_slug_fits_an_attachment_folder_name(tmp_path):
    slug = generate_attachment_slug(URL_TITLE)
    folder = tmp_path / f".attachments.{slug}"

    assert len(folder.name.encode("utf-8")) <= 255
    folder.mkdir()  # the real filesystem accepts it
    base = slug.rsplit("-", 1)[0]
    assert len(base) <= MAX_SLUG_BASE_LENGTH and not base.endswith("-")


def test_short_titles_keep_their_readable_slug():
    assert re.fullmatch(r"meeting-notes-[0-9a-f]{8}", generate_attachment_slug("Meeting notes"))
    assert re.fullmatch(r"note-[0-9a-f]{8}", generate_attachment_slug("???"))


def _with_photo(tmp_path, note, body):
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"\x89PNG")
    attachment = AppleNoteAttachment(uuid="a1", filename="photo.png", source_path=photo, uti="public.png")
    return replace(note, attachments=[attachment], body_html=body, modified_date=datetime.now())


def _attachment_folders(tmp_path):
    return [p for p in (tmp_path / "md").rglob(".attachments.*") if p.is_dir()]


async def test_long_titled_note_with_an_attachment_syncs(tmp_path):
    # Mail-account notes come with their full title (iCloud shortens it).
    engine, fake = await make_engine(tmp_path, {"Google/Notes": ["u1"]})
    fake.notes["u1"] = _with_photo(tmp_path, replace(fake.notes["u1"], name=URL_TITLE), f"<div>{URL_TITLE}</div>")

    await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes")

    [folder] = _attachment_folders(tmp_path)
    assert (folder / "photo.png").exists()


async def test_too_long_slug_stored_by_an_older_release_is_replaced(tmp_path, monkeypatch):
    engine, fake = await make_engine(tmp_path, {"Google/Notes": ["u1"]})
    fake.notes["u1"] = replace(fake.notes["u1"], name=URL_TITLE, body_html=f"<div>{URL_TITLE}</div>")

    def uncapped_slug(source=None):  # before the cap
        return re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-") + "-" + secrets.token_hex(4)

    monkeypatch.setattr(sync_module, "generate_attachment_slug", uncapped_slug)
    await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes")
    monkeypatch.undo()
    [sidecar] = (tmp_path / "md").rglob(".*.meta.json")
    assert len(json.loads(sidecar.read_text())["attachment_slug"]) > 233

    # An image is added to the note later. Reusing the stored slug failed the folder.
    fake.notes["u1"] = _with_photo(tmp_path, fake.notes["u1"], f"<div>{URL_TITLE}</div><div>photo</div>")
    await engine.sync_folder("Google/Notes", markdown_subfolder="Google/Notes")

    [folder] = _attachment_folders(tmp_path)
    assert (folder / "photo.png").exists()
    slug = json.loads(sidecar.read_text())["attachment_slug"]
    assert folder.name == f".attachments.{slug}"
    assert (await engine.db.get_mapping("u1"))["attachment_slug"] == slug
