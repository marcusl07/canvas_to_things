"""File-backed logging helpers for the local Things sync companion."""

from __future__ import annotations

import logging
from pathlib import Path

DEFAULT_LOG_PATH = Path("~/Library/Logs/canvas_to_things/local_sync.log")
DEFAULT_LOGGER_NAME = "canvas_things.local_sync"
DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
MAX_LOG_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5


def resolve_local_sync_log_path(path: str | Path | None = None) -> Path:
    """Resolve the local-sync log path with user-home expansion."""

    candidate = DEFAULT_LOG_PATH if path is None else Path(path)
    return candidate.expanduser()


def rotate_local_sync_log(
    path: str | Path | None = None,
    *,
    max_bytes: int = MAX_LOG_BYTES,
    backup_count: int = BACKUP_COUNT,
) -> Path:
    """Rotate the current log file when it exceeds the configured size."""

    log_path = resolve_local_sync_log_path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not log_path.exists():
        return log_path
    if not log_path.is_file():
        raise OSError(f"Local sync log path is not a regular file: {log_path}")
    if log_path.stat().st_size <= max_bytes:
        return log_path

    oldest_backup = _backup_path(log_path, backup_count)
    if oldest_backup.exists():
        oldest_backup.unlink()

    for index in range(backup_count - 1, 0, -1):
        backup = _backup_path(log_path, index)
        if backup.exists():
            backup.replace(_backup_path(log_path, index + 1))

    log_path.replace(_backup_path(log_path, 1))
    return log_path


def setup_local_sync_logger(
    *,
    logger_name: str = DEFAULT_LOGGER_NAME,
    log_path: str | Path | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure and return the dedicated local-sync file logger."""

    resolved_path = rotate_local_sync_log(log_path)
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = logging.FileHandler(resolved_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(DEFAULT_LOG_FORMAT))
    logger.addHandler(file_handler)
    return logger


def _backup_path(log_path: Path, index: int) -> Path:
    return Path(f"{log_path}.{index}")


__all__ = [
    "BACKUP_COUNT",
    "DEFAULT_LOG_FORMAT",
    "DEFAULT_LOGGER_NAME",
    "DEFAULT_LOG_PATH",
    "MAX_LOG_BYTES",
    "resolve_local_sync_log_path",
    "rotate_local_sync_log",
    "setup_local_sync_logger",
]
