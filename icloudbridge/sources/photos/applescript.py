"""Async AppleScript helpers for interacting with Apple Photos."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


LIST_ALBUMS_SCRIPT = """
tell application "Photos"
    set albumList to name of albums
    return albumList as string
end tell
"""


ALBUM_EXISTS_SCRIPT = """
on run argv
    set albumName to item 1 of argv
    tell application "Photos"
        try
            set _ to first album whose name is albumName
            return "1"
        on error
            return "0"
        end try
    end tell
end run
"""


CREATE_ALBUM_SCRIPT = """
on run argv
    set albumName to item 1 of argv
    tell application "Photos"
        make new album named albumName
    end tell
end run
"""


IMPORT_SCRIPT = """
on run argv
    set importFilePath to item 1 of argv
    set albumName to item 2 of argv

    set fileContent to read POSIX file importFilePath
    set AppleScript's text item delimiters to linefeed
    set fileLines to text items of fileContent
    set fileList to {}
    repeat with aLine in fileLines
        if aLine is not "" then
            set end of fileList to POSIX file aLine
        end if
    end repeat

    if fileList is {} then
        return ""
    end if

    tell application "Photos"
        set targetAlbum to album albumName
        set importedItems to import fileList into targetAlbum with skip check duplicates

        -- Extract local identifiers from imported items
        set idList to {}
        repeat with mediaItem in importedItems
            set end of idList to id of mediaItem
        end repeat

        -- Return comma-separated list of local identifiers
        set AppleScript's text item delimiters to ","
        return idList as string
    end tell
end run
"""


class PhotosAppleScriptAdapter:
    """Thin async wrapper over `osascript` for Photos operations."""

    async def ensure_album(self, album_name: str) -> None:
        """Ensure an album with the given name exists."""
        if not album_name:
            raise ValueError("Album name is required")

        exists_result = (await self._run_script(ALBUM_EXISTS_SCRIPT, album_name)).strip()
        if exists_result == "1":
            return

        await self._run_script(CREATE_ALBUM_SCRIPT, album_name)

    async def import_files(self, manifest: Path, album_name: str) -> list[str]:
        """Import the files listed in `manifest` into the target album.

        Returns:
            List of Apple Photos local identifiers for the imported items
        """
        if not manifest.exists():
            raise FileNotFoundError(f"Manifest not found: {manifest}")

        result = await self._run_script(IMPORT_SCRIPT, str(manifest), album_name)

        # Parse comma-separated list of identifiers
        if not result:
            return []

        return [identifier.strip() for identifier in result.split(",") if identifier.strip()]

    async def _run_script(self, script: str, *args: str) -> str:
        """Execute an AppleScript snippet via `osascript`."""
        cmd = ["osascript", "-"]
        cmd.extend(args)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate(script.encode("utf-8"))

        if process.returncode != 0:
            error = stderr.decode().strip()
            raise RuntimeError(f"AppleScript failed: {error}")

        return stdout.decode().strip()
