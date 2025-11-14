"""Passwords synchronization endpoints."""

import json
import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from icloudbridge.api.dependencies import ConfigDep, PasswordsDBDep, PasswordsSyncEngineDep
from icloudbridge.api.downloads import download_manager
from icloudbridge.api.models import NextcloudCredentialRequest, VaultwardenCredentialRequest
from icloudbridge.sources.passwords.providers import NextcloudPasswordsProvider, VaultwardenProvider
from icloudbridge.sources.passwords.vaultwarden_api import VaultwardenAPIClient
from icloudbridge.utils.credentials import CredentialStore
from icloudbridge.utils.db import SyncLogsDB

logger = logging.getLogger(__name__)

router = APIRouter()


def _cleanup_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:  # pragma: no cover - best effort cleanup
        pass


async def _save_uploaded_csv(upload: UploadFile) -> Path:
    suffix = Path(upload.filename or "").suffix or ".csv"
    temp_path = Path(tempfile.gettempdir()) / f"icloudbridge-passwords-{uuid.uuid4().hex}{suffix}"
    data = await upload.read()
    temp_path.write_bytes(data)
    return temp_path


async def _build_password_provider(config: ConfigDep):
    """Instantiate the configured password provider with stored credentials."""

    provider_name = (config.passwords.provider or "vaultwarden").lower()
    credential_store = CredentialStore()

    if provider_name == "nextcloud":
        username = config.passwords.nextcloud_username
        url = config.passwords.nextcloud_url
        if not username or not url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nextcloud username and URL must be configured.",
            )

        credentials = credential_store.get_nextcloud_credentials(username)
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Nextcloud credentials not found. Please configure them first.",
            )

        provider = NextcloudPasswordsProvider(url, username, credentials["app_password"])
    else:
        email = config.passwords.vaultwarden_email
        url = config.passwords.vaultwarden_url
        if not email or not url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="VaultWarden URL and email must be configured.",
            )

        if not url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="VaultWarden URL must include http:// or https://",
            )

        credentials = credential_store.get_vaultwarden_credentials(email)
        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="VaultWarden credentials not found. Please configure them first.",
            )

        provider = VaultwardenProvider(
            url=url,
            email=credentials["email"],
            password=credentials["password"],
            client_id=credentials.get("client_id"),
            client_secret=credentials.get("client_secret"),
        )

    await provider.authenticate()
    return provider


async def _attach_download_metadata(result: dict) -> tuple[dict, bool]:
    pull_stats = result.get("pull")
    if not pull_stats:
        return result, False

    download_path = pull_stats.pop("download_path", None)
    if not download_path:
        return result, False

    csv_path = Path(download_path)
    if not csv_path.exists():
        return result, False

    token, expires_at = await download_manager.register(csv_path, filename=csv_path.name)
    expires_iso = datetime.fromtimestamp(expires_at).isoformat()
    download_info = {
        "token": token,
        "filename": csv_path.name,
        "expires_at": expires_iso,
    }
    result["download"] = download_info
    pull_stats["download_token"] = token
    pull_stats["download_filename"] = csv_path.name
    pull_stats["download_expires_at"] = expires_iso

    return result, True


async def _run_passwords_sync(
    *,
    engine: PasswordsSyncEngineDep,
    config: ConfigDep,
    uploaded_file: UploadFile | None,
    simulate: bool,
    run_push: bool,
    run_pull: bool,
    log_sync_type: str | None,
    bulk_push: bool,
):
    apple_csv_path: Path | None = None
    output_csv_path: Path | None = None
    keep_output_file = False
    provider = None
    log_id = None
    sync_logs_db: SyncLogsDB | None = None

    try:
        if run_push:
            if not uploaded_file:
                raise HTTPException(status_code=400, detail="Apple Passwords CSV is required")
            apple_csv_path = await _save_uploaded_csv(uploaded_file)

        if run_pull and not simulate:
            output_csv_path = Path(tempfile.gettempdir()) / f"apple-import-{uuid.uuid4().hex}.csv"

        provider = await _build_password_provider(config)

        if log_sync_type and not simulate:
            sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
            await sync_logs_db.initialize()
            log_id = await sync_logs_db.create_log(
                service="passwords",
                sync_type=log_sync_type,
                status="running",
            )

        result = await engine.sync(
            apple_csv_path=apple_csv_path,
            provider=provider,
            output_apple_csv=output_csv_path,
            simulate=simulate,
            run_push=run_push,
            run_pull=run_pull,
            bulk_push=bulk_push,
        )

        result, keep_output_file = await _attach_download_metadata(result)

        if log_id and sync_logs_db:
            await sync_logs_db.update_log(
                log_id=log_id,
                status="completed",
                duration_seconds=round(result.get("total_time", 0), 0),
                stats_json=json.dumps(result),
            )

        response = {
            "status": "success",
            "simulate": simulate,
            "mode": {"push": run_push, "pull": run_pull},
            "stats": result,
        }
        if result.get("download"):
            response["download"] = result["download"]
        return response

    except HTTPException as http_exc:
        if log_id and sync_logs_db:
            await sync_logs_db.update_log(
                log_id=log_id,
                status="failed",
                duration_seconds=0,
                error_message=http_exc.detail if isinstance(http_exc.detail, str) else str(http_exc.detail),
            )
        raise
    except Exception as exc:
        if log_id and sync_logs_db:
            await sync_logs_db.update_log(
                log_id=log_id,
                status="failed",
                duration_seconds=0,
                error_message=str(exc),
            )
        logger.error("Passwords sync failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync failed: {exc}",
        )
    finally:
        if apple_csv_path:
            apple_csv_path.unlink(missing_ok=True)
        if output_csv_path and not keep_output_file:
            output_csv_path.unlink(missing_ok=True)
        if 'provider' in locals() and provider:
            try:
                await provider.close()
            except Exception:
                pass


