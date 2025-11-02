"""Data models for password entries."""

import hashlib
from dataclasses import dataclass


@dataclass
class PasswordEntry:
    """
    Represents a password entry from any source.

    Attributes:
        title: Entry title/name
        username: Username or email
        password: Plaintext password (ephemeral - never stored)
        url: Associated URL/website
        notes: Additional notes
        otp_auth: OTP/TOTP secret (otpauth:// URL)
        folder: Folder or collection name
    """

    title: str
    username: str
    password: str
    url: str | None = None
    notes: str | None = None
    otp_auth: str | None = None
    folder: str | None = None

    def get_password_hash(self) -> str:
        """
        Generate SHA-256 hash of the password.

        Returns:
            Hexadecimal hash string
        """
        return hashlib.sha256(self.password.encode("utf-8")).hexdigest()

    def get_dedup_key(self) -> tuple[str, str | None, str]:
        """
        Get a unique key for deduplication.

        Returns:
            Tuple of (title_lower, url_lower, username_lower)
        """
        return (
            self.title.lower().strip(),
            self.url.lower().strip() if self.url else None,
            self.username.lower().strip(),
        )

    def __eq__(self, other):
        """Check equality based on dedup key."""
        if not isinstance(other, PasswordEntry):
            return False
        return self.get_dedup_key() == other.get_dedup_key()

    def __hash__(self):
        """Hash based on dedup key."""
        return hash(self.get_dedup_key())
