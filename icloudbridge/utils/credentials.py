"""Secure credential storage using system keyring."""

import logging

import keyring

logger = logging.getLogger(__name__)

# Keyring service name for iCloudBridge
SERVICE_NAME = "iCloudBridge"


class CredentialStore:
    """Manages secure storage of CalDAV credentials using system keyring."""

    def __init__(self, service_name: str = SERVICE_NAME):
        """
        Initialize credential store.

        Args:
            service_name: Name of the service in keyring (default: "iCloudBridge")
        """
        self.service_name = service_name

    def set_caldav_password(self, username: str, password: str) -> None:
        """
        Store CalDAV password in system keyring.

        Args:
            username: CalDAV username
            password: CalDAV password to store securely

        Raises:
            keyring.errors.PasswordSetError: If password cannot be stored
        """
        try:
            keyring.set_password(self.service_name, f"caldav:{username}", password)
            logger.info(f"Stored CalDAV password for user: {username}")
        except Exception as e:
            logger.error(f"Failed to store CalDAV password: {e}")
            raise

    def get_caldav_password(self, username: str) -> str | None:
        """
        Retrieve CalDAV password from system keyring.

        Args:
            username: CalDAV username

        Returns:
            Password if found, None otherwise
        """
        try:
            password = keyring.get_password(self.service_name, f"caldav:{username}")
            if password:
                logger.debug(f"Retrieved CalDAV password for user: {username}")
            else:
                logger.debug(f"No CalDAV password found for user: {username}")
            return password
        except Exception as e:
            logger.error(f"Failed to retrieve CalDAV password: {e}")
            return None

    def delete_caldav_password(self, username: str) -> bool:
        """
        Delete CalDAV password from system keyring.

        Args:
            username: CalDAV username

        Returns:
            True if deleted, False if not found or error
        """
        try:
            keyring.delete_password(self.service_name, f"caldav:{username}")
            logger.info(f"Deleted CalDAV password for user: {username}")
            return True
        except keyring.errors.PasswordDeleteError:
            logger.warning(f"No CalDAV password found to delete for user: {username}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete CalDAV password: {e}")
            return False

    def list_stored_users(self) -> list[str]:
        """
        List all users with stored CalDAV passwords.

        Note: This is a best-effort implementation. Some keyring backends
        don't support listing all credentials.

        Returns:
            List of usernames with stored passwords (may be empty if backend doesn't support listing)
        """
        # Unfortunately, keyring doesn't provide a standard way to list all credentials
        # This is a limitation of the keyring library and OS keychains
        logger.warning("Listing credentials is not supported by keyring library")
        return []

    def has_caldav_password(self, username: str) -> bool:
        """
        Check if a CalDAV password exists for the given username.

        Args:
            username: CalDAV username

        Returns:
            True if password exists, False otherwise
        """
        return self.get_caldav_password(username) is not None

    # VaultWarden credential methods

    def set_vaultwarden_credentials(
        self, email: str, password: str, client_id: str | None = None, client_secret: str | None = None
    ) -> None:
        """
        Store VaultWarden credentials in system keyring.

        Stores password, client_id, and client_secret separately.

        Args:
            email: VaultWarden email
            password: VaultWarden password
            client_id: Optional OAuth client ID
            client_secret: Optional OAuth client secret

        Raises:
            keyring.errors.PasswordSetError: If credentials cannot be stored
        """
        try:
            keyring.set_password(self.service_name, f"vaultwarden:password:{email}", password)
            if client_id:
                keyring.set_password(self.service_name, f"vaultwarden:client_id:{email}", client_id)
            if client_secret:
                keyring.set_password(self.service_name, f"vaultwarden:client_secret:{email}", client_secret)
            logger.info(f"Stored VaultWarden credentials for: {email}")
        except Exception as e:
            logger.error(f"Failed to store VaultWarden credentials: {e}")
            raise

    def get_vaultwarden_credentials(self, email: str) -> dict[str, str] | None:
        """
        Retrieve VaultWarden credentials from system keyring.

        Args:
            email: VaultWarden email

        Returns:
            Dictionary with 'email', 'password', 'client_id', 'client_secret' if found, None otherwise
        """
        try:
            password = keyring.get_password(self.service_name, f"vaultwarden:password:{email}")
            if not password:
                logger.debug(f"No VaultWarden password found for: {email}")
                return None

            client_id = keyring.get_password(self.service_name, f"vaultwarden:client_id:{email}")
            client_secret = keyring.get_password(self.service_name, f"vaultwarden:client_secret:{email}")

            logger.debug(f"Retrieved VaultWarden credentials for: {email}")
            return {
                "email": email,
                "password": password,
                "client_id": client_id or "icloudbridge",
                "client_secret": client_secret or "",
            }
        except Exception as e:
            logger.error(f"Failed to retrieve VaultWarden credentials: {e}")
            return None

    def delete_vaultwarden_credentials(self, email: str) -> bool:
        """
        Delete VaultWarden credentials from system keyring.

        Args:
            email: VaultWarden email

        Returns:
            True if deleted, False if not found or error
        """
        try:
            deleted = False
            try:
                keyring.delete_password(self.service_name, f"vaultwarden:password:{email}")
                deleted = True
            except keyring.errors.PasswordDeleteError:
                pass

            # Try to delete optional fields (ignore if not present)
            try:
                keyring.delete_password(self.service_name, f"vaultwarden:client_id:{email}")
            except keyring.errors.PasswordDeleteError:
                pass

            try:
                keyring.delete_password(self.service_name, f"vaultwarden:client_secret:{email}")
            except keyring.errors.PasswordDeleteError:
                pass

            if deleted:
                logger.info(f"Deleted VaultWarden credentials for: {email}")
                return True
            else:
                logger.warning(f"No VaultWarden credentials found to delete for: {email}")
                return False

        except Exception as e:
            logger.error(f"Failed to delete VaultWarden credentials: {e}")
            return False

    def has_vaultwarden_credentials(self, email: str) -> bool:
        """
        Check if VaultWarden credentials exist for the given email.

        Args:
            email: VaultWarden email

        Returns:
            True if credentials exist, False otherwise
        """
        return self.get_vaultwarden_credentials(email) is not None

    # Nextcloud credential methods

    def set_nextcloud_credentials(self, username: str, app_password: str) -> None:
        """
        Store Nextcloud app password in system keyring.

        Args:
            username: Nextcloud username
            app_password: Nextcloud app password (not regular password)

        Raises:
            keyring.errors.PasswordSetError: If credentials cannot be stored
        """
        try:
            keyring.set_password(self.service_name, f"nextcloud:app_password:{username}", app_password)
            logger.info(f"Stored Nextcloud credentials for: {username}")
        except Exception as e:
            logger.error(f"Failed to store Nextcloud credentials: {e}")
            raise

    def get_nextcloud_credentials(self, username: str) -> dict[str, str] | None:
        """
        Retrieve Nextcloud credentials from system keyring.

        Args:
            username: Nextcloud username

        Returns:
            Dictionary with 'username' and 'app_password' if found, None otherwise
        """
        try:
            app_password = keyring.get_password(self.service_name, f"nextcloud:app_password:{username}")
            if not app_password:
                logger.debug(f"No Nextcloud app password found for: {username}")
                return None

            logger.debug(f"Retrieved Nextcloud credentials for: {username}")
            return {
                "username": username,
                "app_password": app_password,
            }
        except Exception as e:
            logger.error(f"Failed to retrieve Nextcloud credentials: {e}")
            return None

    def delete_nextcloud_credentials(self, username: str) -> bool:
        """
        Delete Nextcloud credentials from system keyring.

        Args:
            username: Nextcloud username

        Returns:
            True if deleted, False if not found or error
        """
        try:
            keyring.delete_password(self.service_name, f"nextcloud:app_password:{username}")
            logger.info(f"Deleted Nextcloud credentials for: {username}")
            return True
        except keyring.errors.PasswordDeleteError:
            logger.warning(f"No Nextcloud credentials found to delete for: {username}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete Nextcloud credentials: {e}")
            return False

    def has_nextcloud_credentials(self, username: str) -> bool:
        """
        Check if Nextcloud credentials exist for the given username.

        Args:
            username: Nextcloud username

        Returns:
            True if credentials exist, False otherwise
        """
        return self.get_nextcloud_credentials(username) is not None
