"""Helpers for generating stable slugs/tokens."""

from __future__ import annotations

import re
import secrets


def generate_attachment_slug(source: str | None = None) -> str:
    """Generate a filesystem-friendly slug for attachment folders."""

    if source:
        base = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
    else:
        base = "note"

    if not base:
        base = "note"

    suffix = secrets.token_hex(4)
    return f"{base}-{suffix}"

