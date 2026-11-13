"""Attachment folder names must fit the 255-byte file-name limit."""
from __future__ import annotations

import re

from icloudbridge.utils.slugs import MAX_SLUG_BASE_LENGTH, generate_attachment_slug

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
