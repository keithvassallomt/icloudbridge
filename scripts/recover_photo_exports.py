#!/usr/bin/env python3
"""Recover photos that a previous export overwrote or lost.

Before 0.2.8, two different photos sharing an original filename and a capture
month resolved to the same destination path (e.g. ``2026/06/IMG_1234.HEIC``).
The second one written silently replaced the first, while iCloudBridge recorded
*both* as successfully exported. Because the export skips anything already
recorded, a normal re-run will never bring the lost photos back.

This script clears those stale rows so the next sync re-exports them. 0.2.8
gives each photo its own filename, so they no longer collide.

It is safe to run repeatedly and does nothing unless you pass --apply.

Usage:
    python3 recover_photo_exports.py                  # report only
    python3 recover_photo_exports.py --apply          # clear colliding rows
    python3 recover_photo_exports.py --check-files    # also find missing files
    python3 recover_photo_exports.py --check-files --apply

Stdlib only, so it runs with the system python3 - no venv needed.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_DB = Path.home() / ".iCloudBridge" / "photos.db"
DEFAULT_CONFIG = Path.home() / ".iCloudBridge" / "config.toml"

# Refuse to clear more than this share of the table on a missing-file sweep;
# an unmounted export volume makes every file look gone.
MISSING_SAFETY_RATIO = 0.5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover photos lost to the pre-0.2.8 export overwrite bug.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"Path to photos.db (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--export-folder", type=Path, default=None,
        help="Export folder. Auto-detected from config.toml when omitted.",
    )
    parser.add_argument(
        "--check-files", action="store_true",
        help="Also clear rows whose exported file is missing from disk.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually make the changes. Without this, only reports.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Override the safety limit on how many rows may be cleared.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip the confirmation prompt (for unattended runs).",
    )
    return parser.parse_args()


# --------------------------------------------------------------------- helpers


def backend_is_running() -> bool:
    """True if an iCloudBridge backend or menubar app looks alive."""
    try:
        out = subprocess.run(
            ["ps", "ax", "-o", "command"], capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return False
    return any(
        marker in out
        for marker in ("icloudbridge.scripts.menubar_backend", "iCloudBridgeMenubar")
    )


def detect_export_folder(config_path: Path) -> Path | None:
    """Best-effort read of the export folder from config.toml.

    Prefers [photos.export] export_folder; falls back to the first
    [photos.sources.*] path, which is what the app itself defaults to.
    Deliberately regex-based: the system python3 on macOS predates tomllib.
    """
    try:
        text = config_path.read_text()
    except OSError:
        return None

    match = re.search(r"^\s*export_folder\s*=\s*[\"'](.+?)[\"']", text, re.M)
    if match:
        return Path(match.group(1)).expanduser()

    section = re.search(r"^\[photos\.sources\.[^\]]+\]", text, re.M)
    if section:
        tail = text[section.end():]
        path_match = re.search(r"^\s*path\s*=\s*[\"'](.+?)[\"']", tail, re.M)
        if path_match:
            return Path(path_match.group(1)).expanduser()
    return None


def human(n: int) -> str:
    return f"{n:,}"


# --------------------------------------------------------------------- queries


def collision_rows(conn: sqlite3.Connection) -> list[tuple]:
    """Rows sharing a destination path with at least one other row."""
    return conn.execute(
        """
        SELECT id, content_hash, apple_asset_uuid, nextcloud_path
        FROM photo_exports
        WHERE nextcloud_path IN (
            SELECT nextcloud_path FROM photo_exports
            GROUP BY nextcloud_path HAVING COUNT(*) > 1
        )
        ORDER BY nextcloud_path
        """
    ).fetchall()


def missing_file_rows(
    conn: sqlite3.Connection, export_folder: Path
) -> list[tuple]:
    """Rows whose recorded file is no longer on disk."""
    missing = []
    for row in conn.execute(
        "SELECT id, content_hash, apple_asset_uuid, nextcloud_path FROM photo_exports"
    ):
        if not (export_folder / row[3]).exists():
            missing.append(row)
    return missing


# ----------------------------------------------------------------------- main


def main() -> int:
    args = parse_args()

    if not args.db.exists():
        print(f"ERROR: no database at {args.db}", file=sys.stderr)
        print("Pass --db if iCloudBridge stores its data elsewhere.", file=sys.stderr)
        return 1

    if backend_is_running():
        print("WARNING: iCloudBridge appears to be running.")
        print("         Quit it before applying changes, or rows may be rewritten")
        print("         underneath this script.")
        if args.apply and not args.yes:
            print()
            if input("Continue anyway? [y/N] ").strip().lower() != "y":
                print("Aborted.")
                return 1
        print()

    conn = sqlite3.connect(args.db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM photo_exports").fetchone()[0]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT nextcloud_path) FROM photo_exports"
        ).fetchone()[0]

        print(f"Database:        {args.db}")
        print(f"Export records:  {human(total)}")
        print(f"Distinct paths:  {human(distinct)}")
        print()

        if total == 0:
            print("Nothing recorded yet - no recovery needed.")
            return 0

        # --- overwritten photos -------------------------------------------
        collisions = collision_rows(conn)
        groups: dict[str, int] = {}
        for _, _, _, path in collisions:
            groups[path] = groups.get(path, 0) + 1

        if collisions:
            lost = sum(count - 1 for count in groups.values())
            print(f"Overwritten photos: {human(len(groups))} destination paths are "
                  f"claimed by more than one photo.")
            print(f"                    {human(len(collisions))} records affected; "
                  f"about {human(lost)} photos were lost on disk.")
            print()
            for path, count in sorted(groups.items(), key=lambda kv: -kv[1])[:10]:
                print(f"    {count} photos -> {path}")
            if len(groups) > 10:
                print(f"    ... and {human(len(groups) - 10)} more")
            print()
        else:
            print("Overwritten photos: none found.")
            print()

        # --- missing files -------------------------------------------------
        missing: list[tuple] = []
        if args.check_files:
            export_folder = args.export_folder or detect_export_folder(DEFAULT_CONFIG)
            if export_folder is None:
                print("ERROR: could not determine the export folder.", file=sys.stderr)
                print("       Pass --export-folder explicitly.", file=sys.stderr)
                return 1
            if not export_folder.exists():
                print(f"ERROR: export folder does not exist: {export_folder}",
                      file=sys.stderr)
                print("       If it lives on an external disk, mount it first -",
                      file=sys.stderr)
                print("       otherwise every file looks missing.", file=sys.stderr)
                return 1

            print(f"Checking files under {export_folder} ...")
            missing = missing_file_rows(conn, export_folder)
            ratio = len(missing) / total if total else 0
            print(f"Missing files:   {human(len(missing))} of {human(total)} "
                  f"({ratio:.0%})")
            print()

            if ratio > MISSING_SAFETY_RATIO and not args.force:
                print(f"REFUSING: more than {MISSING_SAFETY_RATIO:.0%} of records "
                      f"have no file on disk.")
                print("          That usually means the wrong export folder, or a")
                print("          disk that is not mounted - not real data loss.")
                print("          Re-run with --force if you are certain.")
                return 1

        # --- act ------------------------------------------------------------
        doomed_ids = {row[0] for row in collisions} | {row[0] for row in missing}
        if not doomed_ids:
            print("Nothing to clear. Your export records look consistent.")
            return 0

        print(f"Records to clear: {human(len(doomed_ids))}")
        print("Clearing them makes the next photo sync export those photos again.")
        print("No files are deleted by this script.")
        print()

        if not args.apply:
            print("DRY RUN - nothing changed. Re-run with --apply to proceed.")
            return 0

        if not args.yes:
            if input(f"Clear {human(len(doomed_ids))} records? [y/N] ").strip().lower() != "y":
                print("Aborted.")
                return 1

        backup = args.db.with_name(
            f"{args.db.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        )
        shutil.copy2(args.db, backup)
        print(f"Backup written to {backup}")

        conn.executemany(
            "DELETE FROM photo_exports WHERE id = ?",
            [(row_id,) for row_id in sorted(doomed_ids)],
        )
        conn.commit()
        remaining = conn.execute("SELECT COUNT(*) FROM photo_exports").fetchone()[0]
        print(f"Cleared {human(len(doomed_ids))} records; "
              f"{human(remaining)} remain.")
        print()
        print("Next: run a full library photo sync. The cleared photos will be")
        print("re-exported, each to its own filename.")
        return 0

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
