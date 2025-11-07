"""Utilities for invoking Apple Shortcuts used during checklist sync."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


class NotesShortcutAdapter:
    """Runs the bespoke shortcuts that rebuild Apple Notes from markdown."""

    UPSERT_SHORTCUT = "iCloudBridge_Upsert_Note"
    APPEND_CHECKLIST_SHORTCUT = "iCloudBridge_Append_Checklist_To_Note"
    APPEND_CONTENT_SHORTCUT = "iCloudBridge_Append_Content_To_Note"

    def __init__(self, call_log: list[dict[str, str | None]] | None = None) -> None:
        self.call_log = call_log if call_log is not None else []

    async def upsert_note(self, folder: str, title: str) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._run_upsert, folder, title)

    async def append_checklist(self, folder: str, title: str, checklist_markdown: str) -> None:
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
        payload = f"{folder}\n{title}\n{markdown_block.rstrip()}\n"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._run_file_shortcut,
            self.APPEND_CONTENT_SHORTCUT,
            folder,
            title,
            payload,
        )

    def _run_upsert(self, folder: str, title: str) -> None:
        data = f"{folder};;{title}"
        cmd = ["shortcuts", "run", self.UPSERT_SHORTCUT]
        result = subprocess.run(
            cmd,
            input=data,
            text=True,
            capture_output=True,
            check=False,
        )
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
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp_file:
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
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
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
