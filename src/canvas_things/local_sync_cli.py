"""Argument parsing for the local Things deadline sync companion."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from .local_sync_config import (
    CONFIG_PATH,
    EXIT_CODE_MEANINGS,
    LocalSyncExitCode,
    LocalSyncOverrides,
    LocalSyncSettings,
    load_local_sync_config,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the local-sync CLI parser."""

    parser = argparse.ArgumentParser(
        description="Sync Canvas-managed deadlines from Things notes back into local Things tasks.",
        epilog=_build_exit_code_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help="Path to config.yml (defaults to config/config.yml).",
    )
    parser.add_argument(
        "--project",
        help="Restrict discovery to a named Things project. Omit to scan Inbox.",
    )
    parser.add_argument(
        "--move-to-project",
        help="Move canonical managed tasks into this project when applying changes.",
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview mutations without writing to Things.",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        help="Apply due-date, project, and trash mutations to Things.",
    )

    parser.add_argument(
        "--candidate-cap",
        type=int,
        help="Maximum number of managed candidate tasks allowed in one run.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        help="Wall-clock timeout for the full run.",
    )
    return parser


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse local-sync CLI arguments."""

    parser = build_parser()
    return parser.parse_args(list(argv) if argv is not None else None)


def args_to_overrides(args: argparse.Namespace) -> LocalSyncOverrides:
    """Convert parsed args into config override values."""

    mode = None
    if args.dry_run:
        mode = "dry-run"
    elif args.apply:
        mode = "apply"

    return LocalSyncOverrides(
        project=args.project,
        move_to_project=args.move_to_project,
        mode=mode,
        candidate_cap=args.candidate_cap,
        timeout_seconds=args.timeout_seconds,
    )


def load_settings_from_argv(argv: Iterable[str] | None = None) -> tuple[argparse.Namespace, LocalSyncSettings]:
    """Parse argv and resolve the final local-sync settings."""

    args = parse_args(argv)
    settings = load_local_sync_config(args.config, overrides=args_to_overrides(args))
    return args, settings


def _build_exit_code_help() -> str:
    lines = ["Exit codes:"]
    for exit_code in LocalSyncExitCode:
        lines.append(f"  {int(exit_code)} = {EXIT_CODE_MEANINGS[exit_code]}")
    return "\n".join(lines)


__all__ = [
    "args_to_overrides",
    "build_parser",
    "load_settings_from_argv",
    "parse_args",
]
