"""AppleScript adapter for interfacing with Apple Notes.app."""

import asyncio
import logging
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# AppleScript to list all note folders
LIST_FOLDERS_SCRIPT = """
tell application "Notes"
    set output to ""
    set n_folders to get every folder
    repeat with n_folder in n_folders
        set folder_id to id of n_folder
        set folder_name to name of n_folder
        if output is "" then
            set token to ""
        else
            set token to "|"
        end if
        set output to output & token & folder_id & "~~" & folder_name
    end repeat
    return output
end tell
"""

# AppleScript to get all notes from a folder (simplified - no staged files!)
GET_NOTES_SCRIPT = """
on run argv
    set folder_name to item 1 of argv
    tell application "Notes"
        set myFolder to first folder whose name = folder_name
        set myNotes to notes of myFolder
        set output to ""
        repeat with theNote in myNotes
            set nId to id of theNote
            set nName to name of theNote
            set nBody to body of theNote
            set nCreation to creation date of theNote
            set nModified to modification date of theNote

            -- Use delimiter that won't appear in note body
            set noteData to nId & "|||" & nName & "|||" & nCreation & "|||" & nModified & "|||" & nBody
            set output to output & noteData & "~~~NEXT_NOTE~~~"
        end repeat
        return output
    end tell
end run
"""

# AppleScript to create a new note
CREATE_NOTE_SCRIPT = """
on run argv
    set {note_folder, note_name, export_file} to {item 1, item 2, item 3} of argv

    -- Read the entire HTML content from file
    set html_content to read POSIX file export_file as «class utf8»

    tell application "Notes"
        tell folder note_folder
            set theNote to make new note
            tell theNote
                -- Set body directly (title already in content from markdown converter)
                set body to html_content
            end tell
        end tell
    end tell
    -- Return UUID and modification date separated by |||
    return (id of theNote) & "|||" & (modification date of theNote)
end run
"""

# AppleScript to update an existing note
UPDATE_NOTE_SCRIPT = """
on run argv
    set {note_folder, note_name, export_file} to {item 1, item 2, item 3} of argv

    -- Read the entire HTML content from file
    set html_content to read POSIX file export_file as «class utf8»

    tell application "Notes"
        tell folder note_folder
            set theNote to note note_name
            tell theNote
                -- Set body directly (title already in content from markdown converter)
                set body to html_content
            end tell
        end tell
    end tell
    return modification date of theNote
end run
"""

# AppleScript to delete a note
DELETE_NOTE_SCRIPT = """
on run argv
    set {note_folder, note_name} to {item 1, item 2} of argv
    tell application "Notes"
        tell folder note_folder
            set theNote to note note_name
            delete theNote
        end tell
    end tell
end run
"""

# Check if Notes app is running
IS_NOTES_RUNNING_SCRIPT = """
tell application "System Events"
    if (get name of every application process) contains "Notes" then
        return true
    else
        return false
    end if
end tell
"""


@dataclass
class AppleScriptNote:
    """Represents a note from Apple Notes.app."""

    uuid: str
    name: str
    created_date: datetime
    modified_date: datetime
    body_html: str
    folder_uuid: str | None = None


@dataclass
class AppleScriptFolder:
    """Represents a folder in Apple Notes.app."""

    uuid: str
    name: str
    note_count: int = 0


