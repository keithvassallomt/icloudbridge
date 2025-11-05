"""Simple settings database for storing runtime configuration."""

import sqlite3
from pathlib import Path


class SettingsDB:
    """Manages persistent settings in SQLite."""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            # Store in system temp or home directory
            db_path = Path.home() / ".icloudbridge_settings.db"

        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize the database schema."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.commit()

    def set(self, key: str, value: str):
        """Set a setting value."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, value)
            )
            conn.commit()

    def get(self, key: str) -> str | None:
        """Get a setting value."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row[0] if row else None

    def delete(self, key: str):
        """Delete a setting."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            conn.commit()


# Global instance
_settings_db = None


def get_settings_db() -> SettingsDB:
    """Get the global settings database instance."""
    global _settings_db
    if _settings_db is None:
        _settings_db = SettingsDB()
    return _settings_db


def get_config_path() -> Path | None:
    """Get the config file path from settings."""
    db = get_settings_db()
    path_str = db.get("config_file_path")
    return Path(path_str) if path_str else None


def set_config_path(path: Path):
    """Set the config file path in settings."""
    db = get_settings_db()
    db.set("config_file_path", str(path))
