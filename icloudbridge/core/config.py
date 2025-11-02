"""Configuration management using Pydantic Settings."""

import logging
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class FolderConfig(BaseSettings):
    """Configuration for a single note folder."""

    enabled: bool = True


class ListConfig(BaseSettings):
    """Configuration for a single reminder list."""

    enabled: bool = True
    calendar: str | None = None


class NotesConfig(BaseSettings):
    """Configuration for Notes synchronization."""

    enabled: bool = True
    remote_folder: Path | None = None
    folders: dict[str, FolderConfig] = Field(default_factory=dict)

    @field_validator("remote_folder", mode="before")
    @classmethod
    def expand_path(cls, v: str | None) -> Path | None:
        """Expand user home directory in paths."""
        if v is None:
            return None
        return Path(v).expanduser().resolve()


class RemindersConfig(BaseSettings):
    """Configuration for Reminders synchronization."""

    enabled: bool = True
    caldav_url: str | None = None
    caldav_username: str | None = None
    caldav_password: str | None = None
    caldav_path: str = "/remote.php/dav/calendars/{username}/"

    # Sync mode: "auto" (sync all lists) or "manual" (only specified mappings)
    sync_mode: str = "auto"

    # Calendar mappings: Apple Reminders list → CalDAV calendar
    # Default: {"Reminders": "tasks"}
    calendar_mappings: dict[str, str] = Field(
        default_factory=lambda: {"Reminders": "tasks"}
    )

    # Legacy fields for backward compatibility (deprecated)
    apple_calendar: str | None = None
    caldav_calendar: str | None = None
    lists: dict[str, ListConfig] = Field(default_factory=dict)

    @field_validator("caldav_url", mode="before")
    @classmethod
    def validate_url(cls, v: str | None) -> str | None:
        """Validate CalDAV URL format."""
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("CalDAV URL must start with http:// or https://")
        return v

    @field_validator("sync_mode", mode="before")
    @classmethod
    def validate_sync_mode(cls, v: str) -> str:
        """Validate sync mode."""
        valid_modes = {"auto", "manual"}
        v = v.lower()
        if v not in valid_modes:
            raise ValueError(f"Sync mode must be one of: {', '.join(valid_modes)}")
        return v

    def get_caldav_password(self) -> str | None:
        """
        Get CalDAV password from keyring or config.

        Priority:
        1. System keyring (if username is configured)
        2. Config/environment variable (fallback)

        Returns:
            Password if found, None otherwise
        """
        # Try keyring first (most secure)
        if self.caldav_username:
            try:
                from icloudbridge.utils.credentials import CredentialStore

                cred_store = CredentialStore()
                password = cred_store.get_caldav_password(self.caldav_username)
                if password:
                    logger.debug("Using CalDAV password from system keyring")
                    return password
            except Exception as e:
                logger.warning(f"Failed to retrieve password from keyring: {e}")

        # Fallback to config/env var
        if self.caldav_password:
            logger.debug("Using CalDAV password from config/environment")
            return self.caldav_password

        return None


class GeneralConfig(BaseSettings):
    """General application configuration."""

    log_level: str = "INFO"
    data_dir: Path = Field(
        default_factory=lambda: Path.home() / "Library" / "Application Support" / "iCloudBridge"
    )
    config_file: Path | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Log level must be one of: {', '.join(valid_levels)}")
        return v

    @field_validator("data_dir", mode="before")
    @classmethod
    def expand_data_dir(cls, v: str | Path) -> Path:
        """Expand user home directory in data directory path."""
        return Path(v).expanduser().resolve()


class AppConfig(BaseSettings):
    """Main application configuration."""

    model_config = SettingsConfigDict(
        env_prefix="ICLOUDBRIDGE_",
        env_nested_delimiter="__",
        case_sensitive=False,
    )

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    notes: NotesConfig = Field(default_factory=NotesConfig)
    reminders: RemindersConfig = Field(default_factory=RemindersConfig)

    @classmethod
    def load_from_file(cls, config_path: Path) -> "AppConfig":
        """Load configuration from a TOML file."""
        if not config_path.exists():
            logger.warning(f"Config file not found: {config_path}, using defaults")
            return cls()

        try:
            import tomllib
        except ImportError:
            # Python < 3.11
            import tomli as tomllib  # type: ignore

        with open(config_path, "rb") as f:
            config_dict = tomllib.load(f)

        return cls(**config_dict)

    def save_to_file(self, config_path: Path) -> None:
        """Save configuration to a TOML file."""
        try:
            import tomli_w
        except ImportError as e:
            logger.error("tomli_w not installed, cannot save config")
            raise ImportError("Install tomli_w to save configuration: pip install tomli-w") from e

        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert to dict, handling Path objects and excluding None values
        config_dict = self.model_dump(mode="json", exclude_none=True)

        with open(config_path, "wb") as f:
            tomli_w.dump(config_dict, f)

        logger.info(f"Configuration saved to {config_path}")

    def ensure_data_dir(self) -> None:
        """Ensure data directory exists."""
        self.general.data_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Data directory: {self.general.data_dir}")

    @property
    def db_path(self) -> Path:
        """Get path to SQLite database."""
        return self.general.data_dir / "icloudbridge.db"

    @property
    def default_config_path(self) -> Path:
        """Get default configuration file path."""
        return self.general.data_dir / "config.toml"


# Global configuration instance
_config: AppConfig | None = None


def get_config() -> AppConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = AppConfig()
        _config.ensure_data_dir()
    return _config


def set_config(config: AppConfig) -> None:
    """Set the global configuration instance."""
    global _config
    _config = config
    _config.ensure_data_dir()


def load_config(config_path: Path | None = None) -> AppConfig:
    """Load configuration from file or create default."""
    if config_path is None:
        config = AppConfig()
        config_path = config.default_config_path

    if config_path.exists():
        config = AppConfig.load_from_file(config_path)
    else:
        config = AppConfig()

    config.general.config_file = config_path
    set_config(config)
    return config
