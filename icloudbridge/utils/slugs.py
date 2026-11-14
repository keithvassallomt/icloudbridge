"""Helpers for generating stable slugs/tokens."""

from __future__ import annotations

import re
import secrets

# The slug names a ".attachments.<slug>" folder, so it must stay well inside
# the 255-byte file-name limit; a note titled with a long URL produced a
# folder name that failed with ENAMETOOLONG and aborted the folder's sync.
# The base is ASCII, so characters equal bytes.
MAX_SLUG_BASE_LENGTH = 64


def generate_attachment_slug(source: str | None = None) -> str:
    """Generate a filesystem-friendly slug for attachment folders."""

    if source:
        base = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
        base = base[:MAX_SLUG_BASE_LENGTH].rstrip("-")
    else:
        base = "note"

    if not base:
        base = "note"

    suffix = secrets.token_hex(4)
    return f"{base}-{suffix}"

