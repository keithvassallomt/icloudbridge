"""Dependency injection for FastAPI endpoints.

This module provides reusable dependencies for:
- Configuration loading
- Sync engine initialization
- Database connections
- Authentication (when enabled)
"""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from icloudbridge.core.config import AppConfig, load_config
from icloudbridge.core.passwords_sync import PasswordsSyncEngine
from icloudbridge.core.photos_export_engine import ExportConfig, PhotoExportEngine
from icloudbridge.core.photos_sync import PhotoSyncEngine
from icloudbridge.core.reminders_sync import RemindersSyncEngine
from icloudbridge.core.sync import NotesSyncEngine
from icloudbridge.utils.db import NotesDB, PasswordsDB, RemindersDB
from icloudbridge.utils.photos_db import PhotosDB

logger = logging.getLogger(__name__)


@lru_cache
def get_config() -> AppConfig:
    """Get the application configuration.

    Returns:
        AppConfig: Application configuration instance

    Note:
        This is cached to avoid reloading config on every request.
        Cache is cleared on config updates via the API.
    """
    # Load config from the path stored in settings database
    from icloudbridge.utils.settings_db import get_config_path

    config_path = get_config_path()
    return load_config(config_path)


async def get_notes_sync_engine(config: Annotated[AppConfig, Depends(get_config)]) -> NotesSyncEngine:
    """Get an initialized notes sync engine.

    Args:
        config: Application configuration

    Returns:
        NotesSyncEngine: Initialized notes sync engine
    """
    config.ensure_data_dir()
    db_path = config.general.data_dir / "notes.db"
    markdown_base_path = config.notes.remote_folder

    if not markdown_base_path:
        raise ValueError("Notes remote_folder not configured")

    engine = NotesSyncEngine(markdown_base_path, db_path)
    await engine.initialize()
    return engine


async def get_reminders_sync_engine(
    config: Annotated[AppConfig, Depends(get_config)]
) -> RemindersSyncEngine:
    """Get an initialized reminders sync engine.

    Args:
        config: Application configuration

    Returns:
        RemindersSyncEngine: Initialized reminders sync engine
    """
    config.ensure_data_dir()
    db_path = config.general.data_dir / "reminders.db"

    # Get CalDAV credentials
    caldav_password = config.reminders.get_caldav_password()
    if not caldav_password:
        raise ValueError("CalDAV password not found in keyring")

    engine = RemindersSyncEngine(
        caldav_url=config.reminders.caldav_url,
        caldav_username=config.reminders.caldav_username,
        caldav_password=caldav_password,
        db_path=db_path,
        caldav_ssl_verify_cert=config.reminders.caldav_ssl_verify_cert,
    )
    await engine.initialize()
    return engine


async def get_passwords_sync_engine(
    config: Annotated[AppConfig, Depends(get_config)]
) -> PasswordsSyncEngine:
    """Get an initialized passwords sync engine.

    Args:
        config: Application configuration

    Returns:
        PasswordsSyncEngine: Initialized passwords sync engine
    """
    # Get the passwords database
    db = await get_passwords_db(config)
    engine = PasswordsSyncEngine(db)
    # Passwords engine doesn't need async initialization
    return engine


async def get_photos_sync_engine(
    config: Annotated[AppConfig, Depends(get_config)]
) -> PhotoSyncEngine:
    """Get an initialized photo sync engine."""

    if not config.photos.enabled:
        raise ValueError("Photo sync is disabled in configuration")

    engine = PhotoSyncEngine(config.photos, config.general.data_dir)
    await engine.initialize()
    return engine


async def get_photos_export_engine(
    config: Annotated[AppConfig, Depends(get_config)]
) -> PhotoExportEngine:
    """Get an initialized photo export engine.

    This engine exports photos from Apple Photos to a local folder.
    Requires bidirectional or export sync mode to be enabled.
    The export folder defaults to the first import source path.
    """
    from pathlib import Path

    if not config.photos.enabled:
        raise ValueError("Photo sync is disabled in configuration")

    if config.photos.sync_mode not in ("export", "bidirectional"):
        raise ValueError(
            f"Photo export requires sync_mode='export' or 'bidirectional', "
            f"got '{config.photos.sync_mode}'"
        )

    export_cfg = config.photos.export

    # Determine export folder (default to first import source path)
    export_folder = export_cfg.export_folder
    if not export_folder:
        # Use first configured import source path
        if config.photos.sources:
            first_source = next(iter(config.photos.sources.values()))
            export_folder = first_source.path
        else:
            raise ValueError("No export folder configured and no import sources available")

    export_config = ExportConfig(
        export_folder=Path(export_folder),
        organize_by=export_cfg.organize_by,
    )

    db = await get_photos_db(config)
    engine = PhotoExportEngine(config=export_config, db=db)
    await engine.initialize()
    return engine


async def get_notes_db(config: Annotated[AppConfig, Depends(get_config)]) -> NotesDB:
    """Get a notes database connection.

    Args:
        config: Application configuration

    Returns:
        NotesDB: Notes database instance
    """
    config.ensure_data_dir()
    db = NotesDB(config.general.data_dir / "notes.db")
    await db.initialize()
    return db


async def get_reminders_db(config: Annotated[AppConfig, Depends(get_config)]) -> RemindersDB:
    """Get a reminders database connection.

    Args:
        config: Application configuration

    Returns:
        RemindersDB: Reminders database instance
    """
    config.ensure_data_dir()
    db = RemindersDB(config.general.data_dir / "reminders.db")
    await db.initialize()
    return db


async def get_passwords_db(config: Annotated[AppConfig, Depends(get_config)]) -> PasswordsDB:
    """Get a passwords database connection.

    Args:
        config: Application configuration

    Returns:
        PasswordsDB: Passwords database instance
    """
    config.ensure_data_dir()
    db = PasswordsDB(config.general.data_dir / "passwords.db")
    await db.initialize()
    return db


async def get_photos_db(config: Annotated[AppConfig, Depends(get_config)]) -> PhotosDB:
    """Get a photos database connection."""

    config.ensure_data_dir()
    db = PhotosDB(config.general.data_dir / "photos.db")
    await db.initialize()
    return db


# Type aliases for dependency injection
ConfigDep = Annotated[AppConfig, Depends(get_config)]
NotesSyncEngineDep = Annotated[NotesSyncEngine, Depends(get_notes_sync_engine)]
RemindersSyncEngineDep = Annotated[RemindersSyncEngine, Depends(get_reminders_sync_engine)]
PasswordsSyncEngineDep = Annotated[PasswordsSyncEngine, Depends(get_passwords_sync_engine)]
PhotosSyncEngineDep = Annotated[PhotoSyncEngine, Depends(get_photos_sync_engine)]
PhotosExportEngineDep = Annotated[PhotoExportEngine, Depends(get_photos_export_engine)]
NotesDBDep = Annotated[NotesDB, Depends(get_notes_db)]
RemindersDBDep = Annotated[RemindersDB, Depends(get_reminders_db)]
PasswordsDBDep = Annotated[PasswordsDB, Depends(get_passwords_db)]
PhotosDBDep = Annotated[PhotosDB, Depends(get_photos_db)]
