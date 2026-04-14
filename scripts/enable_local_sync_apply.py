#!/usr/bin/env python3
"""Switch the local-sync LaunchAgent into apply mode and run one immediate sync."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.setup_local_sync import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    DEFAULT_LAUNCH_AGENT_PATH,
    DEFAULT_STDERR_LOG_PATH,
    DEFAULT_STDOUT_LOG_PATH,
    LocalSyncSetupError,
    build_launch_agent_plist,
    build_sync_command,
    reload_launch_agent,
    set_local_sync_mode,
    run_command,
    write_launch_agent_plist,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enable automatic local-sync writes and run one immediate apply sync.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the local sync config file.",
    )
    parser.add_argument(
        "--launch-agent-path",
        type=Path,
        default=DEFAULT_LAUNCH_AGENT_PATH,
        help="Path to the installed LaunchAgent plist.",
    )
    return parser


def enable_automatic_writes(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    launch_agent_path: Path = DEFAULT_LAUNCH_AGENT_PATH,
    stdout_log_path: Path = DEFAULT_STDOUT_LOG_PATH,
    stderr_log_path: Path = DEFAULT_STDERR_LOG_PATH,
) -> None:
    if not launch_agent_path.exists():
        raise LocalSyncSetupError(
            f"LaunchAgent plist not found at {launch_agent_path}. Run scripts/setup_local_sync.py first."
        )

    set_local_sync_mode(config_path, "apply")
    payload = build_launch_agent_plist(
        config_path=config_path,
        mode="apply",
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
    )
    write_launch_agent_plist(launch_agent_path, payload)
    reload_launch_agent(launch_agent_path)

    env = dict(os.environ)
    env["PYTHONPATH"] = payload["EnvironmentVariables"]["PYTHONPATH"]  # type: ignore[index]
    run_command(build_sync_command(config_path=config_path, mode="apply"), check=True, env=env)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        enable_automatic_writes(
            config_path=args.config,
            launch_agent_path=args.launch_agent_path,
        )
    except LocalSyncSetupError as exc:
        print(f"Enable automatic writes failed: {exc}", file=sys.stderr)
        return 1

    print(f"Enabled apply mode in {args.launch_agent_path}.")
    print("Ran one immediate apply sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
