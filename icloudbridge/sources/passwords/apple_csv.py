"""Parser for Apple Passwords CSV export format."""

import csv
import logging
from pathlib import Path

from .models import PasswordEntry

logger = logging.getLogger(__name__)


class ApplePasswordsCSVParser:
    """
    Parser for Apple Passwords CSV export files.

    Apple Passwords exports in the following format:
    Title,URL,Username,Password,Notes,OTPAuth
    """

    @staticmethod
    def parse_file(csv_path: Path) -> list[PasswordEntry]:
        """
        Parse an Apple Passwords CSV export file.

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
            expected_headers = {"Title", "URL", "Username", "Password", "Notes", "OTPAuth"}
            if not expected_headers.issubset(set(reader.fieldnames or [])):
                raise ValueError(
                    f"Invalid Apple Passwords CSV format. Expected headers: {expected_headers}"
                )

            for row_num, row in enumerate(reader, start=2):  # Start at 2 (1 is header)
                try:
                    # Required fields
                    title = row.get("Title", "").strip()
                    username = row.get("Username", "").strip()
                    password = row.get("Password", "").strip()

                    if not title or not username or not password:
                        logger.warning(
                            f"Row {row_num}: Skipping entry with missing required fields"
                        )
                        errors += 1
                        continue

                    # Optional fields
                    url = row.get("URL", "").strip() or None
                    notes = row.get("Notes", "").strip() or None
                    otp_auth = row.get("OTPAuth", "").strip() or None

                    entry = PasswordEntry(
                        title=title,
                        username=username,
                        password=password,
                        url=url,
                        notes=notes,
                        otp_auth=otp_auth,
                        folder=None,  # Apple CSV doesn't include folder
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
            f"Parsed Apple Passwords CSV: {len(entries)} entries "
            f"({duplicates} duplicates skipped, {errors} errors)"
        )

        return entries

    @staticmethod
    def write_file(entries: list[PasswordEntry], output_path: Path) -> None:
        """
        Write password entries to Apple Passwords CSV format.

        Args:
            entries: List of PasswordEntry objects
            output_path: Path to write CSV file

        Raises:
            IOError: If file cannot be written
        """
        import os

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["Title", "URL", "Username", "Password", "Notes", "OTPAuth"]
            )
            writer.writeheader()

            for entry in entries:
                writer.writerow(
                    {
                        "Title": entry.title,
                        "URL": entry.url or "",
                        "Username": entry.username,
                        "Password": entry.password,
                        "Notes": entry.notes or "",
                        "OTPAuth": entry.otp_auth or "",
                    }
                )

        # Set secure permissions (owner read/write only)
        os.chmod(output_path, 0o600)

        logger.info(f"Wrote {len(entries)} entries to Apple Passwords CSV: {output_path}")
