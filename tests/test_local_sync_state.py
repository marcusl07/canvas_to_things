from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from canvas_things.local_sync_state import (
    LocalSyncRunState,
    LocalSyncStateError,
    load_local_sync_run_state,
    mark_local_sync_finished,
    mark_local_sync_started,
    write_local_sync_run_state,
)


def test_load_local_sync_run_state_returns_empty_for_missing_file(tmp_path: Path):
    assert load_local_sync_run_state(tmp_path / "missing.json") == LocalSyncRunState()


def test_write_and_load_local_sync_run_state_round_trips(tmp_path: Path):
    state_path = tmp_path / "state.json"
    timestamp = datetime(2026, 4, 28, 23, 3, 31, tzinfo=timezone.utc)

    write_local_sync_run_state(
        state_path,
        state=LocalSyncRunState(
            last_started_at=timestamp,
            last_finished_at=timestamp,
            last_exit_code=0,
            last_success_at=timestamp,
        ),
    )

    assert load_local_sync_run_state(state_path) == LocalSyncRunState(
        last_started_at=timestamp,
        last_finished_at=timestamp,
        last_exit_code=0,
        last_success_at=timestamp,
    )


def test_mark_local_sync_finished_keeps_previous_success_on_failure(tmp_path: Path):
    state_path = tmp_path / "state.json"
    previous_success = datetime(2026, 4, 28, 20, 0, tzinfo=timezone.utc)
    started = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)
    finished = datetime(2026, 4, 28, 22, 1, tzinfo=timezone.utc)
    write_local_sync_run_state(
        state_path,
        state=LocalSyncRunState(
            last_finished_at=previous_success,
            last_exit_code=0,
            last_success_at=previous_success,
        ),
    )

    state = mark_local_sync_finished(state_path, started_at=started, finished_at=finished, exit_code=4)

    assert state.last_started_at == started
    assert state.last_finished_at == finished
    assert state.last_exit_code == 4
    assert state.last_success_at == previous_success


def test_mark_local_sync_started_preserves_previous_finished_state(tmp_path: Path):
    state_path = tmp_path / "state.json"
    previous_finished = datetime(2026, 4, 28, 20, 0, tzinfo=timezone.utc)
    started = datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)
    write_local_sync_run_state(
        state_path,
        state=LocalSyncRunState(last_finished_at=previous_finished, last_exit_code=0),
    )

    state = mark_local_sync_started(state_path, started_at=started)

    assert state.last_started_at == started
    assert state.last_finished_at == previous_finished
    assert state.last_exit_code == 0


def test_load_local_sync_run_state_rejects_malformed_state(tmp_path: Path):
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"last_finished_at": "not-a-date"}), encoding="utf-8")

    with pytest.raises(LocalSyncStateError, match="last_finished_at"):
        load_local_sync_run_state(state_path)
