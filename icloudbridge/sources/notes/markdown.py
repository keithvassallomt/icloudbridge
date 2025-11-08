"""Markdown folder adapter for note synchronization.

This adapter handles markdown files in a local/remote folder (e.g., NextCloud Notes).
It's designed to be extensible - other destination adapters (API-based, etc.) can
be added later by following the same interface pattern.
"""

import base64
import json
import logging
import mimetypes
import os
import shutil
from dataclasses import dataclass, field
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

_METADATA_PREFIX = "<!-- icloudbridge-metadata "
_METADATA_SUFFIX = "-->"


@dataclass
class MarkdownNote:
    """Represents a note stored as a markdown file."""

    name: str
    created_date: datetime
    modified_date: datetime
    body_markdown: str
    file_path: Path
    attachments: list[str] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class PreparedAppleNote:
    name: str
    html_content: str
    markdown_body: str
    markdown_with_inline_attachments: str
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

            metadata, body = self._split_metadata(content)

            # Get file metadata
            stat = await aiofiles.os.stat(file_path)
            created_date = datetime.fromtimestamp(stat.st_ctime)
            modified_date = datetime.fromtimestamp(stat.st_mtime)

            # Extract note name from filename
            note_name = file_path.stem  # Filename without .md extension

            # Extract attachment references
            attachments = extract_attachment_references(body)

            return MarkdownNote(
                name=note_name,
                created_date=created_date,
                modified_date=modified_date,
                body_markdown=body,
                file_path=file_path,
                attachments=attachments,
                metadata=metadata,
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
        metadata: dict[str, str] | None = None,
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
                        e.g., {'.attachments.slug/image.png': Path('/tmp/image.png')}
            metadata: Optional metadata to embed (e.g., attachment slug)

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
            markdown_content = self._apply_metadata(metadata or {}, markdown_body)

            if attachments:
                self._sync_attachments(target_folder, attachments)
            elif metadata and metadata.get("attachment_slug"):
                self._purge_attachment_folder(target_folder, metadata["attachment_slug"])

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
        metadata: dict[str, str] | None = None,
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
                    note_name,
                    body_html,
                    folder_name,
                    modified_date,
                    attachments,
                    metadata,
                )
                # Delete old file
                await self.delete_note(file_path)
                return new_path

            # Otherwise, overwrite in place
            markdown_body = html_to_markdown(body_html, note_name or file_path.stem)
            markdown_content = self._apply_metadata(metadata or {}, markdown_body)

            if attachments:
                self._sync_attachments(file_path.parent, attachments)
            elif metadata and metadata.get("attachment_slug"):
                self._purge_attachment_folder(file_path.parent, metadata["attachment_slug"])

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

    def _sync_attachments(
        self,
        base_folder: Path,
        attachments: dict[str, Path],
    ) -> None:
        if not attachments:
            return

        preserved: set[Path] = set()
        folders: set[Path] = set()
        base_folder = base_folder.resolve()

        for relative_ref, source_path in attachments.items():
            if not source_path or not source_path.exists():
                logger.debug("Skipping missing attachment source %s", source_path)
                continue

            dest_path = self._resolve_attachment_destination(base_folder, relative_ref)
            dest_path.parent.mkdir(parents=True, exist_ok=True)

            shutil.copy2(source_path, dest_path)
            preserved.add(dest_path.resolve())
            folders.add(dest_path.parent)
            logger.debug("Copied attachment %s -> %s", source_path, dest_path)

        for folder in folders:
            if not folder.exists():
                continue
            for existing in folder.iterdir():
                if existing.is_file() and existing.resolve() not in preserved:
                    existing.unlink(missing_ok=True)
                    logger.debug("Removed stale attachment %s", existing)

    def _purge_attachment_folder(self, base_folder: Path, slug: str) -> None:
        if not slug:
            return
        folder = (base_folder.resolve()) / f".attachments.{slug}"
        if folder.exists() and folder.is_dir():
            shutil.rmtree(folder)
            logger.debug("Removed attachment folder %s", folder)

    def _resolve_attachment_destination(self, base_folder: Path, relative_ref: str) -> Path:
        relative = Path(relative_ref.lstrip("/\\"))
        dest_path = (base_folder / relative).resolve()
        if not dest_path.is_relative_to(base_folder):
            raise RuntimeError(f"Attachment path {relative_ref} escapes target folder {base_folder}")
        return dest_path

    def _split_metadata(self, content: str) -> tuple[dict[str, str], str]:
        working = content.lstrip("\ufeff")
        if not working.startswith(_METADATA_PREFIX):
            return {}, content

        end_idx = working.find(_METADATA_SUFFIX)
        if end_idx == -1:
            return {}, content

        raw = working[len(_METADATA_PREFIX) : end_idx].strip()
        remainder = working[end_idx + len(_METADATA_SUFFIX) :].lstrip("\n")
        try:
            metadata = json.loads(raw) if raw else {}
            if not isinstance(metadata, dict):
                metadata = {}
        except json.JSONDecodeError:
            metadata = {}
        return metadata, remainder

    def _apply_metadata(self, metadata: dict[str, str], body: str) -> str:
        if not metadata:
            return body

        payload = json.dumps(metadata, separators=(",", ":"), sort_keys=True)
        header = f"{_METADATA_PREFIX}{payload} {_METADATA_SUFFIX}\n\n"
        return header + body.lstrip("\n")

    def _inline_markdown_attachments(self, markdown: str, attachment_paths: dict[str, Path]) -> str:
        if not attachment_paths:
            return markdown

        inlined = markdown
        for ref, file_path in attachment_paths.items():
            if not file_path.exists():
                continue
            mime, _ = mimetypes.guess_type(str(file_path))
            if not mime:
                mime = "application/octet-stream"
            encoded = base64.b64encode(file_path.read_bytes()).decode("ascii")
            data_uri = f"data:{mime};base64,{encoded}"
            inlined = inlined.replace(ref, data_uri)
        return inlined

    async def get_attachment_slug(self, file_path: Path) -> str | None:
        try:
            note = await self.read_note(file_path)
        except FileNotFoundError:
            return None
        return note.metadata.get("attachment_slug")

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
                for ref in note.attachments:
                    relative = Path(ref.lstrip("/\\"))
                    attachment_file = (file_path.parent / relative)
                    if attachment_file.exists():
                        attachment_paths[ref] = attachment_file.resolve()

            has_checklist = contains_markdown_checklist(note.body_markdown)
            html_body = markdown_to_html(
                note.body_markdown,
                note.name,
                attachment_paths if attachment_paths else None,
            )

            inlined_markdown = self._inline_markdown_attachments(note.body_markdown, attachment_paths)

            return PreparedAppleNote(
                name=note.name,
                html_content=html_body,
                markdown_body=note.body_markdown,
                markdown_with_inline_attachments=inlined_markdown,
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
