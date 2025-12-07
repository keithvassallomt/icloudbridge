"""Data models for password entries."""

import hashlib
from dataclasses import dataclass, field


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
        extra_urls: Additional URLs that point to the same login
        provider_id: Provider-specific ID (e.g., VaultWarden cipher ID)
    """

    title: str
    username: str
    password: str
    url: str | None = None
    notes: str | None = None
    otp_auth: str | None = None
    folder: str | None = None
    extra_urls: list[str] = field(default_factory=list)
    provider_id: str | None = None

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

    def add_url(self, url: str) -> None:
        """Add an additional URL if not already tracked."""

        if not url:
            return

        url = url.strip()
        if not url:
            return

        if not self.url:
            self.url = url
            return

        if url == self.url:
            return

        if url not in self.extra_urls:
            self.extra_urls.append(url)

    def get_all_urls(self) -> list[str]:
        """Return list of all URLs associated with the entry."""

        urls = []
        if self.url:
            urls.append(self.url)
        for extra in self.extra_urls:
            if extra and extra not in urls:
                urls.append(extra)
        return urls

    def __eq__(self, other):
        """Check equality based on dedup key."""
        if not isinstance(other, PasswordEntry):
            return False
        return self.get_dedup_key() == other.get_dedup_key()

    def __hash__(self):
        """Hash based on dedup key."""
        return hash(self.get_dedup_key())
