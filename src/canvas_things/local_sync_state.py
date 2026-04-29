"""Small JSON state file for local-sync catch-up scheduling."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path("~/Library/Application Support/canvas_to_things/local_sync_state.json")


class LocalSyncStateError(RuntimeError):
    """Raised when the local-sync state file cannot be read or written."""


@dataclass(frozen=True)
class LocalSyncRunState:
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_exit_code: int | None = None
    last_success_at: datetime | None = None


def resolve_local_sync_state_path(path: str | Path | None = None) -> Path:
    candidate = DEFAULT_STATE_PATH if path is None else Path(path)
    return candidate.expanduser()


def load_local_sync_run_state(path: str | Path | None = None) -> LocalSyncRunState:
    state_path = resolve_local_sync_state_path(path)
    if not state_path.exists():
        return LocalSyncRunState()

    try:
        with state_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise LocalSyncStateError(f"Failed to read local-sync state at {state_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise LocalSyncStateError(f"Local-sync state at {state_path} must contain a JSON object.")

    return LocalSyncRunState(
        last_started_at=_parse_optional_datetime(data.get("last_started_at"), field_name="last_started_at"),
        last_finished_at=_parse_optional_datetime(data.get("last_finished_at"), field_name="last_finished_at"),
        last_exit_code=_parse_optional_int(data.get("last_exit_code"), field_name="last_exit_code"),
        last_success_at=_parse_optional_datetime(data.get("last_success_at"), field_name="last_success_at"),
    )


def write_local_sync_run_state(
    path: str | Path | None = None,
    *,
    state: LocalSyncRunState,
) -> Path:
    state_path = resolve_local_sync_state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_started_at": _format_optional_datetime(state.last_started_at),
        "last_finished_at": _format_optional_datetime(state.last_finished_at),
        "last_exit_code": state.last_exit_code,
        "last_success_at": _format_optional_datetime(state.last_success_at),
    }
    try:
        with state_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except OSError as exc:
        raise LocalSyncStateError(f"Failed to write local-sync state at {state_path}: {exc}") from exc
    return state_path


def mark_local_sync_started(
    path: str | Path | None = None,
    *,
    started_at: datetime,
) -> LocalSyncRunState:
    previous = load_local_sync_run_state(path)
    state = LocalSyncRunState(
        last_started_at=_normalize_datetime(started_at),
        last_finished_at=previous.last_finished_at,
        last_exit_code=previous.last_exit_code,
        last_success_at=previous.last_success_at,
    )
    write_local_sync_run_state(path, state=state)
    return state


def mark_local_sync_finished(
    path: str | Path | None = None,
    *,
    started_at: datetime,
    finished_at: datetime,
    exit_code: int,
) -> LocalSyncRunState:
    normalized_finished = _normalize_datetime(finished_at)
    previous = load_local_sync_run_state(path)
    state = LocalSyncRunState(
        last_started_at=_normalize_datetime(started_at),
        last_finished_at=normalized_finished,
        last_exit_code=exit_code,
        last_success_at=normalized_finished if exit_code == 0 else previous.last_success_at,
    )
    write_local_sync_run_state(path, state=state)
    return state


def _parse_optional_datetime(value: Any, *, field_name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LocalSyncStateError(f"Local-sync state field {field_name} must be an ISO timestamp or null.")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise LocalSyncStateError(f"Local-sync state field {field_name} is not a valid ISO timestamp.") from exc
    return _normalize_datetime(parsed)


def _parse_optional_int(value: Any, *, field_name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise LocalSyncStateError(f"Local-sync state field {field_name} must be an integer or null.")
    return value


def _format_optional_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _normalize_datetime(value).isoformat()


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "DEFAULT_STATE_PATH",
    "LocalSyncRunState",
    "LocalSyncStateError",
    "load_local_sync_run_state",
    "mark_local_sync_finished",
    "mark_local_sync_started",
    "resolve_local_sync_state_path",
    "write_local_sync_run_state",
]