@router.post("/import/apple")
async def import_apple_csv(
    file: UploadFile = File(...),
    engine: PasswordsSyncEngineDep = None,
):
    """Import passwords from Apple Passwords CSV export.

    Args:
        file: CSV file uploaded from Apple Passwords

    Returns:
        Import statistics
    """
    try:
        # Save uploaded file to temporary location
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.csv') as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # Import from CSV
        result = await engine.import_apple_csv(tmp_path)

        # Clean up temporary file
        Path(tmp_path).unlink()

        logger.info(f"Apple CSV import complete: {result}")

        return {
            "status": "success",
            "stats": result,
        }

    except Exception as e:
        logger.error(f"Failed to import Apple CSV: {e}")
        # Clean up on error
        if 'tmp_path' in locals():
            Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}"
        )


@router.post("/import/bitwarden")
async def import_bitwarden_csv(
    file: UploadFile = File(...),
    engine: PasswordsSyncEngineDep = None,
):
    """Import passwords from Bitwarden CSV export.

    Args:
        file: CSV file exported from Bitwarden/VaultWarden

    Returns:
        Import statistics
    """
    try:
        # Save uploaded file to temporary location
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.csv') as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        # Import from CSV
        result = await engine.import_bitwarden_csv(tmp_path)

        # Clean up temporary file
        Path(tmp_path).unlink()

        logger.info(f"Bitwarden CSV import complete: {result}")

        return {
            "status": "success",
            "stats": result,
        }

    except Exception as e:
        logger.error(f"Failed to import Bitwarden CSV: {e}")
        # Clean up on error
        if 'tmp_path' in locals():
            Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Import failed: {str(e)}"
        )


@router.post("/export/bitwarden")
async def export_bitwarden_csv(
    engine: PasswordsSyncEngineDep,
):
    """Generate Bitwarden-formatted CSV for import.

    Returns:
        Path to generated CSV file
    """
    try:
        # Generate CSV in temp directory
        output_path = Path(tempfile.gettempdir()) / "bitwarden_export.csv"

        await engine.export_bitwarden_csv(str(output_path))

        logger.info(f"Bitwarden CSV export generated: {output_path}")

        return {
            "status": "success",
            "path": str(output_path),
            "message": "CSV file generated successfully. Download from the provided path.",
        }

    except Exception as e:
        logger.error(f"Failed to export Bitwarden CSV: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export failed: {str(e)}"
        )


@router.post("/export/apple")
async def export_apple_csv(
    engine: PasswordsSyncEngineDep,
):
    """Generate Apple Passwords CSV for entries only in Bitwarden.

    Returns:
        Path to generated CSV file
    """
    try:
        # Generate CSV in temp directory
        output_path = Path(tempfile.gettempdir()) / "apple_import.csv"

        await engine.export_apple_csv(str(output_path))

        logger.info(f"Apple CSV export generated: {output_path}")

        return {
            "status": "success",
            "path": str(output_path),
            "message": "CSV file generated successfully. Download from the provided path.",
        }

    except Exception as e:
        logger.error(f"Failed to export Apple CSV: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export failed: {str(e)}"
        )


