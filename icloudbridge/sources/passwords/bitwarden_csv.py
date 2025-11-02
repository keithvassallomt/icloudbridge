"""Parser for Bitwarden CSV export/import format."""

import csv
import logging
from pathlib import Path

from .models import PasswordEntry

logger = logging.getLogger(__name__)


class BitwardenCSVParser:
    """
    Parser for Bitwarden CSV import/export files.

    Bitwarden format:
    folder,favorite,type,name,notes,fields,reprompt,login_uri,login_username,login_password,login_totp
    """

    @staticmethod
    def parse_file(csv_path: Path) -> list[PasswordEntry]:
        """
        Parse a Bitwarden CSV export file.

        Args:
            csv_path: Path to the CSV file

        Returns:
            List of PasswordEntry objects

        Raises:
            FileNotFoundError: If CSV file doesn't exist
            ValueError: If CSV format is invalid
        """
        if not csv_path.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")

        entries = []
        duplicates = 0
        errors = 0
        seen_keys = set()

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # Validate headers
            expected_headers = {
                "folder",
                "favorite",
                "type",
                "name",
                "login_uri",
                "login_username",
                "login_password",
            }
            if not expected_headers.issubset(set(reader.fieldnames or [])):
                raise ValueError(
                    f"Invalid Bitwarden CSV format. Expected headers include: {expected_headers}"
                )

            for row_num, row in enumerate(reader, start=2):  # Start at 2 (1 is header)
                try:
                    # Only process login entries
                    entry_type = row.get("type", "").strip()
                    if entry_type != "login":
                        logger.debug(
                            f"Row {row_num}: Skipping non-login entry type: {entry_type}"
                        )
                        continue

                    # Required fields
                    title = row.get("name", "").strip()
                    username = row.get("login_username", "").strip()
                    password = row.get("login_password", "").strip()

                    if not title or not username or not password:
                        logger.warning(
                            f"Row {row_num}: Skipping entry with missing required fields"
                        )
                        errors += 1
                        continue

                    # Optional fields
                    url = row.get("login_uri", "").strip() or None
                    notes = row.get("notes", "").strip() or None
                    otp_auth = row.get("login_totp", "").strip() or None
                    folder = row.get("folder", "").strip() or None

                    entry = PasswordEntry(
                        title=title,
                        username=username,
                        password=password,
                        url=url,
                        notes=notes,
                        otp_auth=otp_auth,
                        folder=folder,
                    )

                    # Deduplication
                    dedup_key = entry.get_dedup_key()
                    if dedup_key in seen_keys:
                        logger.debug(
                            f"Row {row_num}: Duplicate entry skipped: {title} / {username}"
                        )
                        duplicates += 1
                        continue

                    seen_keys.add(dedup_key)
                    entries.append(entry)

                except Exception as e:
                    logger.error(f"Row {row_num}: Error parsing entry: {e}")
                    errors += 1

        logger.info(
            f"Parsed Bitwarden CSV: {len(entries)} entries "
            f"({duplicates} duplicates skipped, {errors} errors)"
        )

        return entries

    @staticmethod
    def write_file(
        entries: list[PasswordEntry],
        output_path: Path,
        folder_mapping: dict[str, str] | None = None,
    ) -> None:
        """
        Write password entries to Bitwarden CSV format.

        Args:
            entries: List of PasswordEntry objects
            output_path: Path to write CSV file
            folder_mapping: Optional dict mapping entry titles/URLs to folder names

        Raises:
            IOError: If file cannot be written
        """
        import os

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "folder",
                    "favorite",
                    "type",
                    "name",
                    "notes",
                    "fields",
                    "reprompt",
                    "login_uri",
                    "login_username",
                    "login_password",
                    "login_totp",
                ],
            )
            writer.writeheader()

            for entry in entries:
                # Determine folder
                folder = entry.folder or ""
                if folder_mapping:
                    # Try to map using title or URL
                    folder = folder_mapping.get(entry.title, folder)
                    if not folder and entry.url:
                        folder = folder_mapping.get(entry.url, "")

                writer.writerow(
                    {
                        "folder": folder,
                        "favorite": "0",  # Not favorite by default
                        "type": "login",
                        "name": entry.title,
                        "notes": entry.notes or "",
                        "fields": "",  # Custom fields not supported yet
                        "reprompt": "0",  # No re-prompt by default
                        "login_uri": entry.url or "",
                        "login_username": entry.username,
                        "login_password": entry.password,
                        "login_totp": entry.otp_auth or "",
                    }
                )

        # Set secure permissions (owner read/write only)
        os.chmod(output_path, 0o600)

        logger.info(f"Wrote {len(entries)} entries to Bitwarden CSV: {output_path}")
