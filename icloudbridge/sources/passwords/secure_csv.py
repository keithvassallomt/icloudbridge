"""Create plaintext exports privately, without clobbering files or following symlinks."""

import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def private_csv(output_path: Path):
    # O_EXCL also refuses symlinks. Permissions apply before the first secret byte.
    fd = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            yield stream
    except BaseException:
        output_path.unlink(missing_ok=True)
        raise
