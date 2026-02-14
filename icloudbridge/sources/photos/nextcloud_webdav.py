"""NextCloud WebDAV client for photo uploads."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)


class NextCloudPhotoUploader:
    """Upload photos to NextCloud via WebDAV.

    Supports:
    - Creating folder structures
    - Uploading files with mtime preservation
    - Checking file existence
    - Getting file ETags for deduplication
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        upload_path: str = "/Photos/iCloudBridge",
        ssl_verify: bool | str = True,
    ):
        """Initialize NextCloud WebDAV uploader.

        Args:
            base_url: NextCloud server URL (e.g., https://cloud.example.com)
            username: NextCloud username
            password: NextCloud app password
            upload_path: Path within user's files to upload to
            ssl_verify: SSL verification (True, False, or path to CA bundle)
        """
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.upload_path = upload_path.strip("/")
        self.ssl_verify = ssl_verify

        self.webdav_url = f"{self.base_url}/remote.php/dav/files/{username}"

        self._inject_truststore_if_available()

        self._client = httpx.AsyncClient(
            auth=(username, password),
            timeout=httpx.Timeout(300.0, connect=30.0),  # 5 min for large uploads
            follow_redirects=True,
            verify=ssl_verify,
        )

        self._folder_cache: set[str] = set()

    async def close(self) -> None:
        """Close HTTP client connections."""
        await self._client.aclose()

    async def test_connection(self) -> bool:
        """Test connection to NextCloud WebDAV.

        Returns:
            True if connection successful, False otherwise
        """
        try:
            # PROPFIND on root to test connection
            response = await self._client.request(
                "PROPFIND",
                self.webdav_url,
                headers={"Depth": "0"},
            )
            return response.status_code in (200, 207)
        except Exception as e:
            logger.error("Failed to connect to NextCloud: %s", e)
            return False

    def _get_full_path(self, remote_path: str) -> str:
        """Get full WebDAV URL for a path."""
        path = remote_path.strip("/")
        if self.upload_path:
            path = f"{self.upload_path}/{path}"
        return f"{self.webdav_url}/{path}"

    async def ensure_folder(self, remote_path: str) -> bool:
        """Create folder if it doesn't exist (recursive).

        Args:
            remote_path: Path relative to upload_path

        Returns:
            True if folder exists or was created
        """
        # Build path components
        if self.upload_path:
            full_path = f"{self.upload_path}/{remote_path.strip('/')}"
        else:
            full_path = remote_path.strip("/")

        # Check cache first
        if full_path in self._folder_cache:
            return True

        # Create each path component
        parts = full_path.split("/")
        current_path = ""

        for part in parts:
            if not part:
                continue
            current_path = f"{current_path}/{part}" if current_path else part

            if current_path in self._folder_cache:
                continue

            url = f"{self.webdav_url}/{current_path}"

            # Check if folder exists
            try:
                response = await self._client.request(
                    "PROPFIND",
                    url,
                    headers={"Depth": "0"},
                )
                if response.status_code in (200, 207):
                    self._folder_cache.add(current_path)
                    continue
            except Exception:
                pass

            # Create folder
            try:
                response = await self._client.request("MKCOL", url)
                if response.status_code in (201, 405):  # 405 = already exists
                    self._folder_cache.add(current_path)
                    logger.debug("Created folder: %s", current_path)
                else:
                    logger.warning(
                        "Failed to create folder %s: HTTP %d",
                        current_path,
                        response.status_code,
                    )
                    return False
            except Exception as e:
                logger.error("Failed to create folder %s: %s", current_path, e)
                return False

        return True

    async def file_exists(self, remote_path: str) -> bool:
        """Check if a file exists at the given path.

        Args:
            remote_path: Path relative to upload_path

        Returns:
            True if file exists
        """
        url = self._get_full_path(remote_path)

        try:
            response = await self._client.head(url)
            return response.status_code == 200
        except Exception:
            return False

    async def get_file_etag(self, remote_path: str) -> str | None:
        """Get the ETag of a file for change detection.

        Args:
            remote_path: Path relative to upload_path

        Returns:
            ETag string or None if file doesn't exist
        """
        url = self._get_full_path(remote_path)

        try:
            response = await self._client.request(
                "PROPFIND",
                url,
                headers={"Depth": "0"},
                content="""<?xml version="1.0" encoding="UTF-8"?>
                    <d:propfind xmlns:d="DAV:">
                        <d:prop>
                            <d:getetag/>
                        </d:prop>
                    </d:propfind>
                """,
            )

            if response.status_code not in (200, 207):
                return None

            # Parse XML response
            root = ET.fromstring(response.content)
            ns = {"d": "DAV:"}
            etag = root.find(".//d:getetag", ns)
            if etag is not None and etag.text:
                return etag.text.strip('"')

        except Exception as e:
            logger.debug("Failed to get ETag for %s: %s", remote_path, e)

        return None

    async def get_file_info(self, remote_path: str) -> dict[str, Any] | None:
        """Get file information (size, mtime, etag).

        Args:
            remote_path: Path relative to upload_path

        Returns:
            Dict with file info or None if not found
        """
        url = self._get_full_path(remote_path)

        try:
            response = await self._client.request(
                "PROPFIND",
                url,
                headers={"Depth": "0"},
                content="""<?xml version="1.0" encoding="UTF-8"?>
                    <d:propfind xmlns:d="DAV:">
                        <d:prop>
                            <d:getetag/>
                            <d:getcontentlength/>
                            <d:getlastmodified/>
                        </d:prop>
                    </d:propfind>
                """,
            )

            if response.status_code not in (200, 207):
                return None

            root = ET.fromstring(response.content)
            ns = {"d": "DAV:"}

            etag_el = root.find(".//d:getetag", ns)
            size_el = root.find(".//d:getcontentlength", ns)
            mtime_el = root.find(".//d:getlastmodified", ns)

            return {
                "etag": etag_el.text.strip('"') if etag_el is not None and etag_el.text else None,
                "size": int(size_el.text) if size_el is not None and size_el.text else 0,
                "mtime": mtime_el.text if mtime_el is not None else None,
            }

        except Exception as e:
            logger.debug("Failed to get file info for %s: %s", remote_path, e)
            return None

    async def upload_photo(
        self,
        local_path: Path,
        remote_path: str,
        mtime: datetime | None = None,
    ) -> tuple[bool, str | None]:
        """Upload a photo to NextCloud.

        Args:
            local_path: Local file path
            remote_path: Path relative to upload_path
            mtime: Optional modification time to preserve

        Returns:
            Tuple of (success, etag)
        """
        if not local_path.exists():
            logger.error("File not found: %s", local_path)
            return False, None

        # Ensure parent folder exists
        parent = str(Path(remote_path).parent)
        if parent and parent != ".":
            if not await self.ensure_folder(parent):
                return False, None

        url = self._get_full_path(remote_path)

        # Read file content
        content = await asyncio.to_thread(local_path.read_bytes)

        headers: dict[str, str] = {}

        # Set mtime header if provided
        if mtime:
            # NextCloud uses X-OC-Mtime header for mtime preservation
            headers["X-OC-Mtime"] = str(int(mtime.timestamp()))

        try:
            response = await self._client.put(
                url,
                content=content,
                headers=headers,
            )

            if response.status_code in (200, 201, 204):
                etag = response.headers.get("ETag", "").strip('"')
                logger.debug("Uploaded %s -> %s", local_path.name, remote_path)
                return True, etag or None
            else:
                logger.error(
                    "Failed to upload %s: HTTP %d - %s",
                    local_path.name,
                    response.status_code,
                    response.text[:200],
                )
                return False, None

        except Exception as e:
            logger.error("Failed to upload %s: %s", local_path.name, e)
            return False, None

    async def delete_file(self, remote_path: str) -> bool:
        """Delete a file from NextCloud.

        Args:
            remote_path: Path relative to upload_path

        Returns:
            True if deleted or doesn't exist
        """
        url = self._get_full_path(remote_path)

        try:
            response = await self._client.delete(url)
            return response.status_code in (200, 204, 404)
        except Exception as e:
            logger.error("Failed to delete %s: %s", remote_path, e)
            return False

    _truststore_injected = False

    def _inject_truststore_if_available(self) -> None:
        """Try to make HTTPX use the system trust store via truststore."""
        if self.ssl_verify is False:
            logger.debug("SSL verification disabled; skipping truststore injection")
            return
        if NextCloudPhotoUploader._truststore_injected:
            return
        try:
            import truststore

            truststore.inject_into_ssl()
            NextCloudPhotoUploader._truststore_injected = True
            logger.info("Using system trust store for NextCloud WebDAV via truststore")
        except ImportError:
            logger.debug("truststore not installed; using default cert bundle")
        except Exception as exc:
            logger.warning("Failed to inject system trust store: %s", exc)
