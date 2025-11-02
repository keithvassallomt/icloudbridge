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
