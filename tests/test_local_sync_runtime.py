import json
from pathlib import Path

import pytest

from canvas_things.local_sync_runtime import (
    LocalSyncLockError,
    LocalSyncLockInfo,
    LocalSyncTimeoutError,
    LocalSyncTimeoutGuard,
    START_TIME_EPSILON_SECONDS,
    acquire_local_sync_lock,
    read_local_sync_lock,
    release_local_sync_lock,
)


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now


def write_lock(lock_path: Path, lock_info: LocalSyncLockInfo) -> None:
    lock_path.write_text(json.dumps(lock_info.to_payload()), encoding="utf-8")


def test_acquire_local_sync_lock_writes_expected_metadata(tmp_path):
    lock_path = tmp_path / "local_sync.lock"
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\n", encoding="utf-8")

    lock_info = acquire_local_sync_lock(
        config_path,
        lock_path=lock_path,
        pid=1234,
        hostname="test-host",
        process_started_at=456.0,
    )

    assert lock_info == LocalSyncLockInfo(
        pid=1234,
        process_started_at=456.0,
        hostname="test-host",
        config_path=config_path.resolve(),
    )
    assert read_local_sync_lock(lock_path) == lock_info

    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert payload == {
        "config_path": str(config_path.resolve()),
        "hostname": "test-host",
        "pid": 1234,
        "process_started_at": 456.0,
    }

    release_local_sync_lock(lock_info, lock_path=lock_path)
    assert not lock_path.exists()


def test_acquire_local_sync_lock_rejects_active_lock(tmp_path):
    lock_path = tmp_path / "local_sync.lock"
    existing = LocalSyncLockInfo(
        pid=222,
        process_started_at=100.0,
        hostname="shared-host",
        config_path=tmp_path / "existing.yml",
    )
    write_lock(lock_path, existing)

    with pytest.raises(LocalSyncLockError, match="already running under pid 222"):
        acquire_local_sync_lock(
            tmp_path / "next.yml",
            lock_path=lock_path,
            pid=333,
            hostname="shared-host",
            process_started_at=200.0,
            pid_is_live=lambda pid: pid == 222,
            get_process_started_at=lambda pid: 100.0,
        )

    assert read_local_sync_lock(lock_path) == existing


def test_acquire_local_sync_lock_replaces_dead_lock(tmp_path):
    lock_path = tmp_path / "local_sync.lock"
    write_lock(
        lock_path,
        LocalSyncLockInfo(
            pid=222,
            process_started_at=100.0,
            hostname="shared-host",
            config_path=tmp_path / "existing.yml",
        ),
    )

    lock_info = acquire_local_sync_lock(
        tmp_path / "next.yml",
        lock_path=lock_path,
        pid=333,
        hostname="shared-host",
        process_started_at=200.0,
        pid_is_live=lambda pid: False,
        get_process_started_at=lambda pid: None,
    )

    assert read_local_sync_lock(lock_path) == lock_info
    assert lock_info.pid == 333


def test_acquire_local_sync_lock_replaces_lock_from_other_host(tmp_path):
    lock_path = tmp_path / "local_sync.lock"
    write_lock(
        lock_path,
        LocalSyncLockInfo(
            pid=222,
            process_started_at=100.0,
            hostname="other-host",
            config_path=tmp_path / "existing.yml",
        ),
    )

    lock_info = acquire_local_sync_lock(
        tmp_path / "next.yml",
        lock_path=lock_path,
        pid=333,
        hostname="shared-host",
        process_started_at=200.0,
        pid_is_live=lambda pid: True,
        get_process_started_at=lambda pid: 100.0,
    )

    assert read_local_sync_lock(lock_path) == lock_info
    assert lock_info.hostname == "shared-host"


def test_acquire_local_sync_lock_replaces_unverifiable_lock_file(tmp_path):
    lock_path = tmp_path / "local_sync.lock"
    lock_path.write_text("{not json", encoding="utf-8")

    lock_info = acquire_local_sync_lock(
        tmp_path / "config.yml",
        lock_path=lock_path,
        pid=333,
        hostname="shared-host",
        process_started_at=200.0,
        pid_is_live=lambda pid: True,
        get_process_started_at=lambda pid: 200.0,
    )

    assert read_local_sync_lock(lock_path) == lock_info


def test_acquire_local_sync_lock_replaces_stale_pid_reuse_lock(tmp_path):
    lock_path = tmp_path / "local_sync.lock"
    write_lock(
        lock_path,
        LocalSyncLockInfo(
            pid=222,
            process_started_at=100.0,
            hostname="shared-host",
            config_path=tmp_path / "existing.yml",
        ),
    )

    lock_info = acquire_local_sync_lock(
        tmp_path / "config.yml",
        lock_path=lock_path,
        pid=333,
        hostname="shared-host",
        process_started_at=200.0,
        pid_is_live=lambda pid: True,
        get_process_started_at=lambda pid: 100.0 + START_TIME_EPSILON_SECONDS + 0.5,
    )

    assert read_local_sync_lock(lock_path) == lock_info
    assert lock_info.pid == 333


def test_acquire_local_sync_lock_replaces_lock_when_start_time_cannot_be_verified(tmp_path):
    lock_path = tmp_path / "local_sync.lock"
    write_lock(
        lock_path,
        LocalSyncLockInfo(
            pid=222,
            process_started_at=100.0,
            hostname="shared-host",
            config_path=tmp_path / "existing.yml",
        ),
    )

    lock_info = acquire_local_sync_lock(
        tmp_path / "config.yml",
        lock_path=lock_path,
        pid=333,
        hostname="shared-host",
        process_started_at=200.0,
        pid_is_live=lambda pid: True,
        get_process_started_at=lambda pid: None,
    )

    assert read_local_sync_lock(lock_path) == lock_info


def test_release_local_sync_lock_leaves_newer_owner_alone(tmp_path):
    lock_path = tmp_path / "local_sync.lock"
    original = LocalSyncLockInfo(
        pid=111,
        process_started_at=10.0,
        hostname="host",
        config_path=tmp_path / "old.yml",
    )
    replacement = LocalSyncLockInfo(
        pid=222,
        process_started_at=20.0,
        hostname="host",
        config_path=tmp_path / "new.yml",
    )
    write_lock(lock_path, replacement)

    release_local_sync_lock(original, lock_path=lock_path)

    assert read_local_sync_lock(lock_path) == replacement


def test_timeout_guard_allows_work_before_deadline():
    clock = FakeClock(100.0)
    guard = LocalSyncTimeoutGuard.start(10.0, monotonic=clock.monotonic)

    clock.now = 109.9
    guard.check_pre_apply()
    guard.check_result_step_boundary("task-1")
    assert guard.remaining_seconds() == pytest.approx(0.1)


def test_timeout_guard_raises_at_pre_apply_boundary():
    clock = FakeClock(100.0)
    guard = LocalSyncTimeoutGuard.start(10.0, monotonic=clock.monotonic)

    clock.now = 110.0
    with pytest.raises(LocalSyncTimeoutError, match="before apply"):
        guard.check_pre_apply()


def test_timeout_guard_raises_between_result_steps():
    clock = FakeClock(100.0)
    guard = LocalSyncTimeoutGuard.start(10.0, monotonic=clock.monotonic)

    clock.now = 115.0
    with pytest.raises(LocalSyncTimeoutError, match="between task result steps \\(task-7\\)"):
        guard.check_result_step_boundary("task-7")
