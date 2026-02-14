"""Configuration management endpoints."""

import logging
from functools import lru_cache

from fastapi import APIRouter, HTTPException, status

from icloudbridge.api.dependencies import ConfigDep
from icloudbridge.api.models import ConfigResponse, ConfigUpdateRequest
from icloudbridge.core.config import FolderMapping, PhotoSourceConfig, PasswordsConfig
from icloudbridge.utils.credentials import CredentialStore
from icloudbridge.sources.reminders.caldav_adapter import CalDAVAdapter

logger = logging.getLogger(__name__)

router = APIRouter()


def _serialize_folder_mappings(mappings: dict[str, FolderMapping]) -> dict[str, dict[str, str]]:
    """Convert FolderMapping objects into primitive dicts for responses."""
    return {apple_folder: mapping.model_dump() for apple_folder, mapping in mappings.items()}


def _serialize_photo_sources(sources: dict[str, PhotoSourceConfig]) -> dict[str, dict[str, str | bool]]:
    """Convert PhotoSourceConfig objects into primitives for responses."""
    serialized: dict[str, dict[str, str | bool]] = {}
    for name, source in sources.items():
        serialized[name] = {
            "path": str(source.path),
            "recursive": source.recursive,
            "include_images": source.include_images,
            "include_videos": source.include_videos,
            "album": source.album or "",
            "delete_after_import": source.delete_after_import,
            "metadata_sidecars": source.metadata_sidecars,
        }
    return serialized


@router.get("", response_model=ConfigResponse)
async def get_config(config: ConfigDep):
    """Get current configuration.

    Returns the current configuration without sensitive data (passwords).
    """
    # Derive Nextcloud URL from CalDAV URL if it follows the Nextcloud pattern
    reminders_nextcloud_url = None
    if config.reminders.caldav_url and "/remote.php/dav" in config.reminders.caldav_url:
        reminders_nextcloud_url = config.reminders.caldav_url.replace("/remote.php/dav", "").rstrip("/")

    return ConfigResponse(
        data_dir=str(config.general.data_dir),
        config_file=str(config.default_config_path) if config.default_config_path else None,
        notes_enabled=config.notes.enabled,
        reminders_enabled=config.reminders.enabled,
        passwords_enabled=config.passwords.enabled,
        photos_enabled=config.photos.enabled,
        notes_remote_folder=str(config.notes.remote_folder) if config.notes.remote_folder else None,
        notes_folder_mappings=_serialize_folder_mappings(config.notes.folder_mappings),
        reminders_caldav_url=config.reminders.caldav_url,
        reminders_caldav_username=config.reminders.caldav_username,
        reminders_nextcloud_url=reminders_nextcloud_url,
        reminders_sync_mode=config.reminders.sync_mode,
        reminders_calendar_mappings=config.reminders.calendar_mappings or {},
        reminders_caldav_ssl_verify_cert=config.reminders.caldav_ssl_verify_cert,
        passwords_provider=config.passwords.provider,
        passwords_ssl_verify_cert=config.passwords.passwords_ssl_verify_cert,
        passwords_vaultwarden_url=config.passwords.vaultwarden_url,
        passwords_vaultwarden_email=config.passwords.vaultwarden_email,
        passwords_nextcloud_url=config.passwords.nextcloud_url,
        passwords_nextcloud_username=config.passwords.nextcloud_username,
        photos_default_album=config.photos.default_album,
        photo_sources=_serialize_photo_sources(config.photos.sources),
        # Photo sync mode and export settings
        photos_sync_mode=config.photos.sync_mode,
        photos_export_mode=config.photos.export_mode,
        photos_export_folder=str(config.photos.export.export_folder) if config.photos.export.export_folder else None,
        photos_export_organize_by=config.photos.export.organize_by,
    )


