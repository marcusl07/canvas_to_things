"""Run the local sync only when the scheduled interval has elapsed."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

from . import local_sync_main
from .local_sync_config import CONFIG_PATH, LocalSyncExitCode
from .local_sync_state import (
    DEFAULT_STATE_PATH,
    LocalSyncStateError,
    load_local_sync_run_state,
    mark_local_sync_finished,
    mark_local_sync_started,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run local Canvas→Things sync only when its scheduled interval is due.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to config.yml (defaults to config/config.yml).",
    )
    parser.add_argument(
        "--state-path",
        type=Path,
        default=DEFAULT_STATE_PATH,
        help="Path to the catch-up state JSON file.",
    )
    parser.add_argument(
        "--sync-interval-seconds",
        type=float,
        required=True,
        help="Minimum elapsed seconds between completed local-sync runs.",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--dry-run", action="store_true", help="Run the underlying sync in dry-run mode.")
    mode_group.add_argument("--apply", action="store_true", help="Run the underlying sync in apply mode.")

    parser.add_argument(
        "--force",
        action="store_true",
        help="Run the underlying sync regardless of the state timestamp.",
    )
    parser.add_argument(
        "--verbose-skip",
        action="store_true",
        help="Print a message when the underlying sync is skipped.",
    )
    return parser


def main(
    argv: Iterable[str] | None = None,
    *,
    now_fn: Callable[[], datetime] | None = None,
    run_sync: Callable[[list[str]], int] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.sync_interval_seconds <= 0:
        print("--sync-interval-seconds must be greater than 0.", file=sys.stderr)
        return int(LocalSyncExitCode.CONFIG_ERROR)

    now = _normalize_datetime((now_fn or _utc_now)())
    try:
        state = load_local_sync_run_state(args.state_path)
    except LocalSyncStateError as exc:
        print(f"Local sync state error: {exc}", file=sys.stderr)
        return int(LocalSyncExitCode.UNEXPECTED_ERROR)

    if not args.force and state.last_finished_at is not None:
        next_due_at = state.last_finished_at + timedelta(seconds=args.sync_interval_seconds)
        if now < next_due_at:
            if args.verbose_skip:
                print(f"Local sync not due until {next_due_at.isoformat()}.")
            return int(LocalSyncExitCode.SUCCESS)

    started_at = now
    try:
        mark_local_sync_started(args.state_path, started_at=started_at)
    except LocalSyncStateError as exc:
        print(f"Local sync state error: {exc}", file=sys.stderr)
        return int(LocalSyncExitCode.UNEXPECTED_ERROR)

    sync_argv = _build_sync_argv(args)
    exit_code = int((run_sync or local_sync_main.main)(sync_argv))
    finished_at = _normalize_datetime((now_fn or _utc_now)())
    try:
        mark_local_sync_finished(
            args.state_path,
            started_at=started_at,
            finished_at=finished_at,
            exit_code=exit_code,
        )
    except LocalSyncStateError as exc:
        print(f"Local sync state error: {exc}", file=sys.stderr)
        return int(LocalSyncExitCode.UNEXPECTED_ERROR)
    return exit_code


def _build_sync_argv(args: argparse.Namespace) -> list[str]:
    sync_argv = ["--config", str(args.config)]
    if args.dry_run:
        sync_argv.append("--dry-run")
    elif args.apply:
        sync_argv.append("--apply")
    return sync_argv


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
