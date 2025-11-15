"""Shared helpers for capturing rich Apple Notes data via the ripper pipeline."""
from __future__ import annotations

import json
import logging
import shutil
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
        self._active_dir: Path | None = None
        self._output_dir: Path | None = None
        self._workspaces: list[Path] = []
        self._container_dir: Path | None = None

    def capture_indexes(self) -> dict[str, dict[str, Any]]:
        """Return lookup indexes for all rich notes currently on disk."""
        tmp_dir = Path(tempfile.mkdtemp(prefix="icloudbridge_notes_rip_"))
        self._active_dir = tmp_dir
        self._workspaces.append(tmp_dir)

        ripper_output = tmp_dir / "ripper_output"
        ripper_output.mkdir(parents=True, exist_ok=True)
        self._output_dir = ripper_output

        container_dir = tmp_dir / "notes_container"
        self._container_dir = container_dir
        note_store = self._copy_notes_container(container_dir)
        self._run_ripper(note_store, ripper_output)

        json_file = self._find_json(ripper_output)
        data = json.loads(json_file.read_text(encoding="utf-8"))

        return build_note_indexes(data.get("notes", {}))

    def capture_entry_map(self) -> dict[str, dict[str, Any]]:
        """Convenience accessor returning a UUID -> entry mapping."""
        indexes = self.capture_indexes()
        return indexes["by_uuid"]

    def resolve_attachment_path(self, relative_path: str | None) -> Path | None:
        """Translate a ripper-provided attachment path into a real file path."""

        if not relative_path:
            return None

        candidate = Path(relative_path)
        if candidate.is_absolute() and candidate.exists():
            return candidate

        if not self._output_dir:
            return None

        rel = Path(relative_path.lstrip("/\\"))
        candidate = self._output_dir / rel
        if candidate.exists():
            return candidate

        return None

    def cleanup(self) -> None:
        """Remove the most recent ripper workspace (if any)."""

        for workspace in list(self._workspaces):
            if workspace.exists():
                try:
                    shutil.rmtree(workspace)
                except OSError as exc:
                    logger.debug("Failed to remove ripper workspace %s: %s", workspace, exc)
        self._workspaces.clear()
        self._active_dir = None
        self._output_dir = None
        self._container_dir = None

    def _copy_notes_container(self, destination: Path) -> Path:
        script = self.repo_root / "tools" / "note_db_copy" / "copy_note_db.py"
        cmd = [
            "/usr/bin/python3",
            str(script),
            "--mode",
            "container",
            "--dest",
            str(destination),
            "--source",
            str(Path.home() / "Library/Group Containers/group.com.apple.notes"),
        ]
        logger.info("Copying Apple Notes container -> %s", destination)
        subprocess.run(cmd, check=True)
        note_store = destination / "NoteStore.sqlite"
        if not note_store.exists():
            raise FileNotFoundError(f"NoteStore.sqlite not found in copied container {destination}")
        return note_store

    def _run_ripper(self, db_path: Path, output_dir: Path) -> None:
        args = ["--output-dir", str(output_dir)]
        if self._container_dir and self._container_dir.exists():
            args.extend(["--mac", str(self._container_dir)])
        else:
            args.extend(["--file", str(db_path)])
        logger.info("Running rich notes ripper (output=%s)", output_dir)
        run_rich_ripper(args, log_stream=logger, log_category="notes_ripper", log_level="DEBUG")

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
