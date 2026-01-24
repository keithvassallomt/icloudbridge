"""VaultWarden API client for direct password synchronization using Bitwarden REST API."""

import base64
import hashlib
import logging
import unicodedata
from typing import Any
from urllib.parse import urlparse

import httpx
from argon2.low_level import Type, hash_secret_raw

from .bitwarden_crypto import (
    encrypt_optional_list,
    encrypt_string,
    ensure_stretched,
    decrypt_cipher_string,
)
from .models import PasswordEntry

logger = logging.getLogger(__name__)


class VaultwardenAPIClient:
    """
    API client for VaultWarden (Bitwarden-compatible) server.

    Uses the Bitwarden REST API directly for cipher (password) operations.
    """

    BITWARDEN_WEB_CLIENT_VERSION = "2025.11.0"  # Keep aligned with Bitwarden web releases

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
        Initialize VaultWarden API client.

        Args:
            url: VaultWarden server URL (e.g., https://vault.example.com)
            email: User email
            password: User password (master password)
            client_id: OAuth client ID (optional)
            client_secret: OAuth client secret (optional)
            ssl_verify_cert: SSL verification flag or CA bundle path
        """
        self.url = url.rstrip("/")
        self.email = email
        self.password = password
        self.ssl_verify_cert = ssl_verify_cert
        parsed = urlparse(self.url)
        host = parsed.hostname or ""
        scheme = parsed.scheme or "https"

        host_is_bitwarden = host.endswith("bitwarden.com") or host.endswith("bitwarden.eu")

        # Allow override via config. Bitwarden cloud expects the public "web" client id;
        # Vaultwarden/self-hosted works with "browser".
        if client_id:
            self.client_id = client_id
        elif host_is_bitwarden:
            self.client_id = "web"
        else:
            self.client_id = "browser"
        self.client_secret = client_secret
        self.access_token: str | None = None
        normalized_email = self.email.strip().lower()
        self._normalized_email = normalized_email
        self._normalized_password = unicodedata.normalize("NFKC", self.password)

        # Bitwarden cloud splits identity/api across subdomains; Vaultwarden keeps them together.
        if host.endswith("bitwarden.com") or host.endswith("bitwarden.eu"):
            base_domain = ".".join(host.split(".")[-2:])
            self.identity_base = f"{scheme}://identity.{base_domain}"
            # Use vault subdomain for API calls (api.bitwarden.* is for organization API only)
            # Personal API keys and user vault access use vault.bitwarden.*
            self.api_base = f"{scheme}://vault.{base_domain}"
        else:
            self.identity_base = f"{self.url}/identity"
            self.api_base = self.url

        seed = f"{normalized_email}:{self.client_id or 'web'}"
        self.device_identifier = hashlib.sha256(seed.encode()).hexdigest()
        # Keep deterministic device identifier; use browser device type (Bitwarden public client).
        self.device_type = 2
        default_headers: dict[str, str] = {}
        if host_is_bitwarden:
            default_headers["Bitwarden-Client-Name"] = "web"
            default_headers["Bitwarden-Client-Version"] = self.BITWARDEN_WEB_CLIENT_VERSION
        self._inject_truststore_if_available()
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers=default_headers,
            verify=self.ssl_verify_cert,
            follow_redirects=True,
        )
        self._master_key: bytes | None = None
        self._user_key: bytes | None = None

    _truststore_injected = False

    def _inject_truststore_if_available(self) -> None:
        """Try to make HTTPX use the system trust store via truststore."""
        if self.ssl_verify_cert is False:
            logger.debug("Vaultwarden SSL verification disabled; skipping truststore injection")
            return
        if VaultwardenAPIClient._truststore_injected:
            return
        try:
            import truststore

            truststore.inject_into_ssl()
            VaultwardenAPIClient._truststore_injected = True
            logger.info("Using system trust store for Vaultwarden SSL verification via truststore")
        except ImportError:
            logger.debug("truststore not installed; using default cert bundle")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(f"Failed to inject system trust store for Vaultwarden: {exc}")

    async def authenticate(self) -> None:
        """
        Authenticate with VaultWarden server using Bitwarden Identity API.

        Supports two authentication methods:
        1. Personal API Key (client_credentials grant) - recommended for Bitwarden Cloud
        2. Username/Password (password grant) - for self-hosted Vaultwarden

        Raises:
            Exception: If authentication fails
        """
        logger.info(
            "Authenticating with VaultWarden at %s (identity=%s)", self.url, self.identity_base
        )

        try:
            auth_url = f"{self.identity_base}/connect/token"

            # Check if using Personal API Key (client_id format: "user.xxxxx")
            is_api_key = self.client_id and self.client_id.startswith("user.")

            if is_api_key and self.client_secret:
                # Use client_credentials grant for Personal API Keys
                logger.info("Using Personal API Key authentication (client_credentials grant)")
                data = {
                    "grant_type": "client_credentials",
                    "scope": "api",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "deviceIdentifier": self.device_identifier,
                    "deviceType": str(self.device_type),
                    "deviceName": "iCloudBridge",
                }
            else:
                # Use password grant for username/password authentication
                logger.info("Using password authentication (password grant)")
                password_b64 = await self._derive_password_key()

                data = {
                    "grant_type": "password",
                    "username": self.email,
                    "password": password_b64,
                    "scope": "api offline_access",
                    "client_id": self.client_id,
                    "deviceIdentifier": self.device_identifier,
                    "deviceType": str(self.device_type),
                    "deviceName": "iCloudBridge",
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

    async def _derive_password_key(self) -> str:
        """Derive the password key using the KDF parameters from VaultWarden."""

        kdf_params = await self._get_kdf_parameters()
        kdf = kdf_params.get("Kdf", 0)
        email_salt = self._normalized_email.encode("utf-8")
        password_bytes = self._normalized_password.encode("utf-8")

        if kdf == 0:  # PBKDF2-SHA256
            iterations = kdf_params.get("KdfIterations") or 100000
            master_key = hashlib.pbkdf2_hmac(
                "sha256", password_bytes, email_salt, iterations, dklen=32
            )
        elif kdf in (1, 2):  # Argon2id (Vaultwarden historically used 2)
            iterations = kdf_params.get("KdfIterations") or 3
            memory_kib = kdf_params.get("KdfMemory") or 64 * 1024
            parallelism = kdf_params.get("KdfParallelism") or 2
            master_key = hash_secret_raw(
                secret=password_bytes,
                salt=email_salt,
                time_cost=iterations,
                memory_cost=memory_kib,
                parallelism=parallelism,
                hash_len=32,
                type=Type.ID,
            )
        else:
            raise Exception(f"Unsupported VaultWarden KDF: {kdf}")

        self._master_key = master_key

        # Bitwarden performs a second PBKDF2 (1 iteration) using the master key as the
        # "password" and the original password as the salt before sending it to /token.
        server_hash = hashlib.pbkdf2_hmac(
            "sha256",
            master_key,
            password_bytes,
            1,
            dklen=32,
        )

        return base64.b64encode(server_hash).decode()

    async def _get_kdf_parameters(self) -> dict[str, Any]:
        """Fetch user-specific KDF settings from VaultWarden."""

        primary_prelogin = f"{self.identity_base}/connect/prelogin"
        fallback_prelogin = f"{self.identity_base}/accounts/prelogin"
        payload = {"email": self.email}

        for url in (primary_prelogin, fallback_prelogin):
            try:
                response = await self._client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                normalized = {k.lower(): v for k, v in data.items() if isinstance(k, str)}
                if "kdf" not in normalized:
                    logger.warning("Prelogin response missing Kdf; falling back to defaults: %s", data)
                    return {"Kdf": 0, "KdfIterations": 100000}
                return {
                    "Kdf": normalized.get("kdf", 0),
                    "KdfIterations": normalized.get("kdfiterations") or normalized.get("kdf_iterations"),
                    "KdfMemory": normalized.get("kdfmemory") or normalized.get("kdf_memory"),
                    "KdfParallelism": normalized.get("kdfparallelism") or normalized.get("kdf_parallelism"),
                }
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    logger.debug("Prelogin endpoint %s not found; trying fallback", url)
                    continue
                logger.error("Prelogin request failed (%s): %s", url, exc.response.text)
                raise
            except Exception as exc:
                logger.error("Failed to fetch KDF parameters from %s: %s", url, exc)
                continue

        return {"Kdf": 0, "KdfIterations": 100000}

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
            user_key = await self._ensure_user_key()

            # Use Bitwarden sync API to get all data
            sync_url = f"{self.api_base}/api/sync"
            response = await self._client.get(sync_url, headers=self._get_headers())
            response.raise_for_status()

            sync_data = response.json()
            ciphers = sync_data.get("ciphers", [])
            folders_data = sync_data.get("folders", [])

            # Build folder ID -> name mapping
            folder_map: dict[str, str] = {}
            for folder in folders_data:
                folder_id = folder.get("id")
                enc_name = folder.get("name")
                if not folder_id or not enc_name:
                    continue
                folder_map[folder_id] = self._maybe_decrypt(enc_name, user_key)

            entries = []
            for cipher in ciphers:
                # Only process login type (skip secure notes, cards, identities)
                # type: 1=login, 2=secure note, 3=card, 4=identity
                if cipher.get("type") != 1:
                    continue

                # Skip anything that's in the trash/bin
                if cipher.get("deletedDate") or cipher.get("trashed") or cipher.get("isDeleted"):
                    logger.debug("Skipping deleted VaultWarden entry: %s", cipher.get("name"))
                    continue

                login_data = cipher.get("login", {})
                folder_id = cipher.get("folderId")

                # Get folder name
                folder_name = folder_map.get(folder_id) if folder_id else None

                # Extract URLs from URIs array
                urls = []
                if login_data.get("uris"):
                    for uri_entry in login_data["uris"]:
                        uri = uri_entry.get("uri")
                        if uri:
                            urls.append(uri)

                # Extract fields
                cipher_id = cipher.get("id")
                title = self._maybe_decrypt(cipher.get("name"), user_key) or "Untitled"
                notes = self._maybe_decrypt(cipher.get("notes"), user_key)
                username = self._maybe_decrypt(login_data.get("username"), user_key) or ""
                password = self._maybe_decrypt(login_data.get("password"), user_key) or ""
                totp = self._maybe_decrypt(login_data.get("totp"), user_key)

                entry = PasswordEntry(
                    title=title,
                    username=username,
                    password=password,
                    url=None,
                    notes=notes,
                    otp_auth=totp,
                    folder=folder_name,
                    provider_id=cipher_id,
                )

                for uri in urls:
                    entry.add_url(self._maybe_decrypt(uri, user_key) or uri)

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
        self,
        entries: list[PasswordEntry],
        folder_mapping: dict[str, str] | None = None,
        dry_run: bool = False,
        use_bulk: bool = False,
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
            "dry_run": dry_run,
        }

        # Get existing ciphers for comparison
        existing_entries = await self.pull_passwords()
        existing_map = {e.get_dedup_key(): e for e in existing_entries}
        user_key = await self._ensure_user_key()
        payload_queue: list[tuple[PasswordEntry, dict[str, Any]]] = []

        # TODO: We'd need to also fetch cipher IDs from sync API to update
        # For now, we'll only create new entries
        for entry in entries:
            try:
                dedup_key = entry.get_dedup_key()

                create_entry = True
                if dedup_key in existing_map:
                    existing = existing_map[dedup_key]
                    create_entry = existing.password != entry.password
                    if not create_entry:
                        stats["skipped"] += 1
                        logger.debug(f"Skipped unchanged: {entry.title}")
                        continue

                if dry_run:
                    if create_entry:
                        stats["created"] += 1
                        logger.debug(f"[Dry Run] Would create: {entry.title}")
                    continue

                if create_entry:
                    payload = self._build_cipher_payload(
                        entry, folder_mapping, user_key, include_folder_id=not use_bulk
                    )
                    payload_queue.append((entry, payload))
                else:
                    stats["skipped"] += 1
                    logger.debug(f"Skipped (update not implemented): {entry.title}")

            except Exception as e:
                error_msg = f"Failed to push {entry.title}: {e}"
                logger.error(error_msg)
                stats["failed"] += 1
                stats["errors"].append(error_msg)

        if dry_run:
            stats["created"] += len(payload_queue)
            return stats

        if use_bulk and payload_queue:
            try:
                await self._bulk_create_ciphers(payload_queue, folder_mapping, user_key)
                stats["created"] += len(payload_queue)
            except Exception as exc:
                logger.error("Bulk cipher import failed: %s", exc)
                stats["failed"] += len(payload_queue)
                stats["errors"].append(str(exc))
            return stats

        # Fallback to per-item creation
        for entry, payload in payload_queue:
            try:
                await self._post_cipher_payload(payload)
                stats["created"] += 1
                logger.debug(f"Created: {entry.title}")
            except Exception as exc:
                error_msg = f"Failed to push {entry.title}: {exc}"
                logger.error(error_msg)
                stats["failed"] += 1
                stats["errors"].append(error_msg)

        logger.info(
            f"Push complete: {stats['created']} created, "
            f"{stats['updated']} updated, {stats['skipped']} skipped, "
            f"{stats['failed']} failed"
        )

        return stats

    async def delete_password(self, cipher_id: str, soft_delete: bool = True) -> bool:
        """
        Delete or soft-delete a cipher (password entry).

        Args:
            cipher_id: The cipher ID to delete
            soft_delete: If True, move to trash. If False, permanently delete.

        Returns:
            True if successful, False otherwise

        Raises:
            RuntimeError: If not authenticated
        """
        self._ensure_authenticated()

        try:
            if soft_delete:
                # Soft delete: PUT /api/ciphers/{id}/delete (move to trash)
                delete_url = f"{self.api_base}/api/ciphers/{cipher_id}/delete"
                response = await self._client.put(
                    delete_url, headers=self._get_headers()
                )
            else:
                # Permanent delete: DELETE /api/ciphers/{id}
                delete_url = f"{self.api_base}/api/ciphers/{cipher_id}"
                response = await self._client.delete(
                    delete_url, headers=self._get_headers()
                )

            response.raise_for_status()
            logger.info(
                f"{'Soft-' if soft_delete else 'Permanently '}deleted cipher: {cipher_id}"
            )
            return True

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to delete cipher {cipher_id}: HTTP {e.response.status_code}"
            )
            logger.error(f"Response: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete cipher {cipher_id}: {e}")
            return False

    async def update_password(
        self, cipher_id: str, entry: PasswordEntry, folder_mapping: dict[str, str] | None = None
    ) -> bool:
        """
        Update an existing cipher (password entry) in VaultWarden.

        Args:
            cipher_id: The cipher ID to update
            entry: Updated PasswordEntry
            folder_mapping: Optional folder name to ID mapping

        Returns:
            True if successful, False otherwise

        Raises:
            RuntimeError: If not authenticated
        """
        self._ensure_authenticated()

        try:
            user_key = await self._ensure_user_key()
            payload = self._build_cipher_payload(entry, folder_mapping, user_key, include_folder_id=True)

            update_url = f"{self.api_base}/api/ciphers/{cipher_id}"
            response = await self._client.put(
                update_url, headers=self._get_headers(), json=payload
            )
            response.raise_for_status()
            logger.info(f"Updated cipher: {cipher_id} ({entry.title})")
            return True

        except httpx.HTTPStatusError as e:
            logger.error(
                f"Failed to update cipher {cipher_id}: HTTP {e.response.status_code}"
            )
            logger.error(f"Response: {e.response.text}")
            return False
        except Exception as e:
            logger.error(f"Failed to update cipher {cipher_id}: {e}")
            return False

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
            user_key = await self._ensure_user_key()
            folders_url = f"{self.api_base}/api/folders"
            response = await self._client.get(folders_url, headers=self._get_headers())
            response.raise_for_status()

            folders_data = response.json()
            decrypted: list[dict[str, str]] = []
            for folder in folders_data.get("data", []):
                folder_id = folder.get("id")
                enc_name = folder.get("name")
                if not folder_id or not enc_name:
                    continue
                decrypted.append({"id": folder_id, "name": self._maybe_decrypt(enc_name, user_key)})
            return decrypted
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
            user_key = await self._ensure_user_key()
            folders_url = f"{self.api_base}/api/folders"
            payload = {"name": encrypt_string(name, user_key)}

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
        user_key = await self._ensure_user_key()
        user_key = await self._ensure_user_key()
        payload = self._build_cipher_payload(entry, folder_mapping, user_key, include_folder_id=True)
        await self._post_cipher_payload(payload)

    def _build_cipher_payload(
        self,
        entry: PasswordEntry,
        folder_mapping: dict[str, str] | None,
        user_key: bytes,
        *,
        include_folder_id: bool,
    ) -> dict[str, Any]:
        folder_id = None
        if include_folder_id and entry.folder and folder_mapping:
            folder_id = folder_mapping.get(entry.folder)

        cipher_data: dict[str, Any] = {
            "type": 1,
            "name": encrypt_string(entry.title, user_key),
            "favorite": False,
            "folderId": folder_id,
            "login": {},
        }

        if entry.notes:
            cipher_data["notes"] = encrypt_string(entry.notes, user_key)

        if entry.username:
            cipher_data["login"]["username"] = encrypt_string(entry.username, user_key)
        if entry.password:
            cipher_data["login"]["password"] = encrypt_string(entry.password, user_key)
        if entry.otp_auth:
            cipher_data["login"]["totp"] = encrypt_string(entry.otp_auth, user_key)

        urls = entry.get_all_urls()
        if urls:
            uris = encrypt_optional_list(urls, user_key)
            if uris:
                cipher_data["login"]["uris"] = uris

        return cipher_data

    async def _post_cipher_payload(self, payload: dict[str, Any]) -> None:
        ciphers_url = f"{self.api_base}/api/ciphers"
        response = await self._client.post(
            ciphers_url, headers=self._get_headers(), json=payload
        )
        response.raise_for_status()

    async def _bulk_create_ciphers(
        self,
        payload_queue: list[tuple[PasswordEntry, dict[str, Any]]],
        folder_mapping: dict[str, str] | None,
        user_key: bytes,
    ) -> None:
        ciphers: list[dict[str, Any]] = []
        folders_payload: list[dict[str, Any]] = []
        folder_index_map: dict[str, int] = {}
        relationships: list[list[int]] = []

        for cipher_index, (entry, payload) in enumerate(payload_queue):
            ciphers.append(payload)
            folder_name = entry.folder
            if folder_name and folder_mapping and folder_name in folder_mapping:
                if folder_name not in folder_index_map:
                    folder_id = folder_mapping[folder_name]
                    folders_payload.append(
                        {
                            "id": folder_id,
                            "name": encrypt_string(folder_name, user_key),
                        }
                    )
                    folder_index_map[folder_name] = len(folders_payload) - 1
                relationships.append([cipher_index, folder_index_map[folder_name]])

        import_body = {
            "ciphers": ciphers,
            "folders": folders_payload,
            "folderRelationships": relationships,
        }
        import_url = f"{self.api_base}/api/ciphers/import"
        response = await self._client.post(
            import_url, headers=self._get_headers(), json=import_body
        )
        response.raise_for_status()

    async def _ensure_user_key(self) -> bytes:
        if self._user_key is not None:
            return self._user_key

        # If we don't have a master key yet (e.g., used API key auth), derive it now
        # Note: Even with Personal API Key authentication, we still need the master
        # password to decrypt vault contents (API key only authenticates to Bitwarden)
        if self._master_key is None:
            logger.debug("Master key not cached, deriving from password for vault decryption")
            await self._derive_password_key()

        if self._master_key is None:
            raise RuntimeError("Master key not available; authenticate first")

        profile_url = f"{self.api_base}/api/accounts/profile"
        response = await self._client.get(profile_url, headers=self._get_headers())
        response.raise_for_status()
        profile = response.json()
        encrypted_key = profile.get("key")
        if not encrypted_key:
            raise RuntimeError("Profile missing encryption key")

        decrypted = decrypt_cipher_string(encrypted_key, self._master_key)
        # user key should be 64 bytes; if shorter, stretch
        if len(decrypted) < 64:
            decrypted = ensure_stretched(decrypted)
        self._user_key = decrypted
        return self._user_key

    def _maybe_decrypt(self, value: str | None, key: bytes) -> str | None:
        if not value:
            return value
        try:
            decrypted = decrypt_cipher_string(value, key)
            return decrypted.decode("utf-8")
        except Exception:
            return value

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
