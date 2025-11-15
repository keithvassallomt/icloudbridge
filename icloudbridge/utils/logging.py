"""Centralized logging utilities."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
from pathlib import Path
from typing import Mapping

from icloudbridge.core.config import AppConfig

try:
    from rich.logging import RichHandler
except ImportError:  # pragma: no cover - rich is an optional dependency
    RichHandler = None  # type: ignore


_LEVEL_MAP: Mapping[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

_current_levelno = logging.INFO
_current_levelname = "INFO"


def _parse_level(value: str | int) -> tuple[int, str]:
    if isinstance(value, int):
        return value, logging.getLevelName(value)
    level_name = str(value).upper()
    if level_name not in _LEVEL_MAP:
        raise ValueError(f"Unsupported log level: {value}")
    return _LEVEL_MAP[level_name], level_name


class SeverityOverrideFilter(logging.Filter):
    """Filter that lets us override levels by record attributes or categories."""

    def __init__(self, category_levels: Mapping[str, str]):
        super().__init__()
        self.category_levels = {
            category: _parse_level(level)[0] for category, level in category_levels.items()
        }

    def filter(self, record: logging.LogRecord) -> bool:
        forced = getattr(record, "force_level", None) or getattr(record, "override_level", None)
        if forced:
            levelno, levelname = _parse_level(forced)
            record.levelno = levelno
            record.levelname = levelname
            return True

        category = getattr(record, "log_category", None)
        if category and category in self.category_levels:
            levelno = self.category_levels[category]
            record.levelno = levelno
            record.levelname = logging.getLevelName(levelno)
        return True


def build_console_handler(level_name: str) -> logging.Handler:
    levelno, _ = _parse_level(level_name)
    if RichHandler is not None:
        handler: logging.Handler = RichHandler(rich_tracebacks=True, show_time=False)
    else:
        handler = logging.StreamHandler()
    handler.setLevel(levelno)
    handler.setFormatter(
        logging.Formatter("%(message)s") if RichHandler is None else logging.Formatter("%(message)s")
    )
    return handler


def build_file_handler(config: AppConfig) -> logging.Handler:
    log_dir = config.general.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    file_path = log_dir / config.general.log_file_name
    handler = logging.handlers.RotatingFileHandler(
        file_path,
        maxBytes=config.general.log_file_max_bytes,
        backupCount=config.general.log_file_backup_count,
        encoding="utf-8",
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    return handler


def setup_logging(config: AppConfig, *, level_name: str | None = None) -> Path:
    """Configure root logging handlers.

    Returns the path to the primary log file for reference or tests.
    """

    effective_level = (level_name or config.general.log_level).upper()
    levelno, levelname = _parse_level(effective_level)
    global _current_levelno, _current_levelname
    _current_levelno = levelno
    _current_levelname = levelname
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.setLevel(levelno)

    filter_ = SeverityOverrideFilter(config.general.log_overrides)

    console_handler = build_console_handler(effective_level)
    console_handler.addFilter(filter_)
    root.addHandler(console_handler)

    file_handler = build_file_handler(config)
    file_handler.addFilter(filter_)
    root.addHandler(file_handler)

    logging.captureWarnings(True)

    # Make sure uvicorn and other libraries propagate to the root logger
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(logger_name).handlers.clear()
        logging.getLogger(logger_name).propagate = True

    return config.general.data_dir / "logs" / config.general.log_file_name


def log_subprocess_output(process: "subprocess.Popen[str]", logger: logging.Logger, *, category: str, level: str = "DEBUG") -> None:
    """Stream a subprocess' stdout/stderr into the logger."""
    levelno, levelname = _parse_level(level)
    if process.stdout is None:
        return
    for line in process.stdout:
        text = line.rstrip()
        if not text:
            continue
        logger.log(levelno, text, extra={"log_category": category, "force_level": levelname})


class WebSocketLogHandler(logging.Handler):
    """Log handler that ships log records over the WebSocket bus."""

    def __init__(self, loop: asyncio.AbstractEventLoop, overrides: Mapping[str, str]):
        super().__init__(level=logging.DEBUG)
        self.loop = loop
        self.addFilter(SeverityOverrideFilter(overrides))
        self._service_keywords = ["notes", "reminders", "passwords", "photos", "scheduler", "api"]

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("uvicorn.access"):
            return

        service = getattr(record, "log_service", None) or self._infer_service(record)
        if service is None:
            service = "api"

        message = self._format_message(record)

        try:
            if self.loop.is_closed():
                return
        except RuntimeError:
            return

        try:
            from icloudbridge.api.websocket import send_log_entry

            coro = send_log_entry(service, record.levelname, message)
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        except RuntimeError:
            # Loop is closed
            return
        except Exception:
            # Never let logging errors bubble up
            return

    def _infer_service(self, record: logging.LogRecord) -> str | None:
        name = record.name.lower()
        for keyword in self._service_keywords:
            if keyword in name:
                return keyword
        return getattr(record, "service", None)

    @staticmethod
    def _format_message(record: logging.LogRecord) -> str:
        try:
            return record.getMessage()
        except Exception:
            return str(record.msg)


_ws_handler: WebSocketLogHandler | None = None


def attach_websocket_log_handler(loop: asyncio.AbstractEventLoop, config: AppConfig) -> None:
    """Attach the WebSocket log handler to the root logger."""

    global _ws_handler

    root = logging.getLogger()
    if _ws_handler is not None:
        root.removeHandler(_ws_handler)
        _ws_handler.close()
        _ws_handler = None

    handler = WebSocketLogHandler(loop, config.general.log_overrides)
    handler.setLevel(_current_levelno)
    root.addHandler(handler)
    _ws_handler = handler


def set_logging_level(level_name: str) -> None:
    """Change logging level for all handlers at runtime."""

    levelno, levelname = _parse_level(level_name)
    global _current_levelno, _current_levelname
    _current_levelno = levelno
    _current_levelname = levelname

    root = logging.getLogger()
    root.setLevel(levelno)
    for handler in root.handlers:
        handler.setLevel(levelno)

    if _ws_handler is not None:
        _ws_handler.setLevel(levelno)


def get_current_log_level() -> str:
    """Return the currently active logging level."""

    return _current_levelname
