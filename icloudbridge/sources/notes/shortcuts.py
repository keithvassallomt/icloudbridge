"""Utilities for invoking Apple Shortcuts used during checklist sync."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import plistlib
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from icloudbridge.utils.converters import add_markdown_soft_breaks, insert_markdown_blank_line_markers
logger = logging.getLogger(__name__)

SHORTCUTS_DB_PATH = Path.home() / "Library/Shortcuts/Shortcuts.sqlite"
VERSION_RE = re.compile(r"iCloudBridge version:\s*(\d+)")

# The first Upsert version that applies tags passed as a third input field
UPSERT_TAGS_VERSION = 2


def installed_shortcut_version(name: str) -> int | None:
    """
    Version of an installed iCloudBridge Shortcut, from the "iCloudBridge
    version: N" line in its leading comment. Shortcuts from before versioning
    have no such line and count as version 1.

    Returns None when the Shortcut is not installed or the library can't be read.
    """
    try:
        with contextlib.closing(sqlite3.connect(SHORTCUTS_DB_PATH.as_uri() + "?mode=ro", uri=True)) as conn:
            row = conn.execute(
                "SELECT a.ZDATA FROM ZSHORTCUT s JOIN ZSHORTCUTACTIONS a ON a.ZSHORTCUT = s.Z_PK "
                "WHERE s.ZNAME = ? AND NOT coalesce(s.ZTOMBSTONED, 0)",
                (name,),
            ).fetchone()
    except sqlite3.Error as exc:
        logger.warning("Could not read the Shortcuts library at %s: %s", SHORTCUTS_DB_PATH, exc)
        return None
    if not row or not row[0]:
        return None
    return shortcut_version_from_actions(row[0])


def shortcut_version_from_actions(actions_plist: bytes) -> int:
    """Read the version marker from a Shortcut's serialized action list."""
    try:
        actions = plistlib.loads(actions_plist)
    except Exception:  # noqa: BLE001 - an unreadable Shortcut is simply unversioned
        return 1
    for action in actions[:1]:
        comment = action.get("WFWorkflowActionParameters", {}).get("WFCommentActionText", "")
        match = VERSION_RE.search(comment or "")
        if match:
            return int(match.group(1))
    return 1


class NotesShortcutAdapter:
    """Runs the bespoke shortcuts that rebuild Apple Notes from markdown."""

    UPSERT_SHORTCUT = "iCloudBridge_Upsert_Note"
    APPEND_CHECKLIST_SHORTCUT = "iCloudBridge_Append_Checklist_To_Note"
    APPEND_CONTENT_SHORTCUT = "iCloudBridge_Append_Content_To_Note"

    def __init__(self, call_log: list[dict[str, str | None]] | None = None) -> None:
        self.call_log = call_log if call_log is not None else []
        self._supports_tags: bool | None = None

    def supports_tags(self) -> bool:
        """Whether the installed Upsert Shortcut can apply tags (checked once)."""
        if self._supports_tags is None:
            version = installed_shortcut_version(self.UPSERT_SHORTCUT)
            self._supports_tags = version is not None and version >= UPSERT_TAGS_VERSION
        return self._supports_tags

    async def upsert_note(self, folder: str, title: str, tags: list[str] | None = None) -> None:
        folder = self._normalize_folder(folder)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._run_upsert, folder, title, tags or [])

    async def append_checklist(self, folder: str, title: str, checklist_markdown: str) -> None:
        folder = self._normalize_folder(folder)
        payload = f"{folder}\n{title}\n\n{checklist_markdown.rstrip()}\n"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._run_file_shortcut,
            self.APPEND_CHECKLIST_SHORTCUT,
            folder,
            title,
            payload,
        )

    async def append_content(self, folder: str, title: str, markdown_block: str) -> None:
        folder = self._normalize_folder(folder)
        normalized_block = add_markdown_soft_breaks(markdown_block)
        normalized_block = insert_markdown_blank_line_markers(normalized_block)
        payload = f"{folder}\n{title}\n\n{normalized_block.rstrip()}\n"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._run_file_shortcut,
            self.APPEND_CONTENT_SHORTCUT,
            folder,
            title,
            payload,
        )

    # A shortcut that asks a question (a "which one?" prompt, say) waits for an
    # answer forever; a scheduled sync must not wait with it.
    SHORTCUT_TIMEOUT_SECONDS = 120

    @classmethod
    def _run_shortcut(cls, cmd: list[str], input: str | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                cmd,
                input=input,
                text=True,
                capture_output=True,
                check=False,
                timeout=cls.SHORTCUT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Shortcut {cmd[2]} didn't finish within {cls.SHORTCUT_TIMEOUT_SECONDS} seconds. "
                "It may be waiting for an answer in a Shortcuts window; cancel it there."
            ) from None

    @staticmethod
    def _normalize_folder(folder: str) -> str:
        parts = folder.split("/", 1)
        if len(parts) == 2 and parts[0].strip().lower() in {"icloud", "on my mac"}:
            return parts[1]
        return folder

    def _run_upsert(self, folder: str, title: str, tags: list[str]) -> None:
        # The tags field is left out entirely when empty: the Shortcut only reads
        # it when there are more than two fields, as Shortcuts treats an empty
        # field unpredictably and would prompt for a tag name.
        data = f"{folder};;{title}"
        if tags:
            data += ";;" + ",".join(tags)
        cmd = ["shortcuts", "run", self.UPSERT_SHORTCUT]
        result = self._run_shortcut(cmd, input=data)
        if result.returncode != 0:
            raise RuntimeError(
                f"Shortcut {self.UPSERT_SHORTCUT} failed: {result.stderr or result.stdout}"
            )
        self._record_call(self.UPSERT_SHORTCUT, folder, title, None)

    def _run_file_shortcut(
        self,
        shortcut_name: str,
        folder: str,
        title: str,
        payload: str,
    ) -> None:
        # The .txt suffix matters: without one, macOS guesses the type from the
        # content, and a note with enough code in it (a few Python imports) reads
        # as a script. The Shortcut then gets the file's name, not its text, and
        # fails with "you asked for item 2, but the list only has 1".
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
            tmp_path = Path(tmp_file.name)

        keep_file = folder == "Bridge" and title == "Magic Note"
        logger.debug(
            "Shortcut %s payload file created for %s/%s at %s",
            shortcut_name,
            folder,
            title,
            tmp_path,
        )

        try:
            cmd = [
                "shortcuts",
                "run",
                shortcut_name,
                "--input-path",
                str(tmp_path),
                "--output-type",
                "public.rtf",
            ]
            result = self._run_shortcut(cmd)
            if result.returncode != 0:
                raise RuntimeError(
                    f"Shortcut {shortcut_name} failed: {result.stderr or result.stdout}"
                )
        finally:
            if keep_file:
                logger.debug("Preserving payload file %s for debugging", tmp_path)
            else:
                tmp_path.unlink(missing_ok=True)

        self._record_call(shortcut_name, folder, title, str(tmp_path))

    def _record_call(
        self,
        shortcut_name: str,
        folder: str,
        title: str,
        temp_path: str | None,
    ) -> None:
        entry = {
            "shortcut": shortcut_name,
            "folder": folder,
            "title": title,
            "temp_path": temp_path,
        }
        self.call_log.append(entry)