class NotesAdapter:
    """Adapter for interfacing with Apple Notes via AppleScript."""

    @staticmethod
    async def _run_applescript(script: str, *args: str) -> str:
        """
        Execute an AppleScript and return its output.

        Args:
            script: AppleScript code to execute
            *args: Arguments to pass to the script

        Returns:
            Script output (stdout)

        Raises:
            RuntimeError: If the script fails to execute
        """
        try:
            # Build osascript command
            cmd = ["osascript", "-e", script]
            cmd.extend(args)

            # Execute asynchronously
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode("utf-8").strip()
                logger.error(f"AppleScript failed: {error_msg}")
                raise RuntimeError(f"AppleScript execution failed: {error_msg}")

            return stdout.decode("utf-8").strip()

        except Exception as e:
            logger.error(f"Failed to run AppleScript: {e}")
            raise

    @staticmethod
    def _parse_apple_date(date_str: str) -> datetime:
        """
        Parse Apple's date format to datetime.

        Apple Notes returns dates in different formats depending on locale:
        - US format: "Monday, January 1, 2024 at 10:30:00 AM"
        - UK/EU format: "Monday, 18 February 2023 at 14:24:28"
        - Sometimes prefixed with "date ": "date Sunday, 2 November 2025 at 09:44:05"

        Args:
            date_str: Date string from AppleScript

        Returns:
            Parsed datetime object
        """
        try:
            # Remove "date " prefix if present
            date_str = re.sub(r"^date\s+", "", date_str, flags=re.IGNORECASE)

            # Remove day of week prefix (e.g., "Monday, ")
            date_str = re.sub(r"^[A-Za-z]+,\s+", "", date_str)

            # Try UK/EU format first: "18 February 2023 at 14:24:28"
            # (day month year, 24-hour time)
            try:
                return datetime.strptime(date_str, "%d %B %Y at %H:%M:%S")
            except ValueError:
                pass

            # Try US format: "January 1, 2024 at 10:30:00 AM"
            try:
                return datetime.strptime(date_str, "%B %d, %Y at %I:%M:%S %p")
            except ValueError:
                pass

            # Try alternative format without "at"
            try:
                return datetime.strptime(date_str, "%B %d, %Y %I:%M:%S %p")
            except ValueError:
                pass

            # Try UK/EU format without "at"
            try:
                return datetime.strptime(date_str, "%d %B %Y %H:%M:%S")
            except ValueError:
                pass

            logger.warning(f"Could not parse date: {date_str}, using current time")
            return datetime.now()

        except Exception as e:
            logger.warning(f"Error parsing date {date_str}: {e}, using current time")
            return datetime.now()

    async def is_notes_running(self) -> bool:
        """
        Check if Apple Notes.app is running.

        Returns:
            True if Notes is running, False otherwise
        """
        try:
            result = await self._run_applescript(IS_NOTES_RUNNING_SCRIPT)
            return result.lower() == "true"
        except Exception:
            return False

    async def ensure_notes_running(self) -> None:
        """
        Ensure Apple Notes.app is running, launch it if not.

        Raises:
            RuntimeError: If Notes cannot be launched
        """
        if not await self.is_notes_running():
            logger.info("Launching Apple Notes.app...")
            try:
                await self._run_applescript('tell application "Notes" to activate')
                # Give it a moment to launch
                await asyncio.sleep(1)
            except Exception as e:
                raise RuntimeError(f"Failed to launch Apple Notes: {e}") from e

    async def list_folders(self) -> list[AppleScriptFolder]:
        """
        Get all note folders from Apple Notes.

        Returns:
            List of AppleScriptFolder objects

        Raises:
            RuntimeError: If fetching folders fails
        """
        await self.ensure_notes_running()

        try:
            output = await self._run_applescript(LIST_FOLDERS_SCRIPT)

            if not output:
                return []

            folders = []
            # Split by | delimiter: "uuid~~name|uuid~~name|..."
            for folder_str in output.split("|"):
                if not folder_str.strip():
                    continue

                parts = folder_str.split("~~")
                if len(parts) == 2:
                    folder_uuid, folder_name = parts
                    folders.append(
                        AppleScriptFolder(
                            uuid=folder_uuid.strip(),
                            name=folder_name.strip(),
                        )
                    )

            logger.info(f"Found {len(folders)} note folders")
            return folders

        except Exception as e:
            logger.error(f"Failed to list folders: {e}")
            raise RuntimeError(f"Failed to list note folders: {e}") from e

    async def get_notes(self, folder_name: str) -> list[AppleScriptNote]:
        """
        Get all notes from a specific folder.

        This is the SIMPLIFIED version - no staged files!
        Parses AppleScript output directly.

        Args:
            folder_name: Name of the folder to fetch notes from

        Returns:
            List of AppleScriptNote objects

        Raises:
            RuntimeError: If fetching notes fails
        """
        await self.ensure_notes_running()

        try:
            output = await self._run_applescript(GET_NOTES_SCRIPT, folder_name)

            if not output or output == "~~~NEXT_NOTE~~~":
                logger.info(f"No notes found in folder: {folder_name}")
                return []

            notes = []
            # Split by note delimiter
            note_strings = output.split("~~~NEXT_NOTE~~~")

            for note_str in note_strings:
                if not note_str.strip():
                    continue

                # Parse: "uuid|||name|||created|||modified|||body"
                parts = note_str.split("|||", 4)  # Split into max 5 parts
                if len(parts) == 5:
                    uuid, name, created_str, modified_str, body_html = parts

                    notes.append(
                        AppleScriptNote(
                            uuid=uuid.strip(),
                            name=name.strip(),
                            created_date=self._parse_apple_date(created_str.strip()),
                            modified_date=self._parse_apple_date(modified_str.strip()),
                            body_html=body_html,  # Keep as-is, may have newlines
                        )
                    )

            logger.info(f"Found {len(notes)} notes in folder: {folder_name}")
            return notes

        except Exception as e:
            logger.error(f"Failed to get notes from {folder_name}: {e}")
            raise RuntimeError(f"Failed to get notes from folder '{folder_name}': {e}") from e

    async def create_note(
        self, folder_name: str, note_title: str, body_html: str
    ) -> tuple[str, datetime]:
        """
        Create a new note in Apple Notes.

        Args:
            folder_name: Folder to create the note in
            note_title: Title of the note
            body_html: HTML content for the note body

        Returns:
            Tuple of (note_uuid, modification_date)

        Raises:
            RuntimeError: If note creation fails
        """
        await self.ensure_notes_running()

        # Write HTML to temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as temp_file:
            temp_file.write(body_html)
            temp_path = temp_file.name

        try:
            # Run create script
            result = await self._run_applescript(
                CREATE_NOTE_SCRIPT, folder_name, note_title, temp_path
            )

            # Parse returned UUID and modification date (separated by |||)
            parts = result.split("|||")
            if len(parts) != 2:
                raise RuntimeError(f"Unexpected AppleScript return format: {result}")

            note_uuid = parts[0].strip()
            mod_date = self._parse_apple_date(parts[1].strip())

            logger.info(f"Created note: {note_title} in {folder_name} (UUID: {note_uuid})")
            return note_uuid, mod_date

        except Exception as e:
            logger.error(f"Failed to create note {note_title}: {e}")
            raise RuntimeError(f"Failed to create note '{note_title}': {e}") from e

        finally:
            # Clean up temp file
            Path(temp_path).unlink(missing_ok=True)

    async def update_note(
        self, folder_name: str, note_name: str, body_html: str
    ) -> datetime:
        """
        Update an existing note in Apple Notes.

        Args:
            folder_name: Folder containing the note
            note_name: Name of the note to update
            body_html: New HTML content for the note body

        Returns:
            Modification date of the updated note

        Raises:
            RuntimeError: If note update fails
        """
        await self.ensure_notes_running()

        # Write HTML to temporary file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".html", delete=False, encoding="utf-8"
        ) as temp_file:
            temp_file.write(body_html)
            temp_path = temp_file.name

        try:
            # Run update script
            result = await self._run_applescript(
                UPDATE_NOTE_SCRIPT, folder_name, note_name, temp_path
            )

            # Parse returned modification date
            mod_date = self._parse_apple_date(result)
            logger.info(f"Updated note: {note_name} in {folder_name}")
            return mod_date

        except Exception as e:
            logger.error(f"Failed to update note {note_name}: {e}")
            raise RuntimeError(f"Failed to update note '{note_name}': {e}") from e

        finally:
            # Clean up temp file
            Path(temp_path).unlink(missing_ok=True)

    async def delete_note(self, folder_name: str, note_name: str) -> bool:
        """
        Delete a note from Apple Notes.

        Args:
            folder_name: Folder containing the note
            note_name: Name of the note to delete

        Returns:
            True if deletion succeeded

        Raises:
            RuntimeError: If note deletion fails
        """
        await self.ensure_notes_running()

        try:
            await self._run_applescript(DELETE_NOTE_SCRIPT, folder_name, note_name)
            logger.info(f"Deleted note: {note_name} from {folder_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete note {note_name}: {e}")
            raise RuntimeError(f"Failed to delete note '{note_name}': {e}") from e
