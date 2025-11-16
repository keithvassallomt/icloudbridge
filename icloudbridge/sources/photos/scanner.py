"""Source scanning helpers for the photo sync pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from icloudbridge.core.config import PhotoSourceConfig
from icloudbridge.sources.photos.constants import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


@dataclass(slots=True)
class PhotoCandidate:
    """Represents a file discovered in a watched folder before hashing/import."""

    path: Path
    source_name: str
    media_type: str
    size: int
    mtime: datetime
    album: str | None
    original_name: str | None = None

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()


class PhotoSourceScanner:
    """Walk configured folders and yield files that match media criteria."""

    def __init__(self, sources: dict[str, PhotoSourceConfig]):
        self.sources = sources

    def available_sources(self) -> list[str]:
        """Return configured source keys."""
        return list(self.sources.keys())

    def iter_candidates(self, source_names: Iterable[str] | None = None) -> Iterator[PhotoCandidate]:
        """Yield `PhotoCandidate` objects for the requested sources."""

        names = list(source_names) if source_names else self.available_sources()
        for name in names:
            cfg = self.sources.get(name)
            if not cfg:
                continue
            yield from self._walk_source(name, cfg)

    def _walk_source(self, name: str, config: PhotoSourceConfig) -> Iterator[PhotoCandidate]:
        base = config.path
        if not base.exists() or not base.is_dir():
            return

        iterator: Iterator[Path]
        if config.recursive:
            iterator = (p for p in base.rglob("*") if p.is_file())
        else:
            iterator = (p for p in base.iterdir() if p.is_file())

        for path in iterator:
            ext = path.suffix.lower()
            media_type: str
            if config.include_images and ext in IMAGE_EXTENSIONS:
                media_type = "image"
            elif config.include_videos and ext in VIDEO_EXTENSIONS:
                media_type = "video"
            else:
                continue

            stat = path.stat()
            yield PhotoCandidate(
                path=path,
                source_name=name,
                media_type=media_type,
                size=stat.st_size,
                mtime=datetime.fromtimestamp(stat.st_mtime),
                album=config.album,
            )
