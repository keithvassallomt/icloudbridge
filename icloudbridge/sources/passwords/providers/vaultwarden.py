"""Vaultwarden/Bitwarden password provider implementation."""

import logging
from typing import Any

from ..models import PasswordEntry
from ..vaultwarden_api import VaultwardenAPIClient
from .base import PasswordProviderBase

logger = logging.getLogger(__name__)


class VaultwardenProvider(PasswordProviderBase):
    """
    Password provider for Vaultwarden/Bitwarden servers.

    This is a thin adapter around VaultwardenAPIClient to implement
    the PasswordProviderBase interface.
    """

    def __init__(
        self,
        url: str,
        email: str,
        password: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        ssl_verify_cert: bool | str = True,
    ):
        """
        Initialize Vaultwarden provider.

        Args:
            url: Vaultwarden server URL
            email: User email
            password: Master password
            client_id: OAuth client ID (optional)
            client_secret: OAuth client secret (optional)
            ssl_verify_cert: SSL verification flag or CA bundle path
        """
        self.client = VaultwardenAPIClient(
            url=url,
            email=email,
            password=password,
            client_id=client_id,
            client_secret=client_secret,
            ssl_verify_cert=ssl_verify_cert,
        )
        self._folder_cache: dict[str, str] = {}  # name -> id mapping

    async def authenticate(self) -> None:
        """Authenticate with Vaultwarden server."""
        await self.client.authenticate()
        # Build folder cache
        folders = await self.client.list_folders()
        self._folder_cache = {folder["name"]: folder["id"] for folder in folders}

    async def list_passwords(self) -> list[dict[str, Any]]:
        """
        List all passwords from Vaultwarden.

        Returns:
            List of password dictionaries with keys:
            - id: Cipher ID
            - title: Entry title
            - username: Username
            - url: Primary URL
            - folder: Folder name
            - notes: Notes
            - otp_auth: TOTP secret
        """
        entries = await self.client.pull_passwords()
        return [
            {
                "id": entry.provider_id,
                "title": entry.title,
                "username": entry.username,
                "password": entry.password,
                "url": entry.url,
                "folder": entry.folder,
                "notes": entry.notes,
                "otp_auth": entry.otp_auth,
            }
            for entry in entries
        ]

    async def get_password(self, password_id: str) -> dict[str, Any] | None:
        """
        Get a specific password by ID.

        Note: VaultwardenAPIClient doesn't implement individual password retrieval,
        so this method is not fully supported.

        Args:
            password_id: Cipher ID

        Returns:
            None (not implemented)
        """
        logger.warning("get_password() not implemented for Vaultwarden provider")
        return None

    async def create_password(self, entry: PasswordEntry) -> str:
        """
        Create a new password entry in Vaultwarden.

        Args:
            entry: PasswordEntry to create

        Returns:
            Empty string (VaultwardenAPIClient doesn't return IDs)
        """
        # Ensure folder exists
        if entry.folder:
            await self._ensure_folder_exists(entry.folder)

        # Use push_passwords with a single entry
        await self.client.push_passwords(
            entries=[entry],
            dry_run=False,
            use_bulk=False,
        )
        return ""

    async def update_password(self, password_id: str, entry: PasswordEntry) -> bool:
        """
        Update an existing password entry.

        Args:
            password_id: Cipher ID
            entry: Updated PasswordEntry

        Returns:
            True if successful, False otherwise
        """
        return await self.client.update_password(password_id, entry)

    async def delete_password(self, password_id: str) -> bool:
        """
        Delete a password entry (move to trash).

        Args:
            password_id: Cipher ID

        Returns:
            True if successful, False otherwise
        """
        return await self.client.delete_password(password_id, soft_delete=True)

    async def list_folders(self) -> list[dict[str, Any]]:
        """
        List all folders from Vaultwarden.

        Returns:
            List of folder dictionaries with keys 'id' and 'name'
        """
        return await self.client.list_folders()

    async def create_folder(self, name: str, parent_id: str | None = None) -> str:
        """
        Create a new folder in Vaultwarden.

        Note: Vaultwarden doesn't support nested folders, parent_id is ignored.

        Args:
            name: Folder name
            parent_id: Ignored

        Returns:
            Folder ID
        """
        folder_id = await self.client.create_folder(name)
        self._folder_cache[name] = folder_id
        return folder_id

    async def get_folder_id(self, name: str) -> str | None:
        """
        Get folder ID by name.

        Args:
            name: Folder name

        Returns:
            Folder ID or None if not found
        """
        # Check cache first
        if name in self._folder_cache:
            return self._folder_cache[name]

        # Refresh cache
        folders = await self.client.list_folders()
        self._folder_cache = {folder["name"]: folder["id"] for folder in folders}

        return self._folder_cache.get(name)

    async def _ensure_folder_exists(self, name: str) -> str:
        """
        Ensure a folder exists, creating it if necessary.

        Args:
            name: Folder name

        Returns:
            Folder ID
        """
        folder_id = await self.get_folder_id(name)
        if folder_id:
            return folder_id

        logger.info(f"Creating folder: {name}")
        return await self.create_folder(name)

    async def bulk_import(self, entries: list[PasswordEntry]) -> dict[str, int]:
        """
        Import multiple password entries in bulk.

        Args:
            entries: List of PasswordEntry objects

        Returns:
            Statistics dictionary with 'created' and 'failed' counts
        """
        # Ensure all folders exist
        folders = {entry.folder for entry in entries if entry.folder}
        for folder in folders:
            await self._ensure_folder_exists(folder)

        # Use Vaultwarden bulk import
        try:
            await self.client.push_passwords(
                entries=entries,
                dry_run=False,
                use_bulk=True,
            )
            return {"created": len(entries), "failed": 0}
        except Exception as e:
            logger.error(f"Bulk import failed: {e}")
            return {"created": 0, "failed": len(entries)}

    async def close(self) -> None:
        """Close HTTP client connections."""
        await self.client.close()
