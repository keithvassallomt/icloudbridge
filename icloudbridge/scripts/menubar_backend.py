"""Packaged backend entrypoint for the macOS menubar build."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn

from icloudbridge.core.config import load_config
from icloudbridge.utils.logging import setup_logging
from icloudbridge.utils.runtime_health import log_interpreter_status

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 27731
DEFAULT_DATA_DIR = Path("~/.iCloudBridge").expanduser()
DEFAULT_LOG_DIR = Path("~/Library/Logs/iCloudBridge").expanduser()


def _parse_port(value: str | None) -> int:
    if not value:
        return DEFAULT_PORT
    try:
        port = int(value)
    except ValueError as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"Invalid port override: {value}") from exc
    if not (0 < port < 65536):
        raise ValueError(f"Port must be between 1 and 65535 (got {port})")
    return port


def _prepare_environment() -> tuple[str, int, Path, Path]:
    """Finalize runtime environment and return (host, port, data_dir, log_dir)."""
    host = os.environ.get("ICLOUDBRIDGE_SERVER_HOST", DEFAULT_HOST)
    port = _parse_port(os.environ.get("ICLOUDBRIDGE_SERVER_PORT"))

    data_dir = Path(
        os.environ.get("ICLOUDBRIDGE_GENERAL__DATA_DIR", DEFAULT_DATA_DIR)
    ).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("ICLOUDBRIDGE_GENERAL__DATA_DIR", str(data_dir))

    log_dir = Path(os.environ.get("ICLOUDBRIDGE_LOG_ROOT", DEFAULT_LOG_DIR)).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ["ICLOUDBRIDGE_LOG_ROOT"] = str(log_dir)

    return host, port, data_dir, log_dir


def run() -> None:
    """Start the production API server used by the packaged menubar app."""
    host, port, data_dir, log_dir = _prepare_environment()
    config = load_config()
    config.ensure_data_dir()

    log_path = setup_logging(config, log_directory=log_dir)
    logger.info("iCloudBridge backend starting")
    logger.info("Data dir: %s", data_dir)
    logger.info("Log file: %s", log_path)
    logger.info("Listening on http://%s:%s", host, port)

    # Surface a stale interpreter at startup rather than letting it show up
    # later as inexplicably empty reminder lists and folders.
    log_interpreter_status("Startup interpreter check")

    uvicorn.run(
        "icloudbridge.api.app:app",
        host=host,
        port=port,
        log_level="info",
        reload=False,
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
