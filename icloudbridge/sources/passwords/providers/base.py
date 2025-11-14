"""Base class for password provider implementations."""

from abc import ABC, abstractmethod
from typing import Any

from ..models import PasswordEntry


class PasswordProviderBase(ABC):
    """
    Abstract base class for password sync providers.

    All password providers (Vaultwarden, Nextcloud, etc.) must implement this interface.
    """

    @abstractmethod
    async def authenticate(self) -> None:
        """
        Authenticate with the password provider service.

        Raises:
            Exception: If authentication fails
        """
        pass

    @abstractmethod
    async def list_passwords(self) -> list[dict[str, Any]]:
        """
        List all passwords from the provider.

        Returns:
            List of password dictionaries in provider-specific format
        """
        pass

    @abstractmethod
    async def get_password(self, password_id: str) -> dict[str, Any] | None:
        """
        Get a specific password by ID.

        Args:
            password_id: Provider-specific password identifier

        Returns:
            Password dictionary or None if not found
        """
        pass

    @abstractmethod
    async def create_password(self, entry: PasswordEntry) -> str:
        """
        Create a new password entry.

        Args:
            entry: PasswordEntry to create

        Returns:
            Provider-specific password ID
        """
        pass

    @abstractmethod
    async def update_password(self, password_id: str, entry: PasswordEntry) -> None:
        """
        Update an existing password entry.

        Args:
            password_id: Provider-specific password identifier
            entry: Updated PasswordEntry
        """
        pass

    @abstractmethod
    async def delete_password(self, password_id: str) -> None:
        """
        Delete a password entry.

        Args:
            password_id: Provider-specific password identifier
        """
        pass

    @abstractmethod
    async def list_folders(self) -> list[dict[str, Any]]:
        """
        List all folders from the provider.

        Returns:
            List of folder dictionaries in provider-specific format
        """
        pass

    @abstractmethod
    async def create_folder(self, name: str, parent_id: str | None = None) -> str:
        """
        Create a new folder.

        Args:
            name: Folder name
            parent_id: Optional parent folder ID

        Returns:
            Provider-specific folder ID
        """
        pass

    @abstractmethod
    async def get_folder_id(self, name: str) -> str | None:
        """
        Get folder ID by name.

        Args:
            name: Folder name

        Returns:
            Provider-specific folder ID or None if not found
        """
        pass

    @abstractmethod
    async def bulk_import(self, entries: list[PasswordEntry]) -> dict[str, int]:
        """
        Import multiple password entries in bulk (if supported).

        Args:
            entries: List of PasswordEntry objects to import

        Returns:
            Statistics dictionary with 'created' and 'failed' counts
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """
        Close any open connections and clean up resources.
        """
        pass
