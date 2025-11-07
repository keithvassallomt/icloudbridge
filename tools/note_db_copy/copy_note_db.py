#!/usr/bin/python3
"""Copy NoteStore.sqlite into the repo for further processing.

This script intentionally runs with the system Python interpreter so you can
grant it Full Disk Access once and reuse it regardless of Poetry/venv paths.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


DEFAULT_SOURCE = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"
DEFAULT_DEST = Path.cwd() / "testing" / "NoteStore.sqlite"


def copy_notestore(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)

    # Copy accompanying WAL/SHM files if present to preserve recent changes
    for suffix in ["-wal", "-shm"]:
        src_sidecar = source.with_name(source.name + suffix)
        if src_sidecar.exists():
            dest_sidecar = destination.with_name(destination.name + suffix)
            shutil.copy2(src_sidecar, dest_sidecar)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Copy NoteStore.sqlite into the repo")
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="Path to the source NoteStore.sqlite (defaults to the macOS Group Containers copy)",
    )
    parser.add_argument(
        "--dest",
        default=str(DEFAULT_DEST),
        help="Destination file path (defaults to testing/NoteStore.sqlite in the repo)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = Path(args.source).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()

    if not source.exists():
        raise FileNotFoundError(f"Source NoteStore.sqlite not found at {source}")

    copy_notestore(source, dest)
    print(f"Copied {source} -> {dest}")


if __name__ == "__main__":
    main()