@router.put("", response_model=ConfigResponse)
async def update_config(update: ConfigUpdateRequest, config: ConfigDep):
    """Update configuration.

    Updates the configuration and saves to disk. Passwords are stored
    in the system keyring for security.

    Args:
        update: Configuration update request

    Returns:
        Updated configuration
    """
    # Debug: Log the entire update request
    print(f"[DEBUG] Received config update request: {update.model_dump(exclude_none=False)}")
    logger.info(f"Received config update request: {update.model_dump(exclude_none=False)}")

    credential_store = CredentialStore()

    # Update general config
    if update.data_dir is not None:
        from pathlib import Path
        from icloudbridge.utils.settings_db import set_config_path

        config.general.data_dir = Path(update.data_dir).expanduser()
        # Store config file location in database as single source of truth
        config_path = config.general.data_dir / "config.toml"
        set_config_path(config_path)
        print(f"[DEBUG] Stored config path in DB: {config_path}")

    # Update notes config
    if update.notes_enabled is not None:
        config.notes.enabled = update.notes_enabled
    if update.notes_remote_folder is not None:
        from pathlib import Path
        config.notes.remote_folder = Path(update.notes_remote_folder).expanduser()
    if update.notes_folder_mappings is not None:
        try:
            config.notes.folder_mappings = {
                apple_folder: FolderMapping(**mapping)
                for apple_folder, mapping in update.notes_folder_mappings.items()
            }
        except Exception as exc:
            logger.error(f"Invalid notes folder mappings: {exc}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid notes folder mappings: {exc}",
            )

    # Update reminders config
    if update.reminders_enabled is not None:
        config.reminders.enabled = update.reminders_enabled
        logger.info(f"Updated reminders enabled: {update.reminders_enabled}")
    if update.reminders_caldav_url is not None:
        config.reminders.caldav_url = update.reminders_caldav_url
        logger.info(f"Updated CalDAV URL: {update.reminders_caldav_url}")
    if update.reminders_caldav_username is not None:
        config.reminders.caldav_username = update.reminders_caldav_username
        logger.info(f"Updated CalDAV username: {update.reminders_caldav_username}")
    if update.reminders_sync_mode is not None:
        config.reminders.sync_mode = update.reminders_sync_mode
        logger.info(f"Updated sync mode: {update.reminders_sync_mode}")
    if update.reminders_caldav_ssl_verify_cert is not None:
        config.reminders.caldav_ssl_verify_cert = update.reminders_caldav_ssl_verify_cert
        logger.info(f"Updated CalDAV SSL verify setting: {update.reminders_caldav_ssl_verify_cert}")
    if update.passwords_ssl_verify_cert is not None:
        config.passwords.passwords_ssl_verify_cert = update.passwords_ssl_verify_cert
        logger.info(f"Updated Passwords SSL verify setting: {update.passwords_ssl_verify_cert}")
    if update.reminders_calendar_mappings is not None:
        caldav_lookup: dict[str, str] = {}
        if config.reminders.caldav_url and config.reminders.caldav_username:
            password = credential_store.get_caldav_password(config.reminders.caldav_username)
            if password:
                adapter = CalDAVAdapter(
                    config.reminders.caldav_url,
                    config.reminders.caldav_username,
                    password,
                    ssl_verify_cert=config.reminders.caldav_ssl_verify_cert,
                )
                if await adapter.connect():
                    calendars = await adapter.list_calendars()
                    caldav_lookup = {cal["name"].lower(): cal["name"] for cal in calendars}

        def canonicalize(name: str) -> str:
            lowered = (name or "").lower()
            return caldav_lookup.get(lowered, name)

        normalized_mappings = {
            apple_name: canonicalize(caldav_name)
            for apple_name, caldav_name in update.reminders_calendar_mappings.items()
        }
        config.reminders.calendar_mappings = normalized_mappings
        logger.info(f"Updated calendar mappings: {normalized_mappings}")

    # Store password AFTER username is set
    print(f"[DEBUG] Checking password field: reminders_caldav_password = {update.reminders_caldav_password!r}")
    if update.reminders_caldav_password is not None and update.reminders_caldav_password != "":
        print("[DEBUG] Password field is not None, attempting to store...")
        # Store password in keyring
        try:
            username = update.reminders_caldav_username or config.reminders.caldav_username
            print(f"[DEBUG] Using username for password storage: {username}")
            if not username:
                raise ValueError("CalDAV username is required to store password")
            credential_store.set_caldav_password(username, update.reminders_caldav_password)
            print(f"[DEBUG] CalDAV password stored in keyring for user: {username}")
            logger.info(f"CalDAV password stored in keyring for user: {username}")
        except Exception as e:
            print(f"[DEBUG] Failed to store CalDAV password in keyring: {e}")
            logger.error(f"Failed to store CalDAV password in keyring: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to store CalDAV password: {str(e)}"
            )
    else:
        print("[DEBUG] Password field is None, skipping password storage")

    # Update passwords config
    if update.passwords_enabled is not None:
        config.passwords.enabled = update.passwords_enabled
    if update.passwords_provider is not None:
        try:
            config.passwords.provider = PasswordsConfig.validate_provider(update.passwords_provider)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )
    if update.passwords_vaultwarden_url is not None:
        config.passwords.vaultwarden_url = update.passwords_vaultwarden_url
    if update.passwords_vaultwarden_email is not None:
        config.passwords.vaultwarden_email = update.passwords_vaultwarden_email
    # Handle VaultWarden credentials (password, client_id, client_secret)
    # Support partial updates: can update any field without re-entering others
    if config.passwords.enabled and (
        (update.passwords_vaultwarden_password is not None and update.passwords_vaultwarden_password != "") or
        (update.passwords_vaultwarden_client_id is not None and update.passwords_vaultwarden_client_id != "") or
        (update.passwords_vaultwarden_client_secret is not None and update.passwords_vaultwarden_client_secret != "")
    ):
        try:
            email = update.passwords_vaultwarden_email or config.passwords.vaultwarden_email
            if not email:
                raise ValueError("VaultWarden email is required to store credentials")

            # Fetch existing credentials for partial updates
            existing_creds = None
            try:
                existing_creds = credential_store.get_vaultwarden_credentials(email)
            except Exception:
                pass  # No existing credentials

            # Merge new with existing (preserve existing if new is None/empty)
            password = update.passwords_vaultwarden_password if update.passwords_vaultwarden_password else \
                       (existing_creds.get("password") if existing_creds else None)
            client_id = update.passwords_vaultwarden_client_id if update.passwords_vaultwarden_client_id is not None else \
                        (existing_creds.get("client_id") if existing_creds else None)
            client_secret = update.passwords_vaultwarden_client_secret if update.passwords_vaultwarden_client_secret is not None else \
                            (existing_creds.get("client_secret") if existing_creds else None)

            # Store merged credentials
            credential_store.set_vaultwarden_credentials(
                email=email,
                password=password,
                client_id=client_id,
                client_secret=client_secret,
            )
            logger.info(f"VaultWarden credentials stored in keyring for: {email}")
        except Exception as e:
            logger.error(f"Failed to store VaultWarden credentials in keyring: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to store VaultWarden credentials: {str(e)}"
            )
    if update.passwords_nextcloud_url is not None:
        config.passwords.nextcloud_url = update.passwords_nextcloud_url
    if update.passwords_nextcloud_username is not None:
        config.passwords.nextcloud_username = update.passwords_nextcloud_username
    if update.passwords_nextcloud_app_password is not None and update.passwords_nextcloud_app_password != "":
        try:
            username = update.passwords_nextcloud_username or config.passwords.nextcloud_username
            if not username:
                raise ValueError("Nextcloud username is required to store app password")
            credential_store.set_nextcloud_credentials(username, update.passwords_nextcloud_app_password)
            logger.info(f"Nextcloud credentials stored in keyring for: {username}")
        except Exception as e:
            logger.error(f"Failed to store Nextcloud credentials in keyring: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to store Nextcloud credentials: {str(e)}"
            )

    # Update photos config
    if update.photos_enabled is not None:
        config.photos.enabled = update.photos_enabled
    if update.photos_default_album is not None:
        # Use the provided value if non-empty, otherwise use default
        config.photos.default_album = update.photos_default_album.strip() if update.photos_default_album else "iCloudBridge Imports"
    if update.photo_sources is not None:
        try:
            config.photos.sources = {
                name: PhotoSourceConfig(**src)
                for name, src in update.photo_sources.items()
            }
        except Exception as exc:
            logger.error(f"Invalid photo sources configuration: {exc}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid photo sources configuration: {exc}",
            )

    # Update photo sync mode and export settings
    if update.photos_sync_mode is not None:
        config.photos.sync_mode = update.photos_sync_mode
    if update.photos_export_mode is not None:
        config.photos.export_mode = update.photos_export_mode
    if update.photos_export_folder is not None:
        from pathlib import Path
        config.photos.export.export_folder = Path(update.photos_export_folder).expanduser().resolve() if update.photos_export_folder else None
    if update.photos_export_organize_by is not None:
        config.photos.export.organize_by = update.photos_export_organize_by

    # Save config to disk
    try:
        print(f"[DEBUG SAVE] Before save - username: {config.reminders.caldav_username}")
        print(f"[DEBUG SAVE] Before save - URL: {config.reminders.caldav_url}")
        print(f"[DEBUG SAVE] Saving to: {config.default_config_path}")
        config.save_to_file(config.default_config_path)
        print(f"[DEBUG SAVE] Config saved successfully")

        # Clear the cached config so next request gets updated version
        from icloudbridge.api.dependencies import get_config
        get_config.cache_clear()

        logger.info("Configuration updated successfully")
    except Exception as e:
        logger.error(f"Failed to save configuration: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save configuration: {str(e)}"
        )

    # Derive Nextcloud URL from CalDAV URL if it follows the Nextcloud pattern
    reminders_nextcloud_url = None
    if config.reminders.caldav_url and "/remote.php/dav" in config.reminders.caldav_url:
        reminders_nextcloud_url = config.reminders.caldav_url.replace("/remote.php/dav", "").rstrip("/")

    return ConfigResponse(
        data_dir=str(config.general.data_dir),
        config_file=str(config.default_config_path) if config.default_config_path else None,
        notes_enabled=config.notes.enabled,
        reminders_enabled=config.reminders.enabled,
        passwords_enabled=config.passwords.enabled,
        photos_enabled=config.photos.enabled,
        notes_remote_folder=str(config.notes.remote_folder) if config.notes.remote_folder else None,
        notes_folder_mappings=_serialize_folder_mappings(config.notes.folder_mappings),
        reminders_caldav_url=config.reminders.caldav_url,
        reminders_caldav_username=config.reminders.caldav_username,
        reminders_nextcloud_url=reminders_nextcloud_url,
        reminders_sync_mode=config.reminders.sync_mode,
        reminders_calendar_mappings=config.reminders.calendar_mappings or {},
        reminders_caldav_ssl_verify_cert=config.reminders.caldav_ssl_verify_cert,
        passwords_ssl_verify_cert=config.passwords.passwords_ssl_verify_cert,
        passwords_vaultwarden_url=config.passwords.vaultwarden_url,
        passwords_vaultwarden_email=config.passwords.vaultwarden_email,
        photos_default_album=config.photos.default_album,
        photo_sources=_serialize_photo_sources(config.photos.sources),
        # Photo sync mode and export settings
        photos_sync_mode=config.photos.sync_mode,
        photos_export_mode=config.photos.export_mode,
        photos_export_folder=str(config.photos.export.export_folder) if config.photos.export.export_folder else None,
        photos_export_organize_by=config.photos.export.organize_by,
    )


