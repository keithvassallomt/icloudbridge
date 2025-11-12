"""EXIF metadata extraction utilities for photo files."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Register HEIF/HEIC support for Pillow
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    logger.debug("pillow-heif not installed, HEIC/HEIF images may not be supported")


def extract_capture_timestamp(path: Path) -> datetime | None:
    """
    Extract the original capture timestamp from EXIF data.

    Falls back to file mtime if EXIF is unavailable.

    Args:
        path: Path to the image file

    Returns:
        datetime of capture, or None if unavailable
    """
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS

        with Image.open(path) as img:
            exif_data = img.getexif()

            if not exif_data:
                # No EXIF data, fall back to mtime
                logger.debug(f"No EXIF data found for {path.name}, using mtime")
                return datetime.fromtimestamp(path.stat().st_mtime)

            # Try common EXIF timestamp tags in order of preference
            # 36867 = DateTimeOriginal (when photo was taken)
            # 36868 = DateTimeDigitized (when photo was digitized)
            # 306 = DateTime (when file was modified)
            for tag_id in [36867, 36868, 306]:
                if tag_id in exif_data:
                    timestamp_str = exif_data[tag_id]
                    try:
                        # EXIF timestamps are typically in format: "2023:10:15 14:30:45"
                        return datetime.strptime(timestamp_str, "%Y:%m:%d %H:%M:%S")
                    except (ValueError, TypeError) as e:
                        logger.warning(f"Failed to parse EXIF timestamp {timestamp_str}: {e}")
                        continue

            # No valid timestamp found in EXIF, use mtime
            logger.debug(f"No valid EXIF timestamp found for {path.name}, using mtime")
            return datetime.fromtimestamp(path.stat().st_mtime)

    except ImportError:
        logger.warning("Pillow not installed, falling back to mtime")
        return datetime.fromtimestamp(path.stat().st_mtime)
    except Exception as e:
        logger.warning(f"Failed to extract EXIF from {path.name}: {e}, using mtime")
        return datetime.fromtimestamp(path.stat().st_mtime)


def extract_exif_metadata(path: Path) -> dict[str, any]:
    """
    Extract all available EXIF metadata from an image.

    Args:
        path: Path to the image file

    Returns:
        Dictionary of EXIF metadata
    """
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS

        with Image.open(path) as img:
            exif_data = img.getexif()

            if not exif_data:
                return {}

            # Convert numeric tags to readable names
            metadata = {}
            for tag_id, value in exif_data.items():
                tag_name = TAGS.get(tag_id, tag_id)

                # Convert bytes to string
                if isinstance(value, bytes):
                    try:
                        value = value.decode('utf-8', errors='ignore')
                    except:
                        value = str(value)

                metadata[tag_name] = value

            return metadata

    except ImportError:
        logger.warning("Pillow not installed, cannot extract EXIF")
        return {}
    except Exception as e:
        logger.warning(f"Failed to extract EXIF metadata from {path.name}: {e}")
        return {}
