"""Pydantic models for API request/response validation."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from icloudbridge.core.models import SyncStatus


class SyncRequest(BaseModel):
    """Request model for synchronization operations."""

    dry_run: bool = Field(
        default=False,
        description="Preview changes without applying them",
    )
    force: bool = Field(
        default=False,
        description="Force sync even if no changes detected",
    )


class SyncResponse(BaseModel):
    """Response model for synchronization operations."""

    status: SyncStatus
    items_synced: int = 0
    items_created: int = 0
    items_updated: int = 0
    items_deleted: int = 0
    errors: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)

    class Config:
        from_attributes = True


class NoteFolderResponse(BaseModel):
    """Response model for note folder information."""

    uuid: str
    name: str
    note_count: int = 0
    last_sync: datetime | None = None
    enabled: bool = True

    class Config:
        from_attributes = True


class ReminderListResponse(BaseModel):
    """Response model for reminder list information."""

    uuid: str
    name: str
    reminder_count: int = 0
    last_sync: datetime | None = None
    enabled: bool = True

    class Config:
        from_attributes = True


class StatusResponse(BaseModel):
    """Response model for overall sync status."""

    notes: dict[str, Any]
    reminders: dict[str, Any]
    passwords: dict[str, Any]
    photos: dict[str, Any] | None = None
    scheduler_running: bool = False
    active_schedules: int = 0


class HealthResponse(BaseModel):
    """Response model for health check."""

    status: str = "healthy"
    timestamp: str


class VersionResponse(BaseModel):
    """Response model for version information."""

    version: str
    python_version: str


class ConfigResponse(BaseModel):
    """Response model for configuration."""

    data_dir: str
    config_file: str | None = None
    notes_enabled: bool
    reminders_enabled: bool
    passwords_enabled: bool
    photos_enabled: bool
    notes_remote_folder: str | None = None
    notes_folder_mappings: dict[str, dict[str, str]] = Field(default_factory=dict)
    reminders_caldav_url: str | None = None
    reminders_caldav_username: str | None = None
    reminders_sync_mode: str | None = None
    reminders_calendar_mappings: dict[str, str] = Field(default_factory=dict)
    passwords_provider: str | None = None
    passwords_vaultwarden_url: str | None = None
    passwords_vaultwarden_email: str | None = None
    passwords_nextcloud_url: str | None = None
    passwords_nextcloud_username: str | None = None
    photos_default_album: str | None = None
    photo_sources: dict[str, dict[str, str | bool]] = Field(default_factory=dict)


class ConfigUpdateRequest(BaseModel):
    """Request model for configuration updates."""

    data_dir: str | None = None
    notes_enabled: bool | None = None
    reminders_enabled: bool | None = None
    passwords_enabled: bool | None = None
    photos_enabled: bool | None = None
    notes_remote_folder: str | None = None
    notes_folder_mappings: dict[str, dict[str, str]] | None = None
    reminders_caldav_url: str | None = None
    reminders_caldav_username: str | None = None
    reminders_caldav_password: str | None = Field(
        default=None,
        description="Password will be stored in system keyring",
    )
    reminders_sync_mode: str | None = None
    reminders_calendar_mappings: dict[str, str] | None = None
    passwords_provider: str | None = None
    passwords_vaultwarden_url: str | None = None
    passwords_vaultwarden_email: str | None = None
    passwords_vaultwarden_password: str | None = Field(
        default=None,
        description="Password will be stored in system keyring",
    )
    passwords_nextcloud_url: str | None = None
    passwords_nextcloud_username: str | None = None
    passwords_nextcloud_app_password: str | None = Field(
        default=None,
        description="Nextcloud app password will be stored in system keyring",
    )
    photos_default_album: str | None = None
    photo_sources: dict[str, dict[str, str | bool]] | None = None


class ErrorResponse(BaseModel):
    """Response model for errors."""

    detail: str
    error_type: str = "error"
    timestamp: datetime = Field(default_factory=datetime.now)


# Additional models for web UI

class NotesSyncRequest(BaseModel):
    """Request model for notes sync."""

    folder: str | None = Field(
        default=None,
        description="Specific folder to sync, or None to sync all folders"
    )
    mode: str = Field(
        default="bidirectional",
        description="Sync direction: 'import' (Markdown → Apple Notes), 'export' (Apple Notes → Markdown), or 'bidirectional' (both ways)"
    )
    dry_run: bool = Field(
        default=False,
        description="Preview changes without applying them"
    )
    skip_deletions: bool = Field(
        default=False,
        description="Skip all deletion operations"
    )
    deletion_threshold: int = Field(
        default=5,
        description="Max deletions before confirmation (-1 to disable)"
    )
    rich_notes_export: bool = Field(
        default=False,
        description="Export read-only rich notes snapshot after sync"
    )
    use_shortcuts: bool | None = Field(
        default=None,
        description="Override shortcut pipeline preference (None = use config default, True/False = override)"
    )


class NotesAllFoldersResponse(BaseModel):
    """Response model for all folders (both Apple Notes and Markdown)."""

    folders: dict[str, dict[str, bool]] = Field(
        description="Dictionary mapping folder paths to source indicators"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "folders": {
                    "Work": {"apple": True, "markdown": True},
                    "Work/Projects": {"apple": True, "markdown": False},
                    "Personal": {"apple": True, "markdown": True},
                    "Configs": {"apple": False, "markdown": True}
                }
            }
        }


class RemindersSyncRequest(BaseModel):
    """Request model for reminders sync."""

    apple_calendar: str | None = None
    caldav_calendar: str | None = None
    auto: bool = True
    dry_run: bool = False
    skip_deletions: bool = False
    deletion_threshold: int = 5


class PhotoSyncRequest(BaseModel):
    """Request model for photos sync."""

    sources: list[str] | None = Field(
        default=None,
        description="Optional list of configured source keys to limit the scan",
    )
    dry_run: bool = Field(default=False, description="Preview imports without sending to Photos")
    initial_scan: bool = Field(default=False, description="Initial scan to build database without importing")
    skip_deletions: bool = False
    deletion_threshold: int = 5


class PasswordsSyncRequest(BaseModel):
    """Request model for passwords sync."""

    apple_csv_path: str | None = None
    output_apple_csv: str | None = None


class VaultwardenCredentialRequest(BaseModel):
    """Request body for storing VaultWarden credentials."""

    email: EmailStr
    password: str
    client_id: str | None = None
    client_secret: str | None = None
    url: str | None = None


class NextcloudCredentialRequest(BaseModel):
    """Request body for storing Nextcloud Passwords credentials."""

    username: str
    app_password: str
    url: str | None = None


class ScheduleCreate(BaseModel):
    """Request model for creating a schedule."""

    services: list[str] = Field(..., description="List of services to sync (notes, reminders, photos)")
    # Deprecated legacy field maintained for backwards compatibility with older clients
    service: str | None = Field(
        default=None,
        description="Deprecated single-service field. Prefer 'services'."
    )
    name: str = Field(..., description="User-friendly schedule name")
    schedule_type: str = Field(..., description="Schedule type (interval or datetime)")
    interval_minutes: int | None = Field(None, description="Interval in minutes")
    cron_expression: str | None = Field(None, description="Cron expression")
    config_json: str | dict | None = Field(None, description="JSON sync configuration")
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    """Request model for updating a schedule."""

    name: str | None = None
    enabled: bool | None = None
    schedule_type: str | None = None
    interval_minutes: int | None = None
    cron_expression: str | None = None
    config_json: str | dict | None = None
    services: list[str] | None = None


class ScheduleResponse(BaseModel):
    """Response model for schedule information."""

    id: int
    service: str
    services: list[str]
    name: str
    enabled: bool
    schedule_type: str
    interval_minutes: int | None
    cron_expression: str | None
    next_run: str | None
    last_run: str | None
    config_json: str | None
    created_at: str
    updated_at: str


class SettingUpdate(BaseModel):
    """Request model for updating settings."""

    key: str
    value: str


class ShortcutStatus(BaseModel):
    """Status of a required shortcut."""

    name: str
    installed: bool
    url: str


class FullDiskAccessStatus(BaseModel):
    """Status of Full Disk Access for Python."""

    has_access: bool
    python_path: str
    notes_db_path: str | None = None


class NotesFolderStatus(BaseModel):
    """Status of the notes folder."""

    exists: bool
    writable: bool
    path: str | None = None


class SetupVerificationResponse(BaseModel):
    """Response model for setup verification."""

    shortcuts: list[ShortcutStatus]
    full_disk_access: FullDiskAccessStatus
    notes_folder: NotesFolderStatus
    is_localhost: bool
    all_ready: bool
