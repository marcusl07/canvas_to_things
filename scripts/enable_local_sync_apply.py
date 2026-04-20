#!/usr/bin/env python3
"""Switch the local-sync LaunchAgent into apply mode and run one immediate sync."""

from __future__ import annotations

import argparse
import os
import plistlib
import sys
from pathlib import Path
from typing import Any, Mapping
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.setup_local_sync import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    DEFAULT_LAUNCH_AGENT_PATH,
    LocalSyncSetupError,
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
) -> None:
    if not launch_agent_path.exists():
        raise LocalSyncSetupError(
            f"LaunchAgent plist not found at {launch_agent_path}. Run scripts/setup_local_sync.py first."
        )

    existing_payload = load_launch_agent_plist(launch_agent_path)
    payload = build_apply_launch_agent_plist(existing_payload, config_path=config_path)
    run_command(payload["ProgramArguments"], check=True, env=build_launch_agent_environment(payload))
    set_local_sync_mode(config_path, "apply")
    write_launch_agent_plist(launch_agent_path, payload)
    reload_launch_agent(launch_agent_path)


def load_launch_agent_plist(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        payload = plistlib.load(handle)

    if not isinstance(payload, dict):
        raise LocalSyncSetupError(f"LaunchAgent plist at {path} must contain a top-level mapping.")
    return payload


def build_apply_launch_agent_plist(
    payload: Mapping[str, Any],
    *,
    config_path: Path,
) -> dict[str, object]:
    working_directory = payload.get("WorkingDirectory")
    if not isinstance(working_directory, str) or not working_directory.strip():
        raise LocalSyncSetupError("Installed LaunchAgent is missing WorkingDirectory.")

    program_arguments = payload.get("ProgramArguments")
    if not isinstance(program_arguments, list) or not program_arguments:
        raise LocalSyncSetupError("Installed LaunchAgent is missing ProgramArguments.")
    if not all(isinstance(argument, str) and argument for argument in program_arguments):
        raise LocalSyncSetupError("Installed LaunchAgent has invalid ProgramArguments.")

    updated_payload = dict(payload)
    updated_payload["ProgramArguments"] = build_sync_command(
        config_path=config_path,
        mode="apply",
        repo_root=Path(working_directory),
        python_executable=program_arguments[0],
    )
    return updated_payload


def build_launch_agent_environment(payload: Mapping[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    raw_environment = payload.get("EnvironmentVariables")
    if raw_environment is None:
        return env
    if not isinstance(raw_environment, dict):
        raise LocalSyncSetupError("Installed LaunchAgent has invalid EnvironmentVariables.")

    for key, value in raw_environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise LocalSyncSetupError("Installed LaunchAgent has non-string environment entries.")
        env[key] = value
    return env


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