@router.get("/validate")
async def validate_config(config: ConfigDep):
    """Validate current configuration.

    Checks if the configuration is valid and all required fields are set.

    Returns:
        Validation status and any errors
    """
    errors = []

    # Validate notes config
    if config.notes.enabled:
        if not config.notes.remote_folder:
            errors.append("Notes remote folder is not configured")
        elif not config.notes.remote_folder.exists():
            errors.append(f"Notes remote folder does not exist: {config.notes.remote_folder}")

    # Validate reminders config
    if config.reminders.enabled:
        if not config.reminders.caldav_url:
            errors.append("Reminders CalDAV URL is not configured")
        if not config.reminders.caldav_username:
            errors.append("Reminders CalDAV username is not configured")

        # Check if password is available
        credential_store = CredentialStore()
        if not credential_store.has_caldav_password(config.reminders.caldav_username):
            errors.append("Reminders CalDAV password is not stored in keyring")

    # Validate passwords config
    if config.passwords.enabled:
        if not config.passwords.vaultwarden_url:
            errors.append("Passwords VaultWarden URL is not configured")
        if not config.passwords.vaultwarden_email:
            errors.append("Passwords VaultWarden email is not configured")

        # Check if credentials are available
        credential_store = CredentialStore()
        if not credential_store.has_vaultwarden_credentials(config.passwords.vaultwarden_email):
            errors.append("Passwords VaultWarden credentials are not stored in keyring")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


