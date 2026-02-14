"""Version helpers for iCloudBridge.

The version is sourced from pyproject.toml (single source of truth) with a
fallback to installed package metadata when available.
"""

from importlib.metadata import PackageNotFoundError, version as metadata_version
from pathlib import Path
from typing import Final

import tomllib

PYPROJECT_PATH: Final[Path] = Path(__file__).resolve().parents[1] / "pyproject.toml"
PACKAGE_NAME: Final[str] = "icloudbridge"


def get_version() -> str:
    """Return the application version from pyproject.toml or package metadata.

    Prefers pyproject.toml when running from source (dev mode), falls back to
    installed package metadata for production builds.
    """
    # Prefer pyproject.toml if it exists (running from source)
    try:
        with PYPROJECT_PATH.open("rb") as file:
            data = tomllib.load(file)
        version = data.get("tool", {}).get("poetry", {}).get("version")
        if version:
            return version
    except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
        pass  # Fall back to package metadata

    # Fall back to installed package metadata
    try:
        return metadata_version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.0.0"
