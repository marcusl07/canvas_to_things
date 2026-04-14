#!/usr/bin/env python3
"""Install the local-sync LaunchAgent in dry-run mode."""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yml"
DEFAULT_EXAMPLE_CONFIG_PATH = REPO_ROOT / "config" / "config.example.yml"
DEFAULT_START_INTERVAL_SECONDS = 2 * 60 * 60
LAUNCH_AGENT_LABEL = "com.canvas_to_things.local_sync"
DEFAULT_LAUNCH_AGENT_PATH = Path.home() / "Library/LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
DEFAULT_LOG_DIR = Path.home() / "Library/Logs/canvas_to_things"
DEFAULT_STDOUT_LOG_PATH = DEFAULT_LOG_DIR / "local_sync.launchagent.out.log"
DEFAULT_STDERR_LOG_PATH = DEFAULT_LOG_DIR / "local_sync.launchagent.err.log"
UNSET = object()


class LocalSyncSetupError(RuntimeError):
    """Raised when setup or enablement cannot complete safely."""


@dataclass(frozen=True)
class LocalSyncPromptAnswers:
    """Prompt-collected local-sync settings for the guided installer."""

    project: str | None
    move_to_project: str | None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the local Canvas→Things sync LaunchAgent in dry-run mode.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the local sync config file.",
    )
    parser.add_argument(
        "--example-config",
        type=Path,
        default=DEFAULT_EXAMPLE_CONFIG_PATH,
        help="Path to the example config used when the real config is missing.",
    )
    parser.add_argument(
        "--launch-agent-path",
        type=Path,
        default=DEFAULT_LAUNCH_AGENT_PATH,
        help="Destination plist path for the LaunchAgent.",
    )
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=DEFAULT_START_INTERVAL_SECONDS,
        help="How often the scheduled sync should run.",
    )
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="Skip the interactive local-sync setup prompts.",
    )
    return parser


def ensure_config_file(config_path: Path, example_config_path: Path) -> bool:
    """Create config/config.yml from the example when it does not yet exist."""

    if config_path.exists():
        return False
    if not example_config_path.exists():
        raise LocalSyncSetupError(
            f"Example config file not found at {example_config_path}. Cannot seed {config_path}."
        )

    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_config_path, config_path)
    return True


def load_config_data(config_path: Path) -> dict[str, Any]:
    """Load the YAML config and enforce a top-level mapping."""

    if not config_path.exists():
        raise LocalSyncSetupError(f"Config file not found at {config_path}.")

    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise LocalSyncSetupError(f"Config file {config_path} must contain a top-level mapping.")
    return data


def write_config_data(config_path: Path, data: Mapping[str, Any]) -> None:
    """Persist the YAML config with stable key ordering."""

    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(dict(data), handle, sort_keys=False)


def _optional_text(value: Any) -> str | None:
    """Normalize optional config text for reuse in prompts and writes."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise LocalSyncSetupError(f"Expected text or null in local_sync config, got {type(value).__name__}.")
    cleaned = value.strip()
    return cleaned or None


def update_local_sync_config(
    config_path: Path,
    *,
    mode: str | object = UNSET,
    project: str | None | object = UNSET,
    move_to_project: str | None | object = UNSET,
) -> None:
    """Apply targeted local_sync config updates without disturbing other settings."""

    data = load_config_data(config_path)

    local_sync = data.get("local_sync")
    if local_sync is None:
        local_sync = {}
    if not isinstance(local_sync, dict):
        raise LocalSyncSetupError(f"Config file {config_path} has a non-mapping local_sync block.")

    if mode is not UNSET:
        if mode not in {"dry-run", "apply"}:
            raise LocalSyncSetupError(f"Unsupported local sync mode {mode!r}.")
        local_sync["mode"] = mode

    if project is not UNSET:
        local_sync["project"] = _optional_text(project)

    if move_to_project is not UNSET:
        local_sync["move_to_project"] = _optional_text(move_to_project)

    data["local_sync"] = local_sync
    write_config_data(config_path, data)


def set_local_sync_mode(config_path: Path, mode: str) -> None:
    """Force the config's local_sync.mode value to the requested mode."""

    update_local_sync_config(config_path, mode=mode)