@router.post("/reset")
async def reset_configuration(config: ConfigDep):
    """Complete configuration reset.

    This will:
    - Delete all databases (notes.db, reminders.db, passwords.db)
    - Clear all passwords from macOS Keychain
    - Delete the configuration file
    - Delete the data directory

    WARNING: This action cannot be undone!
    Note: Does NOT delete synced markdown files from the notes folder.

    Returns:
        Success message
    """
    import shutil
    from pathlib import Path

    logger.info("Starting complete configuration reset")

    try:
        credential_store = CredentialStore()

        # 1. Delete passwords from keychain
        logger.info("Deleting passwords from keychain")

        # Delete CalDAV password if username exists
        if config.reminders.caldav_username:
            try:
                credential_store.delete_caldav_password(config.reminders.caldav_username)
                logger.info(f"Deleted CalDAV password for: {config.reminders.caldav_username}")
            except Exception as e:
                logger.warning(f"Failed to delete CalDAV password: {e}")

        # Delete VaultWarden credentials if email exists
        if config.passwords.vaultwarden_email:
            try:
                credential_store.delete_vaultwarden_credentials(config.passwords.vaultwarden_email)
                logger.info(f"Deleted VaultWarden credentials for: {config.passwords.vaultwarden_email}")
            except Exception as e:
                logger.warning(f"Failed to delete VaultWarden credentials: {e}")

        # 2. Get paths before we lose the config
        data_dir = Path(config.general.data_dir).expanduser()
        config_file = config.default_config_path

        logger.info(f"Data directory: {data_dir}")
        logger.info(f"Config file: {config_file}")

        # 3. Delete individual database files first (in case data dir deletion fails)
        if data_dir.exists():
            logger.info("Deleting database files")
            for db_file in ["notes.db", "reminders.db", "passwords.db", "settings.db"]:
                db_path = data_dir / db_file
                if db_path.exists():
                    try:
                        db_path.unlink()
                        logger.info(f"Deleted: {db_path}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {db_path}: {e}")

        # 4. Delete the config file
        if config_file and config_file.exists():
            try:
                config_file.unlink()
                logger.info(f"Deleted config file: {config_file}")
            except Exception as e:
                logger.warning(f"Failed to delete config file: {e}")

        # 5. Delete the entire data directory
        if data_dir.exists():
            try:
                shutil.rmtree(data_dir)
                logger.info(f"Deleted data directory: {data_dir}")
            except Exception as e:
                logger.warning(f"Failed to delete data directory: {e}")

        # 6. Clear the cached config so next request gets defaults
        from icloudbridge.api.dependencies import get_config
        get_config.cache_clear()

        logger.info("Configuration reset completed successfully")

        return {
            "status": "success",
            "message": "Configuration has been completely reset. All databases, passwords, and configuration files have been deleted.",
        }

    except Exception as e:
        logger.error(f"Failed to reset configuration: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset configuration: {str(e)}"
        )


