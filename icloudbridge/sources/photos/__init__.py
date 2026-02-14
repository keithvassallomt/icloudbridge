"""Photo source helpers (scanner + Apple Photos adapters)."""

from .constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from .scanner import PhotoCandidate, PhotoSourceScanner
from .applescript import PhotosAppleScriptAdapter
from .library_reader import PhotosLibraryReader, PhotoAsset, AlbumInfo
from .nextcloud_webdav import NextCloudPhotoUploader

__all__ = [
    "IMAGE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "PhotoCandidate",
    "PhotoSourceScanner",
    "PhotosAppleScriptAdapter",
    "PhotosLibraryReader",
    "PhotoAsset",
    "AlbumInfo",
    "NextCloudPhotoUploader",
]
