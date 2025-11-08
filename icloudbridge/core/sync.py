"""Core synchronization logic for Apple Notes ↔ Markdown."""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from icloudbridge.sources.notes.applescript import AppleScriptNote, NotesAdapter
from icloudbridge.sources.notes.markdown import MarkdownAdapter, MarkdownNote
from icloudbridge.sources.notes.shortcuts import NotesShortcutAdapter
from icloudbridge.utils.converters import split_markdown_segments, strip_leading_heading
from icloudbridge.utils.db import NotesDB

logger = logging.getLogger(__name__)


class NotesSyncEngine:
    """
    Orchestrates bidirectional synchronization between Apple Notes and Markdown.

    This is the CORE SYNC ENGINE that brings together:
    - AppleScript adapter (source: Apple Notes.app)
    - Markdown adapter (destination: markdown files)
    - Database (state tracking)

    Design Philosophy:
    - Single-pass bidirectional sync (no multi-phase complexity)
    - Last-write-wins conflict resolution (simpler than manual resolution)
    - Database tracks: local UUID ↔ remote path mappings
    - Timestamp-based change detection

    Sync Algorithm:
    1. Fetch all notes from Apple Notes (by folder)
    2. Fetch all markdown files from destination
    3. Build sync plan based on timestamps and database mappings
    4. Execute sync operations (create/update/delete on both sides)
    5. Update database with new mappings

    This is ~70% SIMPLER than TaskBridge's multi-phase approach!
    """

    def __init__(
        self,
        markdown_base_path: Path,
        db_path: Path,
        prefer_shortcuts: bool = True,
    ):
        """
        Initialize the sync engine.

        Args:
            markdown_base_path: Root path for markdown files
            db_path: Path to SQLite database for state tracking
        """
        self.notes_adapter = NotesAdapter()
        self.markdown_adapter = MarkdownAdapter(markdown_base_path)
        self.shortcut_calls: list[dict[str, str | None]] = []
        self.shortcuts = NotesShortcutAdapter(self.shortcut_calls)
        self.use_shortcut_pipeline = prefer_shortcuts
        self.db = NotesDB(db_path)

    async def initialize(self) -> None:
        """
        Initialize the sync engine.

        Sets up database schema and ensures required folders exist.
        """
        await self.db.initialize()
        await self.markdown_adapter.ensure_folder_exists()
        logger.info("Sync engine initialized")

    async def migrate_root_notes_to_folder(self) -> int:
        """
        Automatically migrate root-level markdown notes to the "Notes" folder.

        This handles the case where NextCloud or other services allow notes
        in the root folder, but Apple Notes requires all notes to be in folders.

        Returns:
            Number of notes migrated

        Raises:
            RuntimeError: If migration fails
        """
        try:
            # List notes in base folder (root level)
            root_notes = await self.markdown_adapter.list_notes(folder_name=None)

            if not root_notes:
                logger.debug("No root-level notes found, skipping migration")
                return 0

            logger.info(f"Found {len(root_notes)} note(s) in root folder, migrating to 'Notes' folder...")

            # Ensure "Notes" subfolder exists
            notes_folder = self.markdown_adapter.base_path / "Notes"
            await self.markdown_adapter.ensure_folder_exists(notes_folder)

            # Move each root note to "Notes" subfolder
            migrated_count = 0
            for note_path in root_notes:
                try:
                    # Construct destination path
                    dest_path = notes_folder / note_path.name

                    # Move the file
                    note_path.rename(dest_path)
                    logger.info(f"Migrated '{note_path.name}' to Notes folder")

                    # Update database mapping if exists
                    mapping = await self.db.get_mapping_by_remote_path(str(note_path))
                    if mapping:
                        await self.db.update_mapping(
                            mapping["local_uuid"],
                            str(dest_path),
                            mapping["last_synced"],
                        )
                        logger.debug(f"Updated database mapping for '{note_path.name}'")

                    migrated_count += 1

                except Exception as e:
                    logger.warning(f"Failed to migrate '{note_path.name}': {e}")
                    continue

            if migrated_count > 0:
                logger.info(f"Successfully migrated {migrated_count} note(s) to 'Notes' folder")

            return migrated_count

        except Exception as e:
            logger.error(f"Failed to migrate root notes: {e}")
            raise RuntimeError(f"Failed to migrate root notes: {e}") from e

    async def sync_folder(
        self,
        folder_name: str,
        markdown_subfolder: str | None = None,
        dry_run: bool = False,
        skip_deletions: bool = False,
        deletion_threshold: int = 5,
    ) -> dict[str, int]:
        """
        Synchronize a single Apple Notes folder with markdown files.

        This is the MAIN SYNC METHOD that implements the bidirectional algorithm.

        Args:
            folder_name: Name of the Apple Notes folder to sync
            markdown_subfolder: Optional subfolder in markdown destination
                               (if None, uses base folder)
            dry_run: If True, preview changes without applying them
            skip_deletions: If True, skip all deletion operations
            deletion_threshold: Prompt user if deletions exceed this count
                               (-1 to disable threshold, default: 5)

        Returns:
            Dictionary with sync statistics:
            - created_local: Notes created in Apple Notes
            - created_remote: Markdown files created
            - updated_local: Notes updated in Apple Notes
            - updated_remote: Markdown files updated
            - deleted_local: Notes deleted from Apple Notes
            - deleted_remote: Markdown files deleted
            - unchanged: Notes that didn't need sync
            - would_delete_local: (dry_run only) Notes that would be deleted
            - would_delete_remote: (dry_run only) Markdown files that would be deleted

        Raises:
            RuntimeError: If sync fails
        """
        logger.info(f"Starting sync for folder: {folder_name}" + (" (DRY RUN)" if dry_run else ""))
        stats = {
            "created_local": 0,
            "created_remote": 0,
            "updated_local": 0,
            "updated_remote": 0,
            "deleted_local": 0,
            "deleted_remote": 0,
            "unchanged": 0,
            "would_delete_local": 0,  # For dry_run
            "would_delete_remote": 0,  # For dry_run
        }

        try:
            if self.notes_adapter.is_ignored_folder(folder_name):
                raise RuntimeError(f"Folder '{folder_name}' cannot be synced (ignored by design)")

            # Step 1: Fetch all notes from Apple Notes
            logger.debug(f"Fetching notes from Apple Notes folder: {folder_name}")
            await self.notes_adapter.refresh_rich_cache()
            apple_notes = await self.notes_adapter.get_notes(folder_name)
            logger.info(f"Found {len(apple_notes)} notes in Apple Notes")

            # Step 2: Fetch all markdown files from destination
            logger.debug(f"Fetching markdown files from: {markdown_subfolder or 'base folder'}")
            markdown_files = await self.markdown_adapter.list_notes(markdown_subfolder)
            logger.info(f"Found {len(markdown_files)} markdown files")

            # Step 3: Build mappings and determine sync operations
            # Map: local UUID → AppleScriptNote
            local_notes_by_uuid = {note.uuid: note for note in apple_notes}

            # Map: remote path → MarkdownNote
            remote_notes_by_path = {}
            for md_file in markdown_files:
                try:
                    md_note = await self.markdown_adapter.read_note(md_file)
                    remote_notes_by_path[str(md_file)] = md_note
                except Exception as e:
                    logger.warning(f"Failed to read markdown file {md_file}: {e}")
                    continue

            # Get database mappings for this folder
            # Note: We need folder UUID, but AppleScript doesn't return it in notes
            # For now, we'll query all mappings and filter by note UUIDs
            all_mappings = await self.db.get_all_mappings()
            mappings_by_uuid = {m["local_uuid"]: m for m in all_mappings}

            # Step 4: Check deletion threshold (if not disabled and not skipping deletions)
            if deletion_threshold > 0 and not skip_deletions and not dry_run:
                # Pre-scan to count potential deletions
                deletion_count = 0

                # Count notes that would be deleted (remote file missing)
                for uuid in local_notes_by_uuid:
                    mapping = mappings_by_uuid.get(uuid)
                    if mapping:
                        remote_path_str = str(mapping["remote_path"])
                        if remote_path_str not in remote_notes_by_path:
                            deletion_count += 1

                # Count markdown files that would be deleted (local note missing)
                for remote_path_str in remote_notes_by_path:
                    mapping = await self.db.get_mapping_by_remote_path(remote_path_str)
                    if mapping and mapping["local_uuid"] not in local_notes_by_uuid:
                        deletion_count += 1

                # If threshold exceeded, prompt user
                if deletion_count > deletion_threshold:
                    logger.warning(
                        f"About to delete {deletion_count} notes (threshold: {deletion_threshold})"
                    )
                    # Note: In CLI mode, this will be handled by the CLI layer
                    # For now, we'll raise an exception that the CLI can catch
                    raise RuntimeError(
                        f"Deletion threshold exceeded: {deletion_count} deletions pending "
                        f"(threshold: {deletion_threshold}). Use --deletion-threshold -1 to disable."
                    )

            # Step 5: Determine what needs to be synced
            # Track which notes/files we've processed
            processed_local_uuids = set()
            processed_remote_paths = set()

            # 5a. Process notes that exist in Apple Notes
            for uuid, apple_note in local_notes_by_uuid.items():
                mapping = mappings_by_uuid.get(uuid)

                if mapping:
                    # Note is already mapped - check if it needs updating
                    remote_path = Path(mapping["remote_path"])
                    processed_local_uuids.add(uuid)
                    processed_remote_paths.add(str(remote_path))

                    # Check if remote file still exists
                    if str(remote_path) not in remote_notes_by_path:
                        # Remote file was deleted - delete from Apple Notes
                        if skip_deletions:
                            logger.info(f"Remote file deleted, skipping deletion (--skip-deletions): {apple_note.name}")
                            continue

                        if dry_run:
                            logger.info(f"[DRY RUN] Would delete note: {apple_note.name}")
                            stats["would_delete_local"] += 1
                        else:
                            logger.info(f"Remote file deleted, deleting note: {apple_note.name}")
                            await self.notes_adapter.delete_note(folder_name, apple_note.name)
                            await self.db.delete_mapping(uuid)
                            stats["deleted_local"] += 1
                        continue

                    # Both exist - check which is newer
                    md_note = remote_notes_by_path[str(remote_path)]
                    last_sync = datetime.fromtimestamp(mapping["last_sync_timestamp"])

                    # Determine sync direction based on modification times
                    local_modified = apple_note.modified_date
                    remote_modified = md_note.modified_date

                    if local_modified > last_sync and remote_modified > last_sync:
                        # Both changed since last sync - use last-write-wins
                        if local_modified > remote_modified:
                            # Local is newer - push to remote
                            if dry_run:
                                logger.info(f"[DRY RUN] Would update remote (conflict, local wins): {apple_note.name}")
                            else:
                                logger.info(
                                    f"Conflict (local wins): {apple_note.name} "
                                    f"(local: {local_modified}, remote: {remote_modified})"
                                )
                                await self._push_to_remote(
                                    apple_note, remote_path, markdown_subfolder
                                )
                            stats["updated_remote"] += 1
                        else:
                            # Remote is newer - pull from remote
                            if dry_run:
                                logger.info(f"[DRY RUN] Would update local (conflict, remote wins): {apple_note.name}")
                            else:
                                logger.info(
                                    f"Conflict (remote wins): {apple_note.name} "
                                    f"(local: {local_modified}, remote: {remote_modified})"
                                )
                                new_uuid = await self._pull_from_remote(
                                    md_note,
                                    folder_name,
                                    apple_note.name,
                                    uuid,
                                )
                                if new_uuid:
                                    uuid = new_uuid
                            stats["updated_local"] += 1

                        # Update mapping with current timestamp
                        if not dry_run:
                            await self.db.upsert_mapping(
                                local_uuid=uuid,
                                local_name=apple_note.name,
                                local_folder_uuid="",  # We don't track folder UUID yet
                                remote_path=remote_path,
                                timestamp=datetime.now().timestamp(),
                            )

                    elif local_modified > last_sync:
                        # Only local changed - push to remote
                        if dry_run:
                            logger.info(f"[DRY RUN] Would update remote: {apple_note.name}")
                        else:
                            logger.info(f"Local changed: {apple_note.name}")
                            await self._push_to_remote(apple_note, remote_path, markdown_subfolder)
                            await self.db.upsert_mapping(
                                local_uuid=uuid,
                                local_name=apple_note.name,
                                local_folder_uuid="",
                                remote_path=remote_path,
                                timestamp=datetime.now().timestamp(),
                            )
                        stats["updated_remote"] += 1

                    elif remote_modified > last_sync:
                        # Only remote changed - pull from remote
                        if dry_run:
                            logger.info(f"[DRY RUN] Would update local: {apple_note.name}")
                        else:
                            logger.info(f"Remote changed: {apple_note.name}")
                            new_uuid = await self._pull_from_remote(
                                md_note,
                                folder_name,
                                apple_note.name,
                                uuid,
                            )
                            if new_uuid:
                                uuid = new_uuid
                            await self.db.upsert_mapping(
                                local_uuid=uuid,
                                local_name=apple_note.name,
                                local_folder_uuid="",
                                remote_path=remote_path,
                                timestamp=datetime.now().timestamp(),
                            )
                        stats["updated_local"] += 1

                    else:
                        # Neither changed - no sync needed
                        logger.debug(f"Unchanged: {apple_note.name}")
                        stats["unchanged"] += 1

                else:
                    # Note is not mapped - it's new, create on remote
                    if dry_run:
                        logger.info(f"[DRY RUN] Would create remote: {apple_note.name}")
                        # Create fake path for dry run tracking
                        remote_path = Path(f"{apple_note.name}.md")
                    else:
                        logger.info(f"New local note: {apple_note.name}")
                        remote_path = await self._push_to_remote(
                            apple_note, None, markdown_subfolder
                        )
                        await self.db.upsert_mapping(
                            local_uuid=uuid,
                            local_name=apple_note.name,
                            local_folder_uuid="",
                            remote_path=remote_path,
                            timestamp=datetime.now().timestamp(),
                        )
                    processed_local_uuids.add(uuid)
                    processed_remote_paths.add(str(remote_path))
                    stats["created_remote"] += 1

            # 4b. Process markdown files that don't have local notes
            for remote_path_str, md_note in remote_notes_by_path.items():
                if remote_path_str in processed_remote_paths:
                    continue  # Already processed

                # Check if there's a mapping for this remote file
                mapping = await self.db.get_mapping_by_remote_path(remote_path_str)

                if mapping:
                    # Had a mapping but local note is gone - delete remote
                    if skip_deletions:
                        logger.info(f"Local note deleted, skipping deletion (--skip-deletions): {md_note.name}")
                    elif dry_run:
                        logger.info(f"[DRY RUN] Would delete markdown: {md_note.name}")
                        stats["would_delete_remote"] += 1
                    else:
                        logger.info(f"Local note deleted, deleting markdown: {md_note.name}")
                        await self.markdown_adapter.delete_note(Path(remote_path_str))
                        await self.db.delete_mapping_by_remote_path(remote_path_str)
                        stats["deleted_remote"] += 1
                else:
                    # New remote file - create in Apple Notes
                    if dry_run:
                        logger.info(f"[DRY RUN] Would create local: {md_note.name}")
                    else:
                        logger.info(f"New remote note: {md_note.name}")
                        note_uuid = await self._pull_from_remote(md_note, folder_name, None)
                        if note_uuid:
                            await self.db.upsert_mapping(
                                local_uuid=note_uuid,
                                local_name=md_note.name,
                                local_folder_uuid="",
                                remote_path=Path(remote_path_str),
                                timestamp=datetime.now().timestamp(),
                            )
                    stats["created_local"] += 1

            logger.info(
                f"Sync complete for {folder_name}: "
                f"{stats['created_local']} local created, "
                f"{stats['created_remote']} remote created, "
                f"{stats['updated_local']} local updated, "
                f"{stats['updated_remote']} remote updated, "
                f"{stats['deleted_local']} local deleted, "
                f"{stats['deleted_remote']} remote deleted, "
                f"{stats['unchanged']} unchanged"
            )

            return stats

        except Exception as e:
            logger.error(f"Sync failed for folder {folder_name}: {e}")
            raise RuntimeError(f"Sync failed for folder '{folder_name}': {e}") from e

    async def _push_to_remote(
        self,
        apple_note: AppleScriptNote,
        existing_path: Path | None,
        markdown_subfolder: str | None,
    ) -> Path:
        """
        Push an Apple Note to markdown (create or update).

        Args:
            apple_note: The Apple Note to push
            existing_path: Existing markdown file path (if updating), None if creating
            markdown_subfolder: Subfolder for markdown files

        Returns:
            Path to the created/updated markdown file
        """
        if existing_path:
            # Update existing file
            return await self.markdown_adapter.update_note(
                file_path=existing_path,
                body_html=apple_note.body_html,
                note_name=apple_note.name,
                modified_date=apple_note.modified_date,
                attachments=None,  # TODO: Handle attachments in future
            )
        else:
            # Create new file
            return await self.markdown_adapter.write_note(
                note_name=apple_note.name,
                body_html=apple_note.body_html,
                folder_name=markdown_subfolder,
                modified_date=apple_note.modified_date,
                attachments=None,  # TODO: Handle attachments in future
            )

    async def _pull_from_remote(
        self,
        md_note: MarkdownNote,
        folder_name: str,
        existing_note_name: str | None,
        existing_note_uuid: str | None = None,
    ) -> str | None:
        """
        Pull a markdown note to Apple Notes (create or update).

        Args:
            md_note: The markdown note to pull
            folder_name: Apple Notes folder name
            existing_note_name: Existing note name (if updating), None if creating

        Returns:
            UUID of the note (for new notes)
            For updates, returns None since we already know the UUID
        """
        prepared_note = await self.markdown_adapter.get_note_for_apple_notes(md_note.file_path)

        use_shortcuts = prepared_note.has_checklist or self.use_shortcut_pipeline

        if use_shortcuts:
            logger.debug(
                "Using Shortcut pipeline for note '%s' in folder '%s'",
                md_note.name,
                folder_name,
            )
            await self.shortcuts.upsert_note(folder_name, md_note.name)
            self.notes_adapter.clear_rich_cache()

            new_uuid = None
            for attempt in range(3):
                if attempt:
                    await asyncio.sleep(0.5)
                    self.notes_adapter.clear_rich_cache()
                new_uuid = await self.notes_adapter.get_note_uuid(folder_name, md_note.name)
                if new_uuid:
                    break
            if not new_uuid:
                raise RuntimeError(
                    f"Unable to locate note '{md_note.name}' in folder '{folder_name}' after shortcut upsert"
                )

            markdown_body = strip_leading_heading(prepared_note.markdown_body, md_note.name)
            segments = split_markdown_segments(markdown_body)
            if not segments:
                await self.shortcuts.append_content(folder_name, md_note.name, markdown_body)
            else:
                for segment_type, block in segments:
                    if segment_type == "checklist":
                        await self.shortcuts.append_checklist(folder_name, md_note.name, block)
                    else:
                        await self.shortcuts.append_content(folder_name, md_note.name, block)

            return new_uuid

        if existing_note_name:
            await self.notes_adapter.update_note(
                folder_name=folder_name,
                note_name=existing_note_name,
                body_html=prepared_note.html_content,
            )
            return existing_note_uuid

        note_uuid, _mod_date = await self.notes_adapter.create_note(
            folder_name=folder_name,
            note_title=prepared_note.name,
            body_html=prepared_note.html_content,
        )
        return note_uuid

    async def list_folders(self) -> list[dict]:
        """
        List all folders from Apple Notes.

        Returns:
            List of dictionaries with folder information:
            - uuid: Folder UUID
            - name: Folder name
            - note_count: Number of notes (0 for now, can be populated later)
        """
        folders = await self.notes_adapter.list_folders()
        return [{"uuid": f.uuid, "name": f.name, "note_count": f.note_count} for f in folders]

    async def get_sync_status(self, folder_name: str | None = None) -> dict:
        """
        Get sync status for all folders or a specific folder.

        Args:
            folder_name: Optional folder name to get status for

        Returns:
            Dictionary with sync status information:
            - total_mappings: Total number of synced notes
            - folder_breakdown: List of folders with note counts
        """
        all_mappings = await self.db.get_all_mappings()

        if folder_name:
            # Filter mappings for specific folder
            # Note: We don't currently track folder names in DB, only UUIDs
            # This is a limitation we'll address later
            return {
                "total_mappings": len(all_mappings),
                "folder_name": folder_name,
                "note": "Folder-specific status not yet implemented",
            }

        # Return overall status
        return {
            "total_mappings": len(all_mappings),
            "folders": [],  # TODO: Break down by folder
        }

    async def reset_database(self) -> None:
        """
        Reset the sync database by clearing all note mappings.

        This does NOT delete any notes - it only clears the sync tracking database.
        After reset, the next sync will treat all notes as "new".

        Raises:
            RuntimeError: If reset fails
        """
        try:
            logger.warning("Resetting sync database - all mappings will be cleared")
            await self.db.clear_all_mappings()
            logger.info("Database reset complete")
        except Exception as e:
            logger.error(f"Failed to reset database: {e}")
            raise RuntimeError(f"Failed to reset database: {e}") from e

    async def cleanup_orphaned_mappings(self) -> int:
        """
        Clean up database mappings for notes that no longer exist.

        Returns:
            Number of orphaned mappings removed
        """
        # Get all current local UUIDs and remote paths
        # This is expensive but necessary for cleanup
        logger.info("Scanning for orphaned mappings...")

        # For now, return 0 - full implementation requires scanning all folders
        # This is a maintenance operation that can be run periodically
        logger.warning("Cleanup not yet fully implemented")
        return 0
