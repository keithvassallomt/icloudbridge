"""Nextcloud Passwords provider implementation."""

import json
import logging
import re
from typing import Any

import httpx

from ..models import PasswordEntry
from .base import PasswordProviderBase

logger = logging.getLogger(__name__)


_ICB_FOLDER_TAG = re.compile(r"#icb_([A-Za-z0-9_-]+)")


class NextcloudPasswordsProvider(PasswordProviderBase):
    """
    Password provider for Nextcloud Passwords app.

    Uses the Nextcloud Passwords REST API with app password authentication.
    API Documentation: https://git.mdns.eu/nextcloud/passwords/-/wikis/
    """

    def __init__(
        self,
        url: str,
        username: str,
        app_password: str,
    ):
        """
        Initialize Nextcloud Passwords provider.

        Args:
            url: Nextcloud server URL (e.g., https://cloud.example.com)
            username: Nextcloud username
            app_password: Nextcloud app password (not regular password)
        """
        self.url = url.rstrip("/")
        self.username = username
        self.app_password = app_password
        self.api_base = f"{self.url}/index.php/apps/passwords/api/1.0"

        # HTTP Basic Auth
        self._client = httpx.AsyncClient(
            auth=(username, app_password),
            timeout=30.0,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "OCS-APIRequest": "true",  # Required for Nextcloud APIs
            },
        )

        self._folder_cache: dict[str, str] = {}  # name -> uuid mapping
        self._folder_id_cache: dict[str, dict] = {}  # uuid -> folder data

    async def authenticate(self) -> None:
        """
        Verify authentication with Nextcloud Passwords.

        Tests the connection by attempting to list passwords.
        """
        logger.info(f"Authenticating with Nextcloud at {self.url}")

        try:
            # Test authentication by listing passwords (limit to 1)
            response = await self._client.get(
                f"{self.api_base}/password/list",
                params={"details": "model"},
            )
            response.raise_for_status()

            logger.info("Successfully authenticated with Nextcloud Passwords")

            # Build folder cache
            await self._refresh_folder_cache()

        except httpx.HTTPStatusError as e:
            logger.error(f"Nextcloud authentication failed: HTTP {e.response.status_code}")
            logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to authenticate with Nextcloud: {e}") from e
        except Exception as e:
            logger.error(f"Nextcloud authentication failed: {e}")
            raise Exception(f"Failed to authenticate with Nextcloud: {e}") from e

    async def _refresh_folder_cache(self) -> None:
        """Refresh the folder cache by fetching all folders."""
        try:
            folders = await self.list_folders()
            self._folder_cache = {folder["label"]: folder["id"] for folder in folders}
            self._folder_id_cache = {folder["id"]: folder for folder in folders}
        except Exception as e:
            logger.warning(f"Failed to refresh folder cache: {e}")

    async def list_passwords(self) -> list[dict[str, Any]]:
        """
        List all passwords from Nextcloud.

        Returns:
            List of password dictionaries with keys:
            - id: Password UUID
            - label: Entry title
            - username: Username
            - url: Primary URL
            - folder: Folder UUID
            - folder_label: Folder name
            - notes: Notes (may be encrypted)
        """
        logger.debug("Listing passwords from Nextcloud")

        try:
            response = await self._client.get(
                f"{self.api_base}/password/list",
                params={"details": "model+folder"},  # Include folder details
            )
            response.raise_for_status()

            passwords = response.json()

            # Enrich with folder labels
            result = []
            for pwd in passwords:
                folder_value = pwd.get("folder")
                folder_uuid = None
                folder_label = None

                if isinstance(folder_value, dict):
                    folder_uuid = folder_value.get("id")
                    folder_label = folder_value.get("label")
                else:
                    folder_uuid = folder_value

                if folder_uuid and folder_uuid != "00000000-0000-0000-0000-000000000000":
                    if folder_label is None:
                        if folder_uuid in self._folder_id_cache:
                            folder_label = self._folder_id_cache[folder_uuid].get("label")
                        else:
                            try:
                                folder = await self._get_folder(folder_uuid)
                                if folder:
                                    folder_label = folder.get("label")
                                    self._folder_id_cache[folder_uuid] = folder
                            except Exception:
                                pass

                result.append(
                    {
                        "id": pwd.get("id"),
                        "label": pwd.get("label", ""),
                        "username": pwd.get("username", ""),
                        "url": pwd.get("url", ""),
                        "folder": folder_uuid,
                        "folder_label": folder_label,
                        "notes": pwd.get("notes", ""),
                        "password": pwd.get("password", ""),  # May be encrypted
                    }
                )

            logger.debug(f"Found {len(result)} passwords")
            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to list passwords: HTTP {e.response.status_code}")
            logger.error(f"Response: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to list passwords: {e}")
            raise

    async def get_password(self, password_id: str) -> dict[str, Any] | None:
        """
        Get a specific password by UUID.

        Args:
            password_id: Password UUID

        Returns:
            Password dictionary or None if not found
        """
        logger.debug(f"Fetching password {password_id}")

        try:
            response = await self._client.get(
                f"{self.api_base}/password/show",
                params={"id": password_id, "details": "model"},
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            logger.error(f"Failed to get password: HTTP {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"Failed to get password: {e}")
            raise

    async def create_password(self, entry: PasswordEntry) -> str:
        """
        Create a new password entry in Nextcloud.

        Args:
            entry: PasswordEntry to create

        Returns:
            Password UUID
        """
        logger.debug(f"Creating password: {entry.title}")

        # Ensure folder exists and get UUID
        folder_name = entry.folder
        if not folder_name and entry.notes:
            tag_match = _ICB_FOLDER_TAG.search(entry.notes)
            if tag_match:
                folder_name = tag_match.group(1)

        folder_uuid = "00000000-0000-0000-0000-000000000000"  # Default base folder
        if folder_name:
            folder_uuid = await self._ensure_folder_exists(folder_name)

        urls = entry.get_all_urls()
        primary_url = urls[0] if urls else (entry.url or "")
        extra_urls = urls[1:] if len(urls) > 1 else []

        payload = {
            "label": entry.title,
            "username": entry.username or "",
            "password": entry.password,
            "url": primary_url or "",
            "notes": entry.notes or "",
            "folder": folder_uuid,
        }

        custom_fields: list[dict[str, str]] = []

        for index, extra_url in enumerate(extra_urls, start=2):
            custom_fields.append(
                {"label": f"Website {index}", "value": extra_url, "type": "url"}
            )

        if entry.otp_auth and "secret=" in entry.otp_auth:
            secret = entry.otp_auth.split("secret=")[1].split("&")[0]
            custom_fields.append({"label": "TOTP", "value": secret, "type": "secret"})

        if custom_fields:
            payload["customFields"] = json.dumps(custom_fields)

        try:
            response = await self._client.post(
                f"{self.api_base}/password/create",
                json=payload,
            )
            response.raise_for_status()

            result = response.json()
            password_id = result.get("id")

            logger.debug(f"Created password {password_id}: {entry.title}")
            return password_id

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create password: HTTP {e.response.status_code}")
            logger.error(f"Response: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to create password: {e}")
            raise

    async def update_password(self, password_id: str, entry: PasswordEntry) -> None:
        """
        Update an existing password entry.

        Args:
            password_id: Password UUID
            entry: Updated PasswordEntry
        """
        logger.debug(f"Updating password {password_id}: {entry.title}")

        # Ensure folder exists and get UUID
        folder_name = entry.folder
        if not folder_name and entry.notes:
            tag_match = _ICB_FOLDER_TAG.search(entry.notes)
            if tag_match:
                folder_name = tag_match.group(1)

        folder_uuid = "00000000-0000-0000-0000-000000000000"
        if folder_name:
            folder_uuid = await self._ensure_folder_exists(folder_name)

        urls = entry.get_all_urls()
        primary_url = urls[0] if urls else (entry.url or "")
        extra_urls = urls[1:] if len(urls) > 1 else []

        payload = {
            "id": password_id,
            "label": entry.title,
            "username": entry.username or "",
            "password": entry.password,
            "url": primary_url or "",
            "notes": entry.notes or "",
            "folder": folder_uuid,
        }

        custom_fields: list[dict[str, str]] = []

        for index, extra_url in enumerate(extra_urls, start=2):
            custom_fields.append(
                {"label": f"Website {index}", "value": extra_url, "type": "url"}
            )

        if entry.otp_auth and "secret=" in entry.otp_auth:
            secret = entry.otp_auth.split("secret=")[1].split("&")[0]
            custom_fields.append({"label": "TOTP", "value": secret, "type": "secret"})

        if custom_fields:
            payload["customFields"] = json.dumps(custom_fields)

        try:
            response = await self._client.patch(
                f"{self.api_base}/password/update",
                json=payload,
            )
            response.raise_for_status()

            logger.debug(f"Updated password {password_id}")

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to update password: HTTP {e.response.status_code}")
            logger.error(f"Response: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to update password: {e}")
            raise

    async def delete_password(self, password_id: str) -> None:
        """
        Delete a password entry (moves to trash).

        Args:
            password_id: Password UUID
        """
        logger.debug(f"Deleting password {password_id}")

        try:
            response = await self._client.delete(
                f"{self.api_base}/password/delete",
                params={"id": password_id},
            )
            response.raise_for_status()

            logger.debug(f"Deleted password {password_id}")

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to delete password: HTTP {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"Failed to delete password: {e}")
            raise

    async def list_folders(self) -> list[dict[str, Any]]:
        """
        List all folders from Nextcloud.

        Returns:
            List of folder dictionaries with keys 'id', 'label', and 'parent'
        """
        logger.debug("Listing folders from Nextcloud")

        try:
            response = await self._client.get(
                f"{self.api_base}/folder/list",
                params={"details": "model"},
            )
            response.raise_for_status()

            folders = response.json()
            logger.debug(f"Found {len(folders)} folders")
            return folders

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to list folders: HTTP {e.response.status_code}")
            raise
        except Exception as e:
            logger.error(f"Failed to list folders: {e}")
            raise

    async def _get_folder(self, folder_id: str) -> dict[str, Any] | None:
        """
        Get a specific folder by UUID.

        Args:
            folder_id: Folder UUID

        Returns:
            Folder dictionary or None if not found
        """
        try:
            response = await self._client.get(
                f"{self.api_base}/folder/show",
                params={"id": folder_id, "details": "model"},
            )
            response.raise_for_status()
            return response.json()

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        except Exception as e:
            logger.error(f"Failed to get folder: {e}")
            return None

    async def create_folder(self, name: str, parent_id: str | None = None) -> str:
        """
        Create a new folder in Nextcloud.

        Args:
            name: Folder name (label)
            parent_id: Optional parent folder UUID

        Returns:
            Folder UUID
        """
        logger.debug(f"Creating folder: {name}")

        payload = {
            "label": name,
        }

        if parent_id:
            payload["parent"] = parent_id
        else:
            # Use base folder UUID
            payload["parent"] = "00000000-0000-0000-0000-000000000000"

        try:
            response = await self._client.post(
                f"{self.api_base}/folder/create",
                json=payload,
            )
            response.raise_for_status()

            result = response.json()
            folder_id = result.get("id")

            logger.debug(f"Created folder {folder_id}: {name}")

            # Update cache
            self._folder_cache[name] = folder_id
            self._folder_id_cache[folder_id] = result

            return folder_id

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create folder: HTTP {e.response.status_code}")
            logger.error(f"Response: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Failed to create folder: {e}")
            raise

    async def get_folder_id(self, name: str) -> str | None:
        """
        Get folder UUID by name (label).

        Args:
            name: Folder name

        Returns:
            Folder UUID or None if not found
        """
        # Check cache first
        if name in self._folder_cache:
            return self._folder_cache[name]

        # Refresh cache
        await self._refresh_folder_cache()

        return self._folder_cache.get(name)

    async def _ensure_folder_exists(self, name: str) -> str:
        """
        Ensure a folder exists, creating it if necessary.

        Args:
            name: Folder name

        Returns:
            Folder UUID
        """
        folder_id = await self.get_folder_id(name)
        if folder_id:
            return folder_id

        logger.info(f"Creating folder: {name}")
        return await self.create_folder(name)

    async def bulk_import(self, entries: list[PasswordEntry]) -> dict[str, int]:
        """
        Import multiple password entries.

        Note: Nextcloud doesn't have a bulk import API, so we create entries individually.

        Args:
            entries: List of PasswordEntry objects

        Returns:
            Statistics dictionary with 'created' and 'failed' counts
        """
        logger.info(f"Bulk importing {len(entries)} passwords to Nextcloud")

        # Ensure all folders exist first
        folders = {entry.folder for entry in entries if entry.folder}
        for folder in folders:
            await self._ensure_folder_exists(folder)

        created = 0
        failed = 0

        for entry in entries:
            try:
                await self.create_password(entry)
                created += 1
            except Exception as e:
                logger.error(f"Failed to import {entry.title}: {e}")
                failed += 1

        logger.info(f"Bulk import complete: {created} created, {failed} failed")

        return {"created": created, "failed": failed}

    async def close(self) -> None:
        """Close HTTP client connections."""
        await self._client.aclose()