@router.post("/sync")
async def sync_passwords(
    file: UploadFile = File(...),
    engine: PasswordsSyncEngineDep = None,
    config: ConfigDep = None,
    simulate: bool = False,
    bulk: bool = True,
):
    """Full auto-sync: Apple ↔ VaultWarden (push & pull)."""

    result = await _run_passwords_sync(
        engine=engine,
        config=config,
        uploaded_file=file,
        simulate=simulate,
        run_push=True,
        run_pull=True,
        log_sync_type="manual",
        bulk_push=bulk,
    )
    return result


@router.post("/sync/export")
async def export_passwords(
    file: UploadFile = File(...),
    engine: PasswordsSyncEngineDep = None,
    config: ConfigDep = None,
    simulate: bool = False,
    bulk: bool = True,
):
    """Push Apple Passwords CSV changes to VaultWarden only."""

    return await _run_passwords_sync(
        engine=engine,
        config=config,
        uploaded_file=file,
        simulate=simulate,
        run_push=True,
        run_pull=False,
        log_sync_type=None,
        bulk_push=bulk,
    )


@router.post("/sync/import")
async def import_passwords(
    engine: PasswordsSyncEngineDep = None,
    config: ConfigDep = None,
    simulate: bool = False,
):
    """Pull new VaultWarden entries and prepare Apple CSV."""

    return await _run_passwords_sync(
        engine=engine,
        config=config,
        uploaded_file=None,
        simulate=simulate,
        run_push=False,
        run_pull=True,
        log_sync_type=None,
        bulk_push=True,
    )


@router.get("/download/{token}")
async def download_passwords_csv(token: str, background_tasks: BackgroundTasks):
    """Download a previously generated CSV using a temporary token."""

    try:
        file_path, filename = await download_manager.consume(token)
    except KeyError:
        raise HTTPException(status_code=404, detail="Download link expired or invalid")

    background_tasks.add_task(_cleanup_file, file_path)
    return FileResponse(file_path, media_type="text/csv", filename=filename)


@router.get("/status")
async def get_status(passwords_db: PasswordsDBDep, config: ConfigDep):
    """Get passwords sync status.

    Returns:
        Status information including last sync and entry count
    """
    stats = await passwords_db.get_stats()

    # Get last sync from logs
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()
    logs = await sync_logs_db.get_logs(service="passwords", limit=1)

    provider_name = (config.passwords.provider or "vaultwarden").lower()
    provider_label = "Nextcloud Passwords" if provider_name == "nextcloud" else "VaultWarden"

    # Transform last sync log to match frontend expectations
    last_sync = None
    if logs:
        log = logs[0]
        sync_stats = {}
        if log.get("stats_json"):
            try:
                sync_stats = json.loads(log["stats_json"])
            except json.JSONDecodeError:
                pass

        # Build message
        message = ""
        if log["status"] == "failed":
            message = log.get("error_message", "Sync failed")
        elif sync_stats:
            push_stats = sync_stats.get("push") or {}
            pull_stats = sync_stats.get("pull") or {}
            msg_parts = []
            created = push_stats.get("created")
            if created:
                msg_parts.append(f"pushed {created} to {provider_label}")
            new_entries = pull_stats.get("new_entries")
            if new_entries:
                msg_parts.append(f"pulled {new_entries} for Apple import")

            if msg_parts:
                message = f"Synced: {', '.join(msg_parts)}"
            else:
                message = "Synced, no changes needed"
        else:
            message = "Sync operation completed"

        # Convert timestamps to ISO strings
        started_at = datetime.fromtimestamp(log["started_at"]).isoformat() if log.get("started_at") else None
        completed_at = datetime.fromtimestamp(log["completed_at"]).isoformat() if log.get("completed_at") else None

        last_sync = {
            "id": log["id"],
            "service": log["service"],
            "operation": log["sync_type"],
            "status": log["status"],
            "message": message,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": log.get("duration_seconds"),
            "stats": sync_stats,
            "error_message": log.get("error_message"),
        }

    provider_name = (config.passwords.provider or "vaultwarden").lower()
    credential_store = CredentialStore()
    vaultwarden_email = config.passwords.vaultwarden_email or ""
    nextcloud_username = config.passwords.nextcloud_username or ""

    has_vaultwarden_credentials = (
        credential_store.has_vaultwarden_credentials(vaultwarden_email)
        if vaultwarden_email
        else False
    )
    has_nextcloud_credentials = (
        credential_store.has_nextcloud_credentials(nextcloud_username)
        if nextcloud_username
        else False
    )

    has_credentials = has_nextcloud_credentials if provider_name == "nextcloud" else has_vaultwarden_credentials

    return {
        "enabled": config.passwords.enabled,
        "provider": provider_name,
        "vaultwarden_url": config.passwords.vaultwarden_url,
        "vaultwarden_email": config.passwords.vaultwarden_email,
        "nextcloud_url": config.passwords.nextcloud_url,
        "nextcloud_username": config.passwords.nextcloud_username,
        "has_credentials": has_credentials,
        "has_vaultwarden_credentials": has_vaultwarden_credentials,
        "has_nextcloud_credentials": has_nextcloud_credentials,
        "total_entries": stats.get("total", 0),
        "by_source": stats.get("by_source", {}),
        "last_sync": last_sync,
    }


