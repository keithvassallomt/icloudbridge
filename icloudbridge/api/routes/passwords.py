"""Passwords synchronization endpoints."""

import json
import logging
import tempfile
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from icloudbridge.api.dependencies import ConfigDep, PasswordsDBDep, PasswordsSyncEngineDep
from icloudbridge.api.models import PasswordsSyncRequest
from icloudbridge.sources.passwords.vaultwarden_api import VaultwardenAPIClient
from icloudbridge.utils.credentials import CredentialStore
from icloudbridge.utils.db import SyncLogsDB

logger = logging.getLogger(__name__)

router = APIRouter()


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
):
    """Full auto-sync: Apple ↔ VaultWarden (push & pull).

    Args:
        file: Apple Passwords CSV export

    Returns:
        Sync results with push and pull statistics
    """
    # Create sync log entry
    sync_logs_db = SyncLogsDB(config.general.data_dir / "sync_logs.db")
    await sync_logs_db.initialize()

    log_id = await sync_logs_db.create_log(
        service="passwords",
        sync_type="manual",
        status="running",
    )

    start_time = time.time()

    try:
        # Save uploaded file to temporary location
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.csv') as tmp:
            content = await file.read()
            tmp.write(content)
            apple_csv_path = tmp.name

        # Get VaultWarden credentials
        credential_store = CredentialStore()
        credentials = credential_store.get_vaultwarden_credentials(config.passwords.vaultwarden_email)

        if not credentials:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="VaultWarden credentials not found. Please configure them first."
            )

        # Create VaultWarden client
        vw_client = VaultwardenAPIClient(
            config.passwords.vaultwarden_url,
            credentials['email'],
            credentials['password'],
            credentials.get('client_id'),
            credentials.get('client_secret'),
        )

        # Output path for pull
        output_apple_csv = Path(tempfile.gettempdir()) / "apple_import.csv"

        # Perform sync
        result = await engine.sync(
            apple_csv_path=apple_csv_path,
            vaultwarden_client=vw_client,
            output_apple_csv=str(output_apple_csv),
        )

        # Clean up uploaded file
        Path(apple_csv_path).unlink()

        duration = time.time() - start_time

        # Update sync log with success
        await sync_logs_db.update_log(
            log_id=log_id,
            status="completed",
            duration_seconds=round(duration, 0),
            stats_json=json.dumps(result),
        )

        return {
            "status": "success",
            "duration_seconds": duration,
            "stats": result,
            "apple_import_csv": str(output_apple_csv) if output_apple_csv.exists() else None,
        }

    except Exception as e:
        duration = time.time() - start_time
        error_msg = str(e)

        logger.error(f"Passwords sync failed: {error_msg}")

        # Clean up on error
        if 'apple_csv_path' in locals():
            Path(apple_csv_path).unlink(missing_ok=True)

        # Update sync log with error
        await sync_logs_db.update_log(
            log_id=log_id,
            status="failed",
            duration_seconds=round(duration, 0),
            error_message=error_msg,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Sync failed: {error_msg}"
        )


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
            push_stats = sync_stats.get("push", {})
            pull_stats = sync_stats.get("pull", {})
            msg_parts = []
            if push_stats.get("pushed", 0) > 0:
                msg_parts.append(f"pushed {push_stats['pushed']} to VaultWarden")
            if pull_stats.get("pulled", 0) > 0:
                msg_parts.append(f"pulled {pull_stats['pulled']} from VaultWarden")

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

    # Check if credentials are available
    credential_store = CredentialStore()
    has_credentials = credential_store.has_vaultwarden_credentials(config.passwords.vaultwarden_email or "")

    return {
        "enabled": config.passwords.enabled,
        "vaultwarden_url": config.passwords.vaultwarden_url,
        "vaultwarden_email": config.passwords.vaultwarden_email,
        "has_credentials": has_credentials,
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
            push_stats = stats.get("push", {})
            pull_stats = stats.get("pull", {})
            msg_parts = []
            if push_stats.get("pushed", 0) > 0:
                msg_parts.append(f"pushed {push_stats['pushed']} to VaultWarden")
            if pull_stats.get("pulled", 0) > 0:
                msg_parts.append(f"pulled {pull_stats['pulled']} from VaultWarden")

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
async def reset_database(passwords_db: PasswordsDBDep):
    """Reset passwords sync database.

    Clears all password entries from the database.

    Returns:
        Success message
    """
    try:
        await passwords_db.clear_all_entries()
        logger.info("Passwords database reset successfully")

        return {
            "status": "success",
            "message": "Passwords database reset successfully. All entries cleared.",
        }
    except Exception as e:
        logger.error(f"Failed to reset passwords database: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset database: {str(e)}"
        )


@router.post("/vaultwarden/credentials")
async def set_vaultwarden_credentials(
    email: str,
    password: str,
    client_id: str | None = None,
    client_secret: str | None = None,
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
            email=email,
            password=password,
            client_id=client_id,
            client_secret=client_secret,
        )

        logger.info(f"VaultWarden credentials stored for: {email}")

        return {
            "status": "success",
            "message": f"Credentials stored securely for {email}",
        }
    except Exception as e:
        logger.error(f"Failed to store VaultWarden credentials: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store credentials: {str(e)}"
        )


@router.delete("/vaultwarden/credentials")
async def delete_vaultwarden_credentials(email: str):
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
