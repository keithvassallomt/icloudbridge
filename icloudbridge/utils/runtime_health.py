"""
Checks on the Python runtime the backend is executing from.

macOS grants TCC privileges (Reminders, Photos, file access) to a specific
executable, identified by path and code signature. When that executable is
replaced or deleted underneath a running process - which is exactly what
`brew upgrade python@3.12` does, since it installs into a new Cellar directory
and `brew cleanup` removes the old one - macOS can no longer validate the
process and silently denies every privileged call it makes from then on.

Nothing raises. EventKit just starts reporting zero reminder lists, and
protected directories start reading as empty. The only way to notice is to check
whether the interpreter we are running from still exists.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class InterpreterStatus:
    """Result of inspecting the interpreter backing this process."""

    healthy: bool
    reason: str | None
    running_executable: str | None
    base_executable: str | None
    venv_base_executable: str | None

    def as_dict(self) -> dict:
        return asdict(self)


def _running_executable() -> Path | None:
    """
    Path of the Mach-O image this process is actually running from.

    sys.executable is not good enough: in a venv it is a symlink chain that
    re-resolves to whatever is currently installed, so it keeps looking valid
    after the binary this process started from has been deleted.
    """
    if sys.platform != "darwin":
        return None

    try:
        libc = ctypes.CDLL(None)
        buf = ctypes.create_string_buffer(4096)
        size = ctypes.c_uint32(len(buf))
        if libc._NSGetExecutablePath(buf, ctypes.byref(size)) != 0:
            return None
        return Path(os.fsdecode(buf.value))
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Could not determine running executable: {e}")
        return None


def _venv_base_executable() -> Path | None:
    """The base interpreter recorded in pyvenv.cfg, if we are in a venv."""
    if sys.prefix == sys.base_prefix:
        return None

    cfg = Path(sys.prefix) / "pyvenv.cfg"
    try:
        for line in cfg.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "executable":
                return Path(value.strip())
    except OSError:
        return None
    return None


def check_interpreter() -> InterpreterStatus:
    """
    Determine whether the interpreter backing this process is still intact.

    An unhealthy result means the process should be restarted: its macOS
    privileges are gone and cannot be recovered in place.
    """
    running = _running_executable()
    base = Path(sys._base_executable) if getattr(sys, "_base_executable", None) else None
    venv_base = _venv_base_executable()

    status = InterpreterStatus(
        healthy=True,
        reason=None,
        running_executable=str(running) if running else None,
        base_executable=str(base) if base else None,
        venv_base_executable=str(venv_base) if venv_base else None,
    )

    # The strongest signal: the image we are executing is gone. Any privileged
    # call this process makes from here on is already being denied.
    if running is not None and not running.exists():
        status.healthy = False
        status.reason = (
            f"The interpreter this process is running from ({running}) has been "
            "deleted, most likely by a Python package upgrade. macOS has revoked "
            "this process's Reminders, Photos and file access. Restart the backend."
        )
        return status

    # Weaker but still fatal: a fresh process would start from an interpreter
    # the venv no longer points at, so its permissions were never granted.
    for label, path in (("venv base interpreter", venv_base), ("base interpreter", base)):
        if path is not None and not path.exists():
            status.healthy = False
            status.reason = (
                f"The {label} recorded for this environment ({path}) no longer "
                "exists, so the virtualenv is stale. It must be rebuilt, and macOS "
                "permissions re-granted, before syncing can work."
            )
            return status

    return status


def log_interpreter_status(context: str = "") -> InterpreterStatus:
    """Check the interpreter and log the outcome. Returns the status."""
    status = check_interpreter()
    prefix = f"{context}: " if context else ""

    if status.healthy:
        logger.debug("%sinterpreter check passed (%s)", prefix, status.running_executable)
    else:
        logger.error("%s%s", prefix, status.reason)

    return status
