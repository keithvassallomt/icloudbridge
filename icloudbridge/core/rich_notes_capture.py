"""Shared helpers for capturing rich Apple Notes data via the ripper pipeline."""
from __future__ import annotations

import json
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from icloudbridge.scripts.rich_notes import run_rich_ripper

logger = logging.getLogger(__name__)


class RichNotesCapture:
    """Runs the Ruby ripper and exposes indexed rich-note metadata."""

    def __init__(self, *, repo_root: Path | None = None) -> None:
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]

    def capture_indexes(self) -> dict[str, dict[str, Any]]:
        """Return lookup indexes for all rich notes currently on disk."""
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_dir = Path(tmp_str)
            local_store = tmp_dir / "NoteStore.sqlite"
            ripper_output = tmp_dir / "ripper_output"
            ripper_output.mkdir(parents=True, exist_ok=True)

            self._copy_note_store(local_store)
            self._run_ripper(local_store, ripper_output)

            json_file = self._find_json(ripper_output)
            data = json.loads(json_file.read_text(encoding="utf-8"))

        return build_note_indexes(data.get("notes", {}))

    def capture_entry_map(self) -> dict[str, dict[str, Any]]:
        """Convenience accessor returning a UUID -> entry mapping."""
        indexes = self.capture_indexes()
        return indexes["by_uuid"]

    def _copy_note_store(self, destination: Path) -> None:
        script = self.repo_root / "tools" / "note_db_copy" / "copy_note_db.py"
        cmd = [
            "/usr/bin/python3",
            str(script),
            "--dest",
            str(destination),
        ]
        logger.info("Copying Apple Notes database -> %s", destination)
        subprocess.run(cmd, check=True)

    def _run_ripper(self, db_path: Path, output_dir: Path) -> None:
        args = ["--file", str(db_path), "--output-dir", str(output_dir)]
        logger.info("Running rich notes ripper (output=%s)", output_dir)
        run_rich_ripper(args)

    @staticmethod
    def _find_json(output_dir: Path) -> Path:
        candidates = list(output_dir.rglob("json/all_notes_*.json"))
        if not candidates:
            raise FileNotFoundError(f"Could not find all_notes_*.json under ripper output {output_dir}")
        return candidates[0]


def build_note_indexes(notes_section: Any) -> dict[str, dict[str, Any]]:
    """Mirror the Ruby ripper's sections into handy lookup tables."""
    by_uuid: dict[str, dict[str, Any]] = {}
    by_primary: dict[int, dict[str, Any]] = {}
    by_note_id: dict[int, dict[str, Any]] = {}

    if isinstance(notes_section, dict):
        for key, entry in notes_section.items():
            if not isinstance(entry, dict):
                continue
            uuid = entry.get("uuid") or key
            if uuid:
                by_uuid[str(uuid)] = entry

            pk = entry.get("primary_key")
            if isinstance(pk, int):
                by_primary[pk] = entry

            note_id = entry.get("note_id")
            if isinstance(note_id, int):
                by_note_id[note_id] = entry

    return {
        "by_uuid": by_uuid,
        "by_primary": by_primary,
        "by_note_id": by_note_id,
    }


def lookup_note_entry(local_uuid: str, indexes: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """Resolve a CoreData UUID/primary key/ICNote id into a ripper entry."""
    entry = indexes.get("by_uuid", {}).get(local_uuid)
    if entry:
        return entry

    pk = _extract_primary_key(local_uuid)
    if pk is not None:
        entry = indexes.get("by_primary", {}).get(pk)
        if entry:
            return entry

    note_id = _extract_note_id(local_uuid)
    if note_id is not None:
        entry = indexes.get("by_note_id", {}).get(note_id)
        if entry:
            return entry

    return None


def extract_note_content(note_entry: dict[str, Any]) -> str:
    """Trim the ripper's HTML blob down to the <div class="note-content"> body."""
    html = note_entry.get("html") or ""
    marker = '<div class="note-content">'
    if marker in html:
        body = html.split(marker, 1)[1]
        if body.endswith("</div>"):
            body = body.rsplit("</div>", 1)[0]
    else:
        body = html
    return body


def _extract_primary_key(coredata_id: str) -> int | None:
    if "/p" not in coredata_id:
        return None
    suffix = coredata_id.rsplit("/p", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return None


def _extract_note_id(coredata_id: str) -> int | None:
    suffix = coredata_id.rsplit("/", 1)[-1]
    if suffix.startswith("p"):
        suffix = suffix[1:]
    try:
        return int(suffix)
    except ValueError:
        return None
