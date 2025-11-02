"""VaultWarden API client for direct password synchronization using Bitwarden REST API."""

import base64
import hashlib
import logging
from typing import Any

import httpx

from .models import PasswordEntry

logger = logging.getLogger(__name__)


class VaultwardenAPIClient:
    """
    API client for VaultWarden (Bitwarden-compatible) server.

    Uses the Bitwarden REST API directly for cipher (password) operations.
    """

    def __init__(
        self,
        url: str,
        email: str,
        password: str,
        client_id: str | None = None,
        client_secret: str | None = None,
    ):
        """
        Initialize VaultWarden API client.

        Args:
            url: VaultWarden server URL (e.g., https://vault.example.com)
            email: User email
            password: User password (master password)
            client_id: OAuth client ID (optional)
            client_secret: OAuth client secret (optional)
        """
        self.url = url.rstrip("/")
        self.email = email
        self.password = password
        self.client_id = client_id or "web"
        self.client_secret = client_secret
        self.access_token: str | None = None
        self._client = httpx.AsyncClient(timeout=30.0)

    async def authenticate(self) -> None:
        """
        Authenticate with VaultWarden server using Bitwarden Identity API.

        Raises:
            Exception: If authentication fails
        """
        logger.info(f"Authenticating with VaultWarden at {self.url}")

        try:
            # Bitwarden uses OAuth2 password grant
            auth_url = f"{self.url}/identity/connect/token"

            # Hash the password (Bitwarden requires base64-encoded SHA256 of password)
            password_hash = hashlib.sha256(self.password.encode()).digest()
            password_b64 = base64.b64encode(password_hash).decode()

            data = {
                "grant_type": "password",
                "username": self.email,
                "password": password_b64,
                "scope": "api offline_access",
                "client_id": self.client_id,
            }

            if self.client_secret:
                data["client_secret"] = self.client_secret

            response = await self._client.post(auth_url, data=data)
            response.raise_for_status()

            auth_data = response.json()
            self.access_token = auth_data["access_token"]

            logger.info("Successfully authenticated with VaultWarden")

        except httpx.HTTPStatusError as e:
            logger.error(f"VaultWarden authentication failed: HTTP {e.response.status_code}")
            logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to authenticate with VaultWarden: {e}") from e
        except Exception as e:
            logger.error(f"VaultWarden authentication failed: {e}")
            raise Exception(f"Failed to authenticate with VaultWarden: {e}") from e

    def _ensure_authenticated(self) -> None:
        """Ensure client is authenticated before making API calls."""
        if not self.access_token:
            raise RuntimeError("Not authenticated. Call authenticate() first.")

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers with authentication."""
        self._ensure_authenticated()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    async def pull_passwords(self) -> list[PasswordEntry]:
        """
        Fetch all password entries from VaultWarden using Bitwarden Sync API.

        Returns:
            List of PasswordEntry objects

        Raises:
            RuntimeError: If not authenticated
            Exception: If API call fails
        """
        self._ensure_authenticated()

        logger.info("Fetching passwords from VaultWarden")

        try:
            # Use Bitwarden sync API to get all data
            sync_url = f"{self.url}/api/sync"
            response = await self._client.get(sync_url, headers=self._get_headers())
            response.raise_for_status()

            sync_data = response.json()
            ciphers = sync_data.get("ciphers", [])
            folders_data = sync_data.get("folders", [])

            # Build folder ID -> name mapping
            folder_map = {f["id"]: f["name"] for f in folders_data if f.get("id") and f.get("name")}

            entries = []
            for cipher in ciphers:
                # Only process login type (skip secure notes, cards, identities)
                # type: 1=login, 2=secure note, 3=card, 4=identity
                if cipher.get("type") != 1:
                    continue

                login_data = cipher.get("login", {})
                folder_id = cipher.get("folderId")

                # Get folder name
                folder_name = folder_map.get(folder_id) if folder_id else None

                # Extract URL from URIs array
                url = None
                if login_data.get("uris"):
                    url = login_data["uris"][0].get("uri")

                # Extract fields
                entry = PasswordEntry(
                    title=cipher.get("name", "Untitled"),
                    username=login_data.get("username", ""),
                    password=login_data.get("password", ""),
                    url=url,
                    notes=cipher.get("notes"),
                    otp_auth=login_data.get("totp"),
                    folder=folder_name,
                )

                entries.append(entry)

            logger.info(f"Fetched {len(entries)} passwords from VaultWarden")
            return entries

        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to fetch passwords: HTTP {e.response.status_code}")
            logger.error(f"Response: {e.response.text}")
            raise Exception(f"Failed to pull passwords: {e}") from e
        except Exception as e:
            logger.error(f"Failed to fetch passwords from VaultWarden: {e}")
            raise Exception(f"Failed to pull passwords: {e}") from e

    async def push_passwords(
        self, entries: list[PasswordEntry], folder_mapping: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """
        Push password entries to VaultWarden using Bitwarden Ciphers API.

        Args:
            entries: List of PasswordEntry objects to push
            folder_mapping: Optional mapping of folder names to folder IDs

        Returns:
            Statistics dictionary:
            {
                'created': 5,
                'updated': 12,
                'skipped': 3,
                'failed': 0,
                'errors': []
            }

        Raises:
            RuntimeError: If not authenticated
        """
        self._ensure_authenticated()

        logger.info(f"Pushing {len(entries)} passwords to VaultWarden")

        stats = {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
        }

        # Get existing ciphers for comparison
        existing_entries = await self.pull_passwords()
        existing_map = {e.get_dedup_key(): e for e in existing_entries}

        # TODO: We'd need to also fetch cipher IDs from sync API to update
        # For now, we'll only create new entries
        for entry in entries:
            try:
                dedup_key = entry.get_dedup_key()

                if dedup_key in existing_map:
                    existing = existing_map[dedup_key]
                    if existing.password == entry.password:
                        stats["skipped"] += 1
                        logger.debug(f"Skipped unchanged: {entry.title}")
                    else:
                        # TODO: Implement update - requires cipher ID
                        stats["skipped"] += 1
                        logger.debug(f"Skipped existing (update not implemented): {entry.title}")
                    continue

                # Create new cipher
                await self._create_cipher(entry, folder_mapping)
                stats["created"] += 1
                logger.debug(f"Created: {entry.title}")

            except Exception as e:
                error_msg = f"Failed to push {entry.title}: {e}"
                logger.error(error_msg)
                stats["failed"] += 1
                stats["errors"].append(error_msg)

        logger.info(
            f"Push complete: {stats['created']} created, "
            f"{stats['updated']} updated, {stats['skipped']} skipped, "
            f"{stats['failed']} failed"
        )

        return stats

    async def list_folders(self) -> list[dict[str, str]]:
        """
        List all folders in VaultWarden.

        Returns:
            List of folder dictionaries with 'id' and 'name' keys

        Raises:
            RuntimeError: If not authenticated
        """
        self._ensure_authenticated()

        try:
            folders_url = f"{self.url}/api/folders"
            response = await self._client.get(folders_url, headers=self._get_headers())
            response.raise_for_status()

            folders_data = response.json()
            return [
                {"id": f["id"], "name": f["name"]}
                for f in folders_data.get("data", [])
                if f.get("id") and f.get("name")
            ]
        except Exception as e:
            logger.error(f"Failed to list folders: {e}")
            raise Exception(f"Failed to list folders: {e}") from e

    async def create_folder(self, name: str) -> str:
        """
        Create a new folder in VaultWarden.

        Args:
            name: Folder name

        Returns:
            Folder ID

        Raises:
            RuntimeError: If not authenticated
        """
        self._ensure_authenticated()

        try:
            folders_url = f"{self.url}/api/folders"
            payload = {"name": name}

            response = await self._client.post(
                folders_url, headers=self._get_headers(), json=payload
            )
            response.raise_for_status()

            folder_data = response.json()
            return folder_data["id"]
        except Exception as e:
            logger.error(f"Failed to create folder '{name}': {e}")
            raise Exception(f"Failed to create folder: {e}") from e

    async def _create_cipher(
        self, entry: PasswordEntry, folder_mapping: dict[str, str] | None = None
    ) -> None:
        """
        Create a new cipher (password entry) in VaultWarden.

        Args:
            entry: PasswordEntry to create
            folder_mapping: Optional folder name to ID mapping
        """
        # Determine folder ID
        folder_id = None
        if entry.folder and folder_mapping:
            folder_id = folder_mapping.get(entry.folder)

        # Build cipher payload (Bitwarden cipher format)
        cipher_data = {
            "type": 1,  # Login type
            "name": entry.title,
            "notes": entry.notes,
            "favorite": False,
            "folderId": folder_id,
            "login": {
                "username": entry.username,
                "password": entry.password,
                "totp": entry.otp_auth,
            },
        }

        # Add URL if present
        if entry.url:
            cipher_data["login"]["uris"] = [{"uri": entry.url, "match": None}]

        # Create cipher via API
        ciphers_url = f"{self.url}/api/ciphers"
        response = await self._client.post(
            ciphers_url, headers=self._get_headers(), json=cipher_data
        )
        response.raise_for_status()

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
