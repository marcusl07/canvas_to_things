import logging
from pathlib import Path

from canvas_things.local_sync_logging import (
    BACKUP_COUNT,
    MAX_LOG_BYTES,
    resolve_local_sync_log_path,
    rotate_local_sync_log,
    setup_local_sync_logger,
)


def test_resolve_local_sync_log_path_expands_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))

    resolved = resolve_local_sync_log_path()

    assert resolved == tmp_path / "Library/Logs/canvas_to_things/local_sync.log"


def test_rotate_local_sync_log_keeps_five_backups(tmp_path):
    log_path = tmp_path / "logs" / "local_sync.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_bytes(b"x" * (MAX_LOG_BYTES + 1))

    for index in range(1, BACKUP_COUNT + 1):
        Path(f"{log_path}.{index}").write_text(f"backup-{index}", encoding="utf-8")

    rotate_local_sync_log(log_path)

    assert not log_path.exists()
    assert Path(f"{log_path}.1").stat().st_size == MAX_LOG_BYTES + 1
    assert Path(f"{log_path}.2").read_text(encoding="utf-8") == "backup-1"
    assert Path(f"{log_path}.3").read_text(encoding="utf-8") == "backup-2"
    assert Path(f"{log_path}.4").read_text(encoding="utf-8") == "backup-3"
    assert Path(f"{log_path}.5").read_text(encoding="utf-8") == "backup-4"


def test_rotate_local_sync_log_skips_file_at_threshold(tmp_path):
    log_path = tmp_path / "logs" / "local_sync.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_bytes(b"x" * MAX_LOG_BYTES)

    rotate_local_sync_log(log_path)

    assert log_path.exists()
    assert not Path(f"{log_path}.1").exists()


def test_setup_local_sync_logger_is_idempotent_and_isolated(tmp_path):
    logger_name = "tests.local_sync.logging"
    log_path = tmp_path / "logs" / "local_sync.log"
    root_handlers = tuple(logging.getLogger().handlers)

    try:
        logger = setup_local_sync_logger(logger_name=logger_name, log_path=log_path)
        logger.info("first message")
        for handler in logger.handlers:
            handler.flush()

        logger = setup_local_sync_logger(logger_name=logger_name, log_path=log_path)
        logger.info("second message")
        for handler in logger.handlers:
            handler.flush()

        contents = log_path.read_text(encoding="utf-8")

        assert logger.propagate is False
        assert len(logger.handlers) == 1
        assert tuple(logging.getLogger().handlers) == root_handlers
        assert contents.count("first message") == 1
        assert contents.count("second message") == 1
    finally:
        _reset_logger(logger_name)


def _reset_logger(name: str) -> None:
    logger = logging.getLogger(name)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