def prompt_local_sync_answers(
    config_path: Path,
    *,
    input_fn: Callable[[str], str] = input,
    output = sys.stdout,
) -> LocalSyncPromptAnswers:
    """Collect local-sync scope choices from a human-facing terminal flow."""

    data = load_config_data(config_path)
    local_sync = data.get("local_sync") or {}
    if not isinstance(local_sync, dict):
        raise LocalSyncSetupError(f"Config file {config_path} has a non-mapping local_sync block.")

    current_project = _optional_text(local_sync.get("project"))
    current_move_to_project = _optional_text(local_sync.get("move_to_project"))
    default_scope_choice = "2" if current_project else "1"

    print("Local sync setup", file=output)
    print("Press Enter to accept the default shown in brackets.", file=output)
    print("", file=output)
    print("Where should the sync look for Canvas-managed tasks?", file=output)
    print("  1. Inbox only [recommended]", file=output)
    print("  2. One exact Things project title", file=output)

    while True:
        scope_choice = input_fn(f"Choose a scope [default: {default_scope_choice}]: ").strip() or default_scope_choice
        if scope_choice in {"1", "2"}:
            break
        print("Enter 1 for Inbox or 2 for a specific project.", file=output)

    project: str | None
    if scope_choice == "1":
        project = None
    else:
        prompt_suffix = f" [{current_project}]" if current_project else ""
        while True:
            response = input_fn(f"Exact Things project title to scan{prompt_suffix}: ").strip()
            if response:
                project = response
                break
            if current_project:
                project = current_project
                break
            print("A project title is required when you choose project scope.", file=output)

    move_default_text = current_move_to_project or "keep current project placement"
    move_prompt = (
        "Move canonical tasks into a project after syncing?\n"
        f"Leave blank to {move_default_text}: "
    )
    move_response = input_fn(move_prompt).strip()
    move_to_project = move_response or current_move_to_project

    print("", file=output)
    scope_summary = "Inbox" if project is None else f'project "{project}"'
    move_summary = move_to_project or "leave tasks where they already are"
    print(f"Using scope: {scope_summary}", file=output)
    print(f"Canonical task placement: {move_summary}", file=output)
    print("Setup will still install the schedule in dry-run mode for safety.", file=output)

    return LocalSyncPromptAnswers(project=project, move_to_project=move_to_project)


def build_pythonpath(repo_root: Path, existing_pythonpath: str | None = None) -> str:
    repo_src = str((repo_root / "src").resolve())
    entries = [repo_src]
    if existing_pythonpath:
        entries.append(existing_pythonpath)
    return os.pathsep.join(entries)


def build_sync_command(
    *,
    config_path: Path,
    mode: str,
    repo_root: Path = REPO_ROOT,
    python_executable: str | None = None,
) -> list[str]:
    python_bin = python_executable or sys.executable
    return [
        python_bin,
        "-m",
        "canvas_things.local_sync_main",
        "--config",
        str(config_path.resolve()),
        "--dry-run" if mode == "dry-run" else "--apply",
    ]


def build_launch_agent_plist(
    *,
    config_path: Path,
    mode: str,
    repo_root: Path = REPO_ROOT,
    interval_seconds: int = DEFAULT_START_INTERVAL_SECONDS,
    python_executable: str | None = None,
    log_dir: Path = DEFAULT_LOG_DIR,
    stdout_log_path: Path = DEFAULT_STDOUT_LOG_PATH,
    stderr_log_path: Path = DEFAULT_STDERR_LOG_PATH,
    environment: Mapping[str, str] | None = None,
) -> dict[str, object]:
    if interval_seconds <= 0:
        raise LocalSyncSetupError("interval_seconds must be greater than 0.")

    env = dict(environment or os.environ)
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": build_sync_command(
            config_path=config_path,
            mode=mode,
            repo_root=repo_root,
            python_executable=python_executable,
        ),
        "WorkingDirectory": str(repo_root.resolve()),
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "StandardOutPath": str(stdout_log_path),
        "StandardErrorPath": str(stderr_log_path),
        "EnvironmentVariables": {
            "PATH": env.get("PATH", ""),
            "PYTHONPATH": build_pythonpath(repo_root, env.get("PYTHONPATH")),
        },
    }