@router.post("/test-connection")
async def test_connection(service: str, config: ConfigDep):
    """Test connection to a service.

    Tests the connection to CalDAV or VaultWarden to ensure credentials
    and configuration are correct.

    Args:
        service: Service to test (reminders, passwords)

    Returns:
        Connection test result
    """
    # Clear cache and reload config from database
    from icloudbridge.api.dependencies import get_config
    from icloudbridge.core.config import load_config
    from icloudbridge.utils.settings_db import get_config_path

    get_config.cache_clear()

    # Get config path from database - single source of truth
    config_file = get_config_path()
    print(f"[DEBUG TEST] Config path from DB: {config_file}")

    if config_file and config_file.exists():
        print(f"[DEBUG TEST] Loading from: {config_file}")
        config = load_config(config_file)
    else:
        print(f"[DEBUG TEST] Config path not found or doesn't exist, using defaults")
        config = get_config()

    if service == "reminders":
        try:
            from icloudbridge.sources.reminders.caldav_adapter import CalDAVAdapter

            print(f"[DEBUG TEST] Config username: {config.reminders.caldav_username}")
            print(f"[DEBUG TEST] Config URL: {config.reminders.caldav_url}")
            password = config.reminders.get_caldav_password()
            print(f"[DEBUG TEST] Retrieved password: {'<exists>' if password else '<none>'}")
            if not password:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="CalDAV password not found in keyring"
                )

            adapter = CalDAVAdapter(
                config.reminders.caldav_url,
                config.reminders.caldav_username,
                password,
                ssl_verify_cert=config.reminders.caldav_ssl_verify_cert,
            )

            # Try to connect and list calendars
            calendars = await adapter.list_calendars()

            return {
                "success": True,
                "message": f"Successfully connected to CalDAV server. Found {len(calendars)} calendars.",
                "calendars": [cal["name"] for cal in calendars],
            }
        except Exception as e:
            logger.error(f"CalDAV connection test failed: {e}")
            return {
                "success": False,
                "message": f"Connection failed: {str(e)}",
            }

    elif service == "passwords":
        provider_name = (config.passwords.provider or "vaultwarden").lower()
        credential_store = CredentialStore()

        if provider_name == "nextcloud":
            try:
                from icloudbridge.sources.passwords.providers import NextcloudPasswordsProvider

                username = config.passwords.nextcloud_username
                url = config.passwords.nextcloud_url
                if not username or not url:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Nextcloud username and URL must be configured",
                    )

                credentials = credential_store.get_nextcloud_credentials(username)
                if not credentials:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Nextcloud credentials not found in keyring",
                    )

                provider = NextcloudPasswordsProvider(url, username, credentials["app_password"])
                try:
                    await provider.authenticate()
                finally:
                    await provider.close()

                return {
                    "success": True,
                    "message": "Successfully connected to Nextcloud Passwords.",
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Nextcloud connection test failed: {e}")
                return {
                    "success": False,
                    "message": f"Connection failed: {str(e)}",
                }
        else:
            try:
                from icloudbridge.sources.passwords.vaultwarden_api import VaultwardenAPIClient

                credentials = credential_store.get_vaultwarden_credentials(config.passwords.vaultwarden_email)

                if not credentials:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="VaultWarden credentials not found in keyring"
                    )

                client = VaultwardenAPIClient(
                    config.passwords.vaultwarden_url,
                    credentials['email'],
                    credentials['password'],
                    credentials.get('client_id'),
                    credentials.get('client_secret'),
                )

                try:
                    await client.authenticate()
                finally:
                    await client.close()

                return {
                    "success": True,
                    "message": "Successfully connected to VaultWarden server.",
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"VaultWarden connection test failed: {e}")
                return {
                    "success": False,
                    "message": f"Connection failed: {str(e)}",
                }

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown service: {service}"
        )
