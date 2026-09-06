"""Async AppleScript helpers for interacting with Apple Photos."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Default ceiling for a single `osascript` invocation. Photos can legitimately
# be slow (cloud originals are downloaded on demand), but without a bound a
# pathological query blocks the sync forever rather than failing the batch.
DEFAULT_SCRIPT_TIMEOUT = 1800.0

# Importing into Photos copies every file into the library, so a large album
# batch is legitimately slower than a query. Kept generous so the bound only
# catches a genuine hang, not a big-but-healthy import.
IMPORT_SCRIPT_TIMEOUT = 3600.0


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
        set importedItems to import fileList into targetAlbum

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


CHECK_ITEM_EXISTS_BY_NAME_SCRIPT = """
on run argv
    set targetName to item 1 of argv

    tell application "Photos"
        set matches to media items whose filename is targetName
    end tell

    if (count of matches) is 0 then
        return "0"
    end if

    return "1"
end run
"""


# Fetch ALL media-item filenames in one call (one library scan).
GET_ALL_FILENAMES_SCRIPT = """
tell application "Photos"
    set allNames to filename of media items
    set AppleScript's text item delimiters to linefeed
    return allNames as string
end tell
"""


# Export photos by filename to a destination folder.
# Photos.app handles downloading cloud-only originals automatically.
EXPORT_BY_FILENAMES_SCRIPT = """
on run argv
    set manifestPath to item 1 of argv
    set destFolder to item 2 of argv

    set fileContent to read POSIX file manifestPath
    set AppleScript's text item delimiters to linefeed
    set targetNames to text items of fileContent

    tell application "Photos"
        set exportItems to {}
        repeat with aName in targetNames
            if aName is not "" then
                try
                    set matches to (media items whose filename is aName)
                    repeat with aMatch in matches
                        set end of exportItems to contents of aMatch
                    end repeat
                end try
            end if
        end repeat

        if (count of exportItems) is 0 then
            return "0"
        end if

        export exportItems to POSIX file destFolder using originals true
        return (count of exportItems) as string
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

        result = await self._run_script(
            IMPORT_SCRIPT, str(manifest), album_name, timeout=IMPORT_SCRIPT_TIMEOUT
        )

        # Parse comma-separated list of identifiers
        if not result:
            return []

        return [identifier.strip() for identifier in result.split(",") if identifier.strip()]

    async def asset_exists_by_name(self, filename: str) -> bool:
        result = await self._run_script(CHECK_ITEM_EXISTS_BY_NAME_SCRIPT, filename)
        return result.strip() == "1"

    async def export_by_filenames(
        self,
        filenames: list[str],
        dest_folder: Path,
        timeout: float | None = DEFAULT_SCRIPT_TIMEOUT,
    ) -> int:
        """Export photos by filename to a destination folder.

        Photos.app automatically downloads cloud-only originals (e.g. from
        iCloud Shared Library) before exporting. This is the fallback for
        photos whose originals aren't available on disk.

        Each `whose filename is ...` lookup is a full library scan, so the
        manifest is deduplicated: a repeated filename returns the same media
        items anyway, and querying it twice only doubles the cost.

        Returns:
            Number of items exported.

        Raises:
            TimeoutError: if Photos exceeds `timeout`.
            RuntimeError: if the AppleScript itself fails.
        """
        if not filenames:
            return 0

        import tempfile

        dest_folder.mkdir(parents=True, exist_ok=True)

        # Deduplicate while preserving order.
        unique_names = list(dict.fromkeys(n for n in filenames if n))
        if len(unique_names) != len(filenames):
            logger.debug(
                "Cloud export manifest: %d requested -> %d unique filenames",
                len(filenames),
                len(unique_names),
            )

        # Write filenames to a manifest
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, prefix="icloudbridge_export_"
        ) as f:
            f.write("\n".join(unique_names))
            manifest_path = f.name

        try:
            result = await self._run_script(
                EXPORT_BY_FILENAMES_SCRIPT,
                manifest_path,
                str(dest_folder),
                timeout=timeout,
            )
            return int(result.strip()) if result.strip().isdigit() else 0
        finally:
            Path(manifest_path).unlink(missing_ok=True)

    async def batch_assets_exist_by_name(self, filenames: list[str]) -> dict[str, bool]:
        """Check multiple filenames at once via a single AppleScript call.

        Fetches ALL filenames from Photos library in one query, then matches
        in Python. Much faster than per-file ``whose`` filters.

        Returns a dict mapping each filename to whether it exists in Photos.
        """
        if not filenames:
            return {}

        # One library scan to get every filename
        result = await self._run_script(GET_ALL_FILENAMES_SCRIPT)
        library_names: set[str] = set()
        if result:
            library_names = {line.strip() for line in result.splitlines() if line.strip()}

        return {name: name in library_names for name in filenames}

    async def _run_script(
        self, script: str, *args: str, timeout: float | None = DEFAULT_SCRIPT_TIMEOUT
    ) -> str:
        """Execute an AppleScript snippet via `osascript`.

        Raises:
            TimeoutError: if the script exceeds `timeout` seconds. The
                `osascript` process is killed so it cannot outlive the sync.
            RuntimeError: if osascript exits non-zero.
        """
        cmd = ["osascript", "-"]
        cmd.extend(args)

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(script.encode("utf-8")), timeout=timeout
            )
        except asyncio.TimeoutError:
            # Photos may still be churning; kill it so the next batch can run.
            process.kill()
            try:
                await process.wait()
            except ProcessLookupError:
                pass
            raise TimeoutError(
                f"AppleScript timed out after {timeout:.0f}s"
            ) from None

        if process.returncode != 0:
            error = stderr.decode().strip()
            raise RuntimeError(f"AppleScript failed: {error}")

        return stdout.decode().strip()
