"""Markdown folder adapter for note synchronization.

This adapter handles markdown files in a local/remote folder (e.g., NextCloud Notes).
It's designed to be extensible - other destination adapters (API-based, etc.) can
be added later by following the same interface pattern.
"""

import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import aiofiles
import aiofiles.os

from icloudbridge.utils.converters import (
    contains_markdown_checklist,
    extract_attachment_references,
    html_to_markdown,
    markdown_to_html,
    sanitize_filename,
)

logger = logging.getLogger(__name__)


@dataclass
class MarkdownNote:
    """Represents a note stored as a markdown file."""

    name: str
    created_date: datetime
    modified_date: datetime
    body_markdown: str
    file_path: Path
    attachments: list[str] = None  # List of attachment file paths

    def __post_init__(self):
        """Initialize attachments list if None."""
        if self.attachments is None:
            self.attachments = []


@dataclass
class PreparedAppleNote:
    name: str
    html_content: str
    markdown_body: str
    has_checklist: bool
    attachment_paths: dict[str, Path]


class MarkdownAdapter:
    """
    Adapter for reading/writing notes as markdown files.

    This is a FOLDER-BASED adapter - it works with any folder that can be synced
    (NextCloud, Dropbox, Google Drive, Syncthing, etc.).

    Design: This class focuses on FILE OPERATIONS only. It doesn't care about
    sync protocols, APIs, or network. This makes it:
    - Simple and testable
    - Easy to extend (add API-based adapters later)
    - Portable across different sync services

    Future adapters (NextCloudAPIAdapter, GoogleDriveAPIAdapter, etc.) can
    follow the same interface pattern.
    """

    def __init__(self, base_path: Path):
        """
        Initialize the markdown adapter.

        Args:
            base_path: Root folder for markdown notes (e.g., ~/NextCloud/Notes)
        """
        self.base_path = Path(base_path).expanduser().resolve()

    async def ensure_folder_exists(self, folder_path: Path | None = None) -> None:
        """
        Ensure a folder exists, create if it doesn't.

        Args:
            folder_path: Path to folder, or None for base_path
        """
        target = folder_path if folder_path else self.base_path
        target.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured folder exists: {target}")

    async def list_notes(self, folder_name: str | None = None) -> list[Path]:
        """
        List all markdown files in a folder.

        Args:
            folder_name: Subfolder name, or None for base folder

        Returns:
            List of paths to .md files
        """
        if folder_name:
            search_path = self.base_path / folder_name
        else:
            search_path = self.base_path

        if not search_path.exists():
            logger.debug(f"Folder does not exist: {search_path}")
            return []

        # Find all .md files (non-recursive for now)
        md_files = list(search_path.glob("*.md"))
        logger.info(f"Found {len(md_files)} markdown files in {search_path}")
        return md_files

    async def read_note(self, file_path: Path) -> MarkdownNote:
        """
        Read a markdown file and return a MarkdownNote.

        Args:
            file_path: Path to the markdown file

        Returns:
            MarkdownNote object

        Raises:
            FileNotFoundError: If file doesn't exist
            RuntimeError: If reading fails
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Markdown file not found: {file_path}")

        try:
            # Read file content
            async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                content = await f.read()

            # Get file metadata
            stat = await aiofiles.os.stat(file_path)
            created_date = datetime.fromtimestamp(stat.st_ctime)
            modified_date = datetime.fromtimestamp(stat.st_mtime)

            # Extract note name from filename
            note_name = file_path.stem  # Filename without .md extension

            # Extract attachment references
            attachments = extract_attachment_references(content)

            return MarkdownNote(
                name=note_name,
                created_date=created_date,
                modified_date=modified_date,
                body_markdown=content,
                file_path=file_path,
                attachments=attachments,
            )

        except Exception as e:
            logger.error(f"Failed to read markdown file {file_path}: {e}")
            raise RuntimeError(f"Failed to read note '{file_path.name}': {e}") from e

    async def write_note(
        self,
        note_name: str,
        body_html: str,
        folder_name: str | None = None,
        modified_date: datetime | None = None,
        attachments: dict[str, Path] | None = None,
    ) -> Path:
        """
        Write a note as a markdown file.

        This is the CORE method for syncing notes FROM Apple Notes TO markdown.

        Args:
            note_name: Name of the note (used as filename)
            body_html: HTML body from Apple Notes
            folder_name: Optional subfolder name
            modified_date: Optional modification date to set on file
            attachments: Optional dict mapping attachment refs to source paths
                        e.g., {'.attachments/uuid.png': Path('/path/to/image.png')}

        Returns:
            Path to the created/updated markdown file

        Raises:
            RuntimeError: If writing fails
        """
        try:
            # Determine target folder
            if folder_name:
                target_folder = self.base_path / folder_name
            else:
                target_folder = self.base_path

            await self.ensure_folder_exists(target_folder)

            # Sanitize filename
            safe_filename = sanitize_filename(note_name) + ".md"
            file_path = target_folder / safe_filename

            # Convert HTML to Markdown
            markdown_body = html_to_markdown(body_html, note_name)

            # Note: Title is already in the body from Apple Notes, don't duplicate it
            markdown_content = markdown_body

            # Handle attachments if provided
            attachment_paths = {}
            if attachments:
                attachments_folder = target_folder / ".attachments"
                await self.ensure_folder_exists(attachments_folder)

                for ref, source_path in attachments.items():
                    # Copy attachment to .attachments folder
                    dest_path = await self._copy_attachment(source_path, attachments_folder)
                    attachment_paths[ref] = dest_path

            # Write markdown file
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(markdown_content)

            # Set modification time if provided
            if modified_date:
                timestamp = modified_date.timestamp()
                os.utime(file_path, (timestamp, timestamp))

            logger.info(f"Wrote markdown file: {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to write markdown file for '{note_name}': {e}")
            raise RuntimeError(f"Failed to write note '{note_name}': {e}") from e

    async def update_note(
        self,
        file_path: Path,
        body_html: str,
        note_name: str | None = None,
        modified_date: datetime | None = None,
        attachments: dict[str, Path] | None = None,
    ) -> Path:
        """
        Update an existing markdown file.

        This is essentially the same as write_note but uses an existing file path.

        Args:
            file_path: Path to existing markdown file
            body_html: New HTML body from Apple Notes
            note_name: Optional new name (if renaming)
            modified_date: Optional modification date to set
            attachments: Optional dict mapping attachment refs to source paths

        Returns:
            Path to the updated file (may be different if renamed)

        Raises:
            RuntimeError: If update fails
        """
        try:
            # If renaming, use write_note to create new file and delete old
            if note_name and note_name != file_path.stem:
                folder_name = file_path.parent.name if file_path.parent != self.base_path else None
                new_path = await self.write_note(
                    note_name, body_html, folder_name, modified_date, attachments
                )
                # Delete old file
                await self.delete_note(file_path)
                return new_path

            # Otherwise, overwrite in place
            markdown_body = html_to_markdown(body_html, note_name or file_path.stem)
            # Note: Title is already in the body from Apple Notes, don't duplicate it
            markdown_content = markdown_body

            # Handle attachments
            if attachments:
                attachments_folder = file_path.parent / ".attachments"
                await self.ensure_folder_exists(attachments_folder)

                for _ref, source_path in attachments.items():
                    await self._copy_attachment(source_path, attachments_folder)

            # Write file
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(markdown_content)

            # Set modification time
            if modified_date:
                timestamp = modified_date.timestamp()
                os.utime(file_path, (timestamp, timestamp))

            logger.info(f"Updated markdown file: {file_path}")
            return file_path

        except Exception as e:
            logger.error(f"Failed to update markdown file {file_path}: {e}")
            raise RuntimeError(f"Failed to update note '{file_path.name}': {e}") from e

    async def delete_note(self, file_path: Path) -> bool:
        """
        Delete a markdown file.

        Args:
            file_path: Path to the markdown file to delete

        Returns:
            True if deletion succeeded

        Raises:
            RuntimeError: If deletion fails
        """
        try:
            if file_path.exists():
                await aiofiles.os.remove(file_path)
                logger.info(f"Deleted markdown file: {file_path}")
            else:
                logger.warning(f"File already deleted: {file_path}")

            return True

        except Exception as e:
            logger.error(f"Failed to delete markdown file {file_path}: {e}")
            raise RuntimeError(f"Failed to delete note '{file_path.name}': {e}") from e

    async def _copy_attachment(self, source_path: Path, dest_folder: Path) -> Path:
        """
        Copy an attachment file to the destination folder.

        Args:
            source_path: Path to source attachment file
            dest_folder: Destination folder (e.g., .attachments/)

        Returns:
            Path to the copied file

        Raises:
            RuntimeError: If copy fails
        """
        try:
            # Keep original filename
            dest_path = dest_folder / source_path.name

            # Copy file (synchronous for now, aiofiles doesn't have copy)
            shutil.copy2(source_path, dest_path)

            logger.debug(f"Copied attachment: {source_path.name}")
            return dest_path

        except Exception as e:
            logger.error(f"Failed to copy attachment {source_path}: {e}")
            raise RuntimeError(f"Failed to copy attachment '{source_path.name}': {e}") from e

    async def get_note_for_apple_notes(self, file_path: Path) -> PreparedAppleNote:
        """
        Read a markdown file and prepare it for Apple Notes.

        This converts markdown → HTML and provides attachment path mappings.

        Args:
            file_path: Path to markdown file

        Returns:
            PreparedAppleNote describing the converted HTML plus metadata

        Raises:
            RuntimeError: If reading/conversion fails
        """
        try:
            # Read markdown note
            note = await self.read_note(file_path)

            # Build attachment paths dict
            attachment_paths = {}
            if note.attachments:
                attachments_folder = file_path.parent / ".attachments"
                for ref in note.attachments:
                    # ref is like ".attachments/uuid.png"
                    attachment_file = attachments_folder / Path(ref).name
                    if attachment_file.exists():
                        attachment_paths[ref] = attachment_file

            has_checklist = contains_markdown_checklist(note.body_markdown)
            html_body = markdown_to_html(
                note.body_markdown,
                note.name,
                attachment_paths if attachment_paths else None,
            )

            return PreparedAppleNote(
                name=note.name,
                html_content=html_body,
                markdown_body=note.body_markdown,
                has_checklist=has_checklist,
                attachment_paths=attachment_paths,
            )

        except Exception as e:
            logger.error(f"Failed to prepare note for Apple Notes {file_path}: {e}")
            raise RuntimeError(f"Failed to read note '{file_path.name}': {e}") from e


# Future extension point: Other destination adapters
#
# class NextCloudAPIAdapter:
#     """API-based adapter for NextCloud Notes."""
#     async def list_notes(self) -> list[Note]: ...
#     async def write_note(self, note: Note) -> str: ...
#     async def update_note(self, note_id: str, note: Note) -> None: ...
#     async def delete_note(self, note_id: str) -> None: ...
#
# class GoogleDriveAdapter:
#     """Adapter for Google Drive storage."""
#     ...
#
# All adapters follow the same interface pattern, making it easy to
# swap destinations in the sync logic layer.
