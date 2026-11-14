"""Parser for Apple Passwords CSV export format."""

import csv
import logging
import re
from pathlib import Path

from .diagnostics import InvalidInputError
from .models import PasswordEntry
from .secure_csv import private_csv

logger = logging.getLogger(__name__)


_ICB_FOLDER_TAG = re.compile(r"#icb_([A-Za-z0-9_-]+)")


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
        account_index: dict[tuple[str, str, str], PasswordEntry] = {}
        duplicates = 0
        errors = 0

        with open(csv_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)

            # Validate headers
            expected_headers = {"Title", "URL", "Username", "Password", "Notes", "OTPAuth"}
            if not expected_headers.issubset(set(reader.fieldnames or [])):
                raise InvalidInputError(
                    "This isn't an Apple Passwords export: expected the columns "
                    "Title, URL, Username, Password, Notes and OTPAuth."
                )

            for row_num, row in enumerate(reader, start=2):  # Start at 2 (1 is header)
                try:
                    # Required fields
                    title = row.get("Title", "").strip()
                    username = row.get("Username", "").strip()
                    password = row.get("Password", "")

                    if not title:
                        logger.warning(
                            f"Row {row_num}: Skipping entry with missing required fields"
                        )
                        errors += 1
                        continue

                    # Optional fields
                    url = row.get("URL", "").strip() or None
                    notes_raw = row.get("Notes", "")
                    notes = notes_raw or None
                    otp_auth = row.get("OTPAuth", "").strip() or None

                    folder = None
                    if notes_raw:
                        tag_match = _ICB_FOLDER_TAG.search(notes_raw)
                        if tag_match:
                            folder = tag_match.group(1)

                    account_key = (title.lower(), username.lower(), password)
                    entry = account_index.get(account_key)
                    if entry:
                        duplicates += 1
                        if notes and not entry.notes:
                            entry.notes = notes
                        if otp_auth and not entry.otp_auth:
                            entry.otp_auth = otp_auth
                        if folder and not entry.folder:
                            entry.folder = folder
                        if url:
                            entry.add_url(url)
                    else:
                        entry = PasswordEntry(
                            title=title,
                            username=username,
                            password=password,
                            url=None,
                            notes=notes,
                            otp_auth=otp_auth,
                            folder=folder,
                        )
                        if url:
                            entry.add_url(url)
                        account_index[account_key] = entry
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
        with private_csv(output_path) as f:
            writer = csv.DictWriter(
                f, fieldnames=["Title", "URL", "Username", "Password", "Notes", "OTPAuth"]
            )
            writer.writeheader()

            for entry in entries:
                urls = entry.get_all_urls() or [None]
                for url in urls:
                    writer.writerow(
                        {
                            "Title": entry.title,
                            "URL": url or "",
                            "Username": entry.username,
                            "Password": entry.password,
                            "Notes": entry.notes or "",
                            "OTPAuth": entry.otp_auth or "",
                        }
                    )

        logger.info(f"Wrote {len(entries)} entries to Apple Passwords CSV: {output_path}")