def write_launch_agent_plist(plist_path: Path, payload: Mapping[str, object]) -> None:
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    Path(payload["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    Path(payload["StandardErrorPath"]).parent.mkdir(parents=True, exist_ok=True)
    with plist_path.open("wb") as handle:
        plistlib.dump(dict(payload), handle, sort_keys=False)


def run_command(
    argv: Sequence[str],
    *,
    check: bool = True,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        env=dict(env) if env is not None else None,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        details = stderr or stdout or f"exit code {result.returncode}"
        raise LocalSyncSetupError(f"Command {' '.join(argv)} failed: {details}")
    return result


def reload_launch_agent(
    plist_path: Path,
    *,
    uid: int | None = None,
) -> None:
    resolved_uid = os.getuid() if uid is None else uid
    domain_target = f"gui/{resolved_uid}"
    run_command(["launchctl", "bootout", domain_target, str(plist_path)], check=False)
    run_command(["launchctl", "bootstrap", domain_target, str(plist_path)], check=True)


def install_local_sync_launch_agent(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    example_config_path: Path = DEFAULT_EXAMPLE_CONFIG_PATH,
    launch_agent_path: Path = DEFAULT_LAUNCH_AGENT_PATH,
    interval_seconds: int = DEFAULT_START_INTERVAL_SECONDS,
    repo_root: Path = REPO_ROOT,
    stdout_log_path: Path = DEFAULT_STDOUT_LOG_PATH,
    stderr_log_path: Path = DEFAULT_STDERR_LOG_PATH,
    prompt_user: bool = False,
    input_fn: Callable[[str], str] = input,
    output = sys.stdout,
) -> dict[str, object]:
    created_config = ensure_config_file(config_path, example_config_path)
    if prompt_user:
        answers = prompt_local_sync_answers(config_path, input_fn=input_fn, output=output)
        update_local_sync_config(
            config_path,
            project=answers.project,
            move_to_project=answers.move_to_project,
            mode="dry-run",
        )
    else:
        set_local_sync_mode(config_path, "dry-run")
    payload = build_launch_agent_plist(
        config_path=config_path,
        mode="dry-run",
        repo_root=repo_root,
        interval_seconds=interval_seconds,
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
    )
    write_launch_agent_plist(launch_agent_path, payload)
    reload_launch_agent(launch_agent_path)
    return {
        "config_created": created_config,
        "config_path": config_path,
        "launch_agent_path": launch_agent_path,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    prompt_user = not args.no_prompt and sys.stdin.isatty() and sys.stdout.isatty()
    try:
        result = install_local_sync_launch_agent(
            config_path=args.config,
            example_config_path=args.example_config,
            launch_agent_path=args.launch_agent_path,
            interval_seconds=args.interval_seconds,
            prompt_user=prompt_user,
        )
    except LocalSyncSetupError as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Setup cancelled.", file=sys.stderr)
        return 1

    config_status = "Created" if result["config_created"] else "Updated"
    print(f"{config_status} config at {result['config_path']}.")
    print(f"Installed LaunchAgent at {result['launch_agent_path']} in dry-run mode.")
    print("Run a manual dry-run whenever you want to inspect behavior:")
    print("  python -m canvas_things.local_sync_main --config config/config.yml --dry-run")
    print("Automatic writes remain disabled until you run:")
    print("  python scripts/enable_local_sync_apply.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