@router.get("/history")
async def get_history(
    config: ConfigDep,
    limit: int = 10,
    offset: int = 0,
):
    """Get passwords sync history.

    Args:
        limit: Maximum number of logs to return
        offset: Number of logs to skip

    Returns:
        List of sync log entries
    """
    provider_name = (config.passwords.provider or "vaultwarden").lower()
    provider_label = "Nextcloud Passwords" if provider_name == "nextcloud" else "VaultWarden"

    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    logs = await sync_logs_db.get_logs(
        service="passwords",
        limit=limit,
        offset=offset,
    )

    # Transform logs to match frontend expectations
    transformed_logs = []
    for log in logs:
        # Parse stats from JSON
        stats = {}
        if log.get("stats_json"):
            try:
                stats = json.loads(log["stats_json"])
            except json.JSONDecodeError:
                pass

        # Build descriptive message from stats
        message = ""
        if log["status"] == "failed":
            message = log.get("error_message", "Sync failed")
        elif stats:
            push_stats = stats.get("push") or {}
            pull_stats = stats.get("pull") or {}
            msg_parts = []
            created = push_stats.get("created")
            if created:
                msg_parts.append(f"pushed {created} to {provider_label}")
            new_entries = pull_stats.get("new_entries")
            if new_entries:
                msg_parts.append(f"pulled {new_entries} for Apple import")

            if msg_parts:
                message = f"Synced: {', '.join(msg_parts)}"
            else:
                message = "Synced, no changes needed"
        else:
            message = "Sync operation completed"

        # Convert Unix timestamps (seconds) to ISO strings
        started_at = datetime.fromtimestamp(log["started_at"]).isoformat() if log.get("started_at") else None
        completed_at = datetime.fromtimestamp(log["completed_at"]).isoformat() if log.get("completed_at") else None

        transformed_logs.append({
            "id": log["id"],
            "service": log["service"],
            "operation": log["sync_type"],
            "status": log["status"],
            "message": message,
            "started_at": started_at,
            "completed_at": completed_at,
            "duration_seconds": log.get("duration_seconds"),
            "stats": stats,
            "error_message": log.get("error_message"),
        })

    return {
        "logs": transformed_logs,
        "limit": limit,
        "offset": offset,
    }


@router.post("/reset")
async def reset_database(passwords_db: PasswordsDBDep, config: ConfigDep):
    """Reset passwords sync database, history, and keychain credentials.

    Clears all password entries from the database, deletes sync history,
    and removes Vaultwarden credentials from keychain.

    Returns:
        Success message
    """
    try:
        # Reset passwords database
        await passwords_db.clear_all_entries()
        logger.info("Passwords database reset successfully")

        # Clear sync history for passwords service
        sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
        await sync_logs_db.initialize()
        await sync_logs_db.clear_service_logs("passwords")
        logger.info("Passwords sync history cleared")

        credential_store = CredentialStore()

        # Delete Vaultwarden credentials from keychain if email exists
        if config.passwords.vaultwarden_email:
            try:
                credential_store.delete_vaultwarden_credentials(config.passwords.vaultwarden_email)
                logger.info(f"Deleted Vaultwarden credentials for: {config.passwords.vaultwarden_email}")
            except Exception as e:
                logger.warning(f"Failed to delete Vaultwarden credentials: {e}")

        # Delete Nextcloud credentials if username exists
        if config.passwords.nextcloud_username:
            try:
                credential_store.delete_nextcloud_credentials(config.passwords.nextcloud_username)
                logger.info(f"Deleted Nextcloud credentials for: {config.passwords.nextcloud_username}")
            except Exception as e:
                logger.warning(f"Failed to delete Nextcloud credentials: {e}")

        return {
            "status": "success",
            "message": "Passwords database, history, and keychain credentials reset successfully.",
        }
    except Exception as e:
        logger.error(f"Failed to reset passwords: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset passwords: {str(e)}"
        )


