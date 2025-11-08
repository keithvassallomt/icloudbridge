#!/usr/bin/python3
"""Copy NoteStore.sqlite into the repo for further processing.

This script intentionally runs with the system Python interpreter so you can
grant it Full Disk Access once and reuse it regardless of Poetry/venv paths.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


CONTAINER_ROOT = Path.home() / "Library/Group Containers/group.com.apple.notes"
DEFAULT_SOURCE = CONTAINER_ROOT / "NoteStore.sqlite"
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


def copy_container(source_root: Path, destination_root: Path) -> None:
    if destination_root.exists():
        shutil.rmtree(destination_root)
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, destination_root)


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
    parser.add_argument(
        "--mode",
        choices=["db", "container"],
        default="db",
        help="Copy a single NoteStore.sqlite ('db') or the entire Notes container ('container')",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = Path(args.source).expanduser().resolve()
    dest = Path(args.dest).expanduser().resolve()

    mode = args.mode

    if mode == "container":
        source_root = Path(args.source).expanduser().resolve()
        if not source_root.is_dir():
            raise FileNotFoundError(f"Source Notes container not found at {source_root}")
        dest_root = Path(args.dest).expanduser().resolve()
        copy_container(source_root, dest_root)
        print(f"Copied notes container {source_root} -> {dest_root}")
        return

    if not source.exists():
        raise FileNotFoundError(f"Source NoteStore.sqlite not found at {source}")

    copy_notestore(source, dest)
    print(f"Copied {source} -> {dest}")


if __name__ == "__main__":
    main()
