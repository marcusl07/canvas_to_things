"""Runtime guards for the local Things deadline sync companion."""

from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

APP_SUPPORT_DIR = Path.home() / "Library/Application Support/canvas_to_things"
LOCK_PATH = APP_SUPPORT_DIR / "local_sync.lock"
START_TIME_EPSILON_SECONDS = 1.0


class LocalSyncRuntimeError(RuntimeError):
    """Base error for local-sync runtime guard failures."""


class LocalSyncLockError(LocalSyncRuntimeError):
    """Raised when another local-sync run already holds the lock."""


class LocalSyncTimeoutError(LocalSyncRuntimeError):
    """Raised when the wall-clock timeout is exceeded."""


@dataclass(frozen=True)
class LocalSyncLockInfo:
    """Persisted metadata for the active local-sync process lock."""

    pid: int
    process_started_at: float
    hostname: str
    config_path: Path

    def to_payload(self) -> dict[str, object]:
        return {
            "pid": self.pid,
            "process_started_at": self.process_started_at,
            "hostname": self.hostname,
            "config_path": str(self.config_path),
        }


@dataclass
class LocalSyncTimeoutGuard:
    """Wall-clock timeout guard for one local-sync run."""

    timeout_seconds: float
    started_at: float
    _monotonic: Callable[[], float] = time.monotonic

    @classmethod
    def start(
        cls,
        timeout_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> "LocalSyncTimeoutGuard":
        return cls(timeout_seconds=timeout_seconds, started_at=monotonic(), _monotonic=monotonic)

    def elapsed_seconds(self) -> float:
        return self._monotonic() - self.started_at

    def remaining_seconds(self) -> float:
        return max(0.0, self.timeout_seconds - self.elapsed_seconds())

    def check_pre_apply(self) -> None:
        self._check("before apply")

    def check_result_step_boundary(self, step_name: str | None = None) -> None:
        if step_name:
            self._check(f"between task result steps ({step_name})")
            return
        self._check("between task result steps")

    def _check(self, stage: str) -> None:
        elapsed = self.elapsed_seconds()
        if elapsed >= self.timeout_seconds:
            raise LocalSyncTimeoutError(
                f"Local sync timeout exceeded {stage} after {elapsed:.1f}s "
                f"(limit: {self.timeout_seconds:.1f}s)."
            )


def acquire_local_sync_lock(
    config_path: Path,
    *,
    lock_path: Path = LOCK_PATH,
    pid: int | None = None,
    hostname: str | None = None,
    process_started_at: float | None = None,
    pid_is_live: Callable[[int], bool] | None = None,
    get_process_started_at: Callable[[int], float | None] | None = None,
) -> LocalSyncLockInfo:
    """Acquire the local-sync runtime lock or raise if an active lock exists."""

    pid_is_live = pid_is_live or _pid_is_live
    get_process_started_at = get_process_started_at or lookup_process_started_at

    current_pid = pid if pid is not None else os.getpid()
    current_hostname = hostname if hostname is not None else socket.gethostname()
    current_started_at = (
        process_started_at if process_started_at is not None else _require_process_started_at(current_pid)
    )
    current_lock = LocalSyncLockInfo(
        pid=current_pid,
        process_started_at=current_started_at,
        hostname=current_hostname,
        config_path=config_path.resolve(),
    )

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _write_lock_info(lock_path, current_lock, exclusive=True)
        return current_lock
    except FileExistsError:
        pass

    existing_lock = read_local_sync_lock(lock_path)
    if existing_lock is not None and _is_active_lock(
        existing_lock,
        current_hostname=current_hostname,
        pid_is_live=pid_is_live,
        get_process_started_at=get_process_started_at,
    ):
        raise LocalSyncLockError(
            f"Local sync is already running under pid {existing_lock.pid} "
            f"on {existing_lock.hostname} using {existing_lock.config_path}."
        )

    _write_lock_info(lock_path, current_lock, exclusive=False)
    return current_lock


@contextmanager
def local_sync_lock(
    config_path: Path,
    *,
    lock_path: Path = LOCK_PATH,
    pid: int | None = None,
    hostname: str | None = None,
    process_started_at: float | None = None,
    pid_is_live: Callable[[int], bool] | None = None,
    get_process_started_at: Callable[[int], float | None] | None = None,
) -> Iterator[LocalSyncLockInfo]:
    """Context manager that acquires and then releases the local-sync lock."""

    lock_info = acquire_local_sync_lock(
        config_path,
        lock_path=lock_path,
        pid=pid,
        hostname=hostname,
        process_started_at=process_started_at,
        pid_is_live=pid_is_live,
        get_process_started_at=get_process_started_at,
    )
    try:
        yield lock_info
    finally:
        release_local_sync_lock(lock_info, lock_path=lock_path)


def read_local_sync_lock(lock_path: Path = LOCK_PATH) -> LocalSyncLockInfo | None:
    """Read the current lock file when it is present and well-formed."""

    if not lock_path.exists():
        return None

    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    pid = payload.get("pid")
    process_started_at = payload.get("process_started_at")
    hostname = payload.get("hostname")
    config_path = payload.get("config_path")
    if not isinstance(pid, int) or pid <= 0:
        return None
    if not isinstance(hostname, str) or not hostname.strip():
        return None
    if not isinstance(config_path, str) or not config_path.strip():
        return None
    if not isinstance(process_started_at, (int, float)):
        return None
    process_started_at = float(process_started_at)
    if not math.isfinite(process_started_at) or process_started_at <= 0:
        return None

    return LocalSyncLockInfo(
        pid=pid,
        process_started_at=process_started_at,
        hostname=hostname,
        config_path=Path(config_path),
    )


def release_local_sync_lock(lock_info: LocalSyncLockInfo, *, lock_path: Path = LOCK_PATH) -> None:
    """Release the lock only if it is still owned by the provided lock info."""

    existing_lock = read_local_sync_lock(lock_path)
    if existing_lock != lock_info:
        return

    try:
        lock_path.unlink()
    except FileNotFoundError:
        return


def lookup_process_started_at(pid: int) -> float | None:
    """Resolve a process start timestamp in epoch seconds via ps."""

    if pid <= 0:
        return None

    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None

    text = result.stdout.strip()
    if not text:
        return None

    try:
        started_struct = time.strptime(text, "%a %b %d %H:%M:%S %Y")
    except ValueError:
        return None
    return float(time.mktime(started_struct))


def _require_process_started_at(pid: int) -> float:
    started_at = lookup_process_started_at(pid)
    if started_at is None:
        raise LocalSyncRuntimeError(f"Could not determine process start time for pid {pid}.")
    return started_at


def _is_active_lock(
    lock_info: LocalSyncLockInfo,
    *,
    current_hostname: str,
    pid_is_live: Callable[[int], bool],
    get_process_started_at: Callable[[int], float | None],
) -> bool:
    if lock_info.hostname != current_hostname:
        return False
    if not pid_is_live(lock_info.pid):
        return False

    live_started_at = get_process_started_at(lock_info.pid)
    if live_started_at is None:
        return False

    return lock_info.process_started_at + START_TIME_EPSILON_SECONDS >= live_started_at


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _write_lock_info(lock_path: Path, lock_info: LocalSyncLockInfo, *, exclusive: bool) -> None:
    payload = json.dumps(lock_info.to_payload(), indent=2, sort_keys=True) + "\n"

    if exclusive:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        return

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=lock_path.parent,
            prefix=f"{lock_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(payload)
            temp_path = Path(handle.name)
        os.replace(temp_path, lock_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)


__all__ = [
    "APP_SUPPORT_DIR",
    "LOCK_PATH",
    "LocalSyncLockError",
    "LocalSyncLockInfo",
    "LocalSyncRuntimeError",
    "LocalSyncTimeoutError",
    "LocalSyncTimeoutGuard",
    "START_TIME_EPSILON_SECONDS",
    "acquire_local_sync_lock",
    "local_sync_lock",
    "lookup_process_started_at",
    "read_local_sync_lock",
    "release_local_sync_lock",
]