@router.post("/vaultwarden/credentials")
async def set_vaultwarden_credentials(
    payload: VaultwardenCredentialRequest,
    config: ConfigDep,
):
    """Store VaultWarden credentials in system keyring.

    Args:
        email: VaultWarden email
        password: VaultWarden password
        client_id: Optional OAuth client ID
        client_secret: Optional OAuth client secret

    Returns:
        Success message
    """
    try:
        credential_store = CredentialStore()
        credential_store.set_vaultwarden_credentials(
            email=payload.email,
            password=payload.password,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
        )

        logger.info(f"VaultWarden credentials stored for: {payload.email}")

        updated = False
        if not config.passwords.enabled:
            config.passwords.enabled = True
            updated = True
        if config.passwords.provider != "vaultwarden":
            config.passwords.provider = "vaultwarden"
            updated = True
        if config.passwords.vaultwarden_email != payload.email:
            config.passwords.vaultwarden_email = payload.email
            updated = True
        if payload.url:
            config.passwords.vaultwarden_url = payload.url
            updated = True

        if updated:
            try:
                config.save_to_file(config.default_config_path)
                from icloudbridge.api.dependencies import get_config

                get_config.cache_clear()
                logger.info("Passwords configuration updated with VaultWarden email")
            except Exception as exc:
                logger.warning("Failed to persist VaultWarden email in config: %s", exc)

        return {
            "status": "success",
            "message": f"Credentials stored securely for {payload.email}",
        }
    except Exception as e:
        logger.error(f"Failed to store VaultWarden credentials: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store credentials: {str(e)}"
        )


@router.delete("/vaultwarden/credentials")
async def delete_vaultwarden_credentials(email: str, config: ConfigDep):
    """Delete VaultWarden credentials from system keyring.

    Args:
        email: VaultWarden email

    Returns:
        Success message
    """
    try:
        credential_store = CredentialStore()
        credential_store.delete_vaultwarden_credentials(email)

        logger.info(f"VaultWarden credentials deleted for: {email}")

        if config.passwords.vaultwarden_email == email:
            config.passwords.vaultwarden_email = None
            try:
                config.save_to_file(config.default_config_path)
                from icloudbridge.api.dependencies import get_config

                get_config.cache_clear()
            except Exception as exc:
                logger.warning("Failed to persist VaultWarden email removal: %s", exc)

        return {
            "status": "success",
            "message": f"Credentials deleted for {email}",
        }
    except Exception as e:
        logger.error(f"Failed to delete VaultWarden credentials: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete credentials: {str(e)}"
        )


@router.post("/nextcloud/credentials")
async def set_nextcloud_credentials(
    payload: NextcloudCredentialRequest,
    config: ConfigDep,
):
    """Store Nextcloud Passwords credentials in system keyring."""

    try:
        credential_store = CredentialStore()
        credential_store.set_nextcloud_credentials(payload.username, payload.app_password)

        logger.info(f"Nextcloud credentials stored for: {payload.username}")

        updated = False
        if not config.passwords.enabled:
            config.passwords.enabled = True
            updated = True
        if config.passwords.provider != "nextcloud":
            config.passwords.provider = "nextcloud"
            updated = True
        if config.passwords.nextcloud_username != payload.username:
            config.passwords.nextcloud_username = payload.username
            updated = True
        if payload.url:
            config.passwords.nextcloud_url = payload.url
            updated = True

        if updated:
            try:
                config.save_to_file(config.default_config_path)
                from icloudbridge.api.dependencies import get_config

                get_config.cache_clear()
                logger.info("Passwords configuration updated with Nextcloud settings")
            except Exception as exc:
                logger.warning("Failed to persist Nextcloud configuration: %s", exc)

        return {
            "status": "success",
            "message": f"Credentials stored securely for {payload.username}",
        }
    except Exception as e:
        logger.error(f"Failed to store Nextcloud credentials: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store credentials: {str(e)}"
        )


@router.delete("/nextcloud/credentials")
async def delete_nextcloud_credentials(username: str, config: ConfigDep):
    """Delete Nextcloud credentials from system keyring."""

    try:
        credential_store = CredentialStore()
        deleted = credential_store.delete_nextcloud_credentials(username)

        if config.passwords.nextcloud_username == username:
            config.passwords.nextcloud_username = None
            try:
                config.save_to_file(config.default_config_path)
                from icloudbridge.api.dependencies import get_config

                get_config.cache_clear()
            except Exception as exc:
                logger.warning("Failed to persist Nextcloud username removal: %s", exc)

        if deleted:
            return {
                "status": "success",
                "message": f"Credentials deleted for {username}",
            }
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No credentials found to delete",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete Nextcloud credentials: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete credentials: {str(e)}"
        )
