from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from canvas_things.local_sync_if_due_main import main
from canvas_things.local_sync_state import LocalSyncRunState, write_local_sync_run_state


def test_main_skips_silently_when_last_run_is_inside_interval(tmp_path: Path, capsys):
    state_path = tmp_path / "state.json"
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: apply\n", encoding="utf-8")
    now = datetime(2026, 4, 28, 23, 0, tzinfo=timezone.utc)
    write_local_sync_run_state(
        state_path,
        state=LocalSyncRunState(last_finished_at=datetime(2026, 4, 28, 22, 0, tzinfo=timezone.utc)),
    )
    sync_calls: list[list[str]] = []

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-path",
            str(state_path),
            "--sync-interval-seconds",
            "7200",
            "--apply",
        ],
        now_fn=lambda: now,
        run_sync=lambda argv: sync_calls.append(argv) or 0,
    )

    captured = capsys.readouterr()

    assert exit_code == 0
    assert sync_calls == []
    assert captured.out == ""
    assert captured.err == ""


def test_main_runs_and_records_state_when_due(tmp_path: Path):
    state_path = tmp_path / "state.json"
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: apply\n", encoding="utf-8")
    times = iter(
        [
            datetime(2026, 4, 28, 23, 0, tzinfo=timezone.utc),
            datetime(2026, 4, 28, 23, 0, 2, tzinfo=timezone.utc),
        ]
    )
    write_local_sync_run_state(
        state_path,
        state=LocalSyncRunState(last_finished_at=datetime(2026, 4, 28, 20, 0, tzinfo=timezone.utc)),
    )
    sync_calls: list[list[str]] = []

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-path",
            str(state_path),
            "--sync-interval-seconds",
            "7200",
            "--apply",
        ],
        now_fn=lambda: next(times),
        run_sync=lambda argv: sync_calls.append(argv) or 0,
    )

    assert exit_code == 0
    assert sync_calls == [["--config", str(config_path), "--apply"]]


def test_main_runs_when_no_state_exists(tmp_path: Path):
    state_path = tmp_path / "state.json"
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: dry-run\n", encoding="utf-8")
    now = datetime(2026, 4, 28, 23, 0, tzinfo=timezone.utc)
    sync_calls: list[list[str]] = []

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-path",
            str(state_path),
            "--sync-interval-seconds",
            "7200",
            "--dry-run",
        ],
        now_fn=lambda: now,
        run_sync=lambda argv: sync_calls.append(argv) or 0,
    )

    assert exit_code == 0
    assert sync_calls == [["--config", str(config_path), "--dry-run"]]


def test_main_force_runs_inside_interval(tmp_path: Path):
    state_path = tmp_path / "state.json"
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: apply\n", encoding="utf-8")
    now = datetime(2026, 4, 28, 23, 0, tzinfo=timezone.utc)
    write_local_sync_run_state(
        state_path,
        state=LocalSyncRunState(last_finished_at=datetime(2026, 4, 28, 22, 59, tzinfo=timezone.utc)),
    )
    sync_calls: list[list[str]] = []

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--state-path",
            str(state_path),
            "--sync-interval-seconds",
            "7200",
            "--apply",
            "--force",
        ],
        now_fn=lambda: now,
        run_sync=lambda argv: sync_calls.append(argv) or 0,
    )

    assert exit_code == 0
    assert sync_calls == [["--config", str(config_path), "--apply"]]
