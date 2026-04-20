from __future__ import annotations

import io
import plistlib
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import enable_local_sync_apply, setup_local_sync


def read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def read_plist(path: Path) -> dict:
    with path.open("rb") as handle:
        return plistlib.load(handle)


@pytest.fixture
def recorded_commands(monkeypatch) -> list[tuple[tuple[str, ...], dict | None]]:
    calls: list[tuple[tuple[str, ...], dict | None]] = []

    def fake_run_command(argv, *, check=True, env=None):
        calls.append((tuple(argv), dict(env) if env is not None else None))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(setup_local_sync, "run_command", fake_run_command)
    monkeypatch.setattr(enable_local_sync_apply, "run_command", fake_run_command)
    return calls


def test_install_local_sync_launch_agent_seeds_missing_config_and_uses_dry_run(
    tmp_path: Path,
    recorded_commands,
):
    example_config = tmp_path / "config.example.yml"
    example_config.write_text(
        """
version: 1
canvas:
  base_url: "https://canvas.example.com"
local_sync:
  mode: "apply"
  candidate_cap: 50
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yml"
    launch_agent_path = tmp_path / "LaunchAgents" / "com.canvas_to_things.local_sync.plist"
    stdout_log_path = tmp_path / "logs" / "local_sync.out.log"
    stderr_log_path = tmp_path / "logs" / "local_sync.err.log"

    result = setup_local_sync.install_local_sync_launch_agent(
        config_path=config_path,
        example_config_path=example_config,
        launch_agent_path=launch_agent_path,
        interval_seconds=900,
        repo_root=tmp_path,
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
    )

    config = read_yaml(config_path)
    plist = read_plist(launch_agent_path)

    assert result["config_created"] is True
    assert config["local_sync"]["mode"] == "dry-run"
    assert plist["ProgramArguments"][-1] == "--dry-run"
    assert plist["StartInterval"] == 900
    assert plist["EnvironmentVariables"]["PYTHONPATH"].startswith(str((tmp_path / "src").resolve()))
    assert recorded_commands == [
        (("launchctl", "bootout", f"gui/{setup_local_sync.os.getuid()}", str(launch_agent_path)), None),
        (("launchctl", "bootstrap", f"gui/{setup_local_sync.os.getuid()}", str(launch_agent_path)), None),
    ]


def test_install_local_sync_launch_agent_forces_existing_config_back_to_dry_run(
    tmp_path: Path,
    recorded_commands,
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
version: 1
local_sync:
  project: "School"
  mode: "apply"
""",
        encoding="utf-8",
    )
    example_config = tmp_path / "config.example.yml"
    example_config.write_text("version: 1\n", encoding="utf-8")
    launch_agent_path = tmp_path / "agent.plist"
    stdout_log_path = tmp_path / "logs" / "local_sync.out.log"
    stderr_log_path = tmp_path / "logs" / "local_sync.err.log"

    setup_local_sync.install_local_sync_launch_agent(
        config_path=config_path,
        example_config_path=example_config,
        launch_agent_path=launch_agent_path,
        repo_root=tmp_path,
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
    )

    config = read_yaml(config_path)
    plist = read_plist(launch_agent_path)

    assert config["local_sync"]["project"] == "School"
    assert config["local_sync"]["mode"] == "dry-run"
    assert "--apply" not in plist["ProgramArguments"]
    assert plist["ProgramArguments"][-1] == "--dry-run"


def test_install_local_sync_launch_agent_prompt_uses_inbox_defaults(
    tmp_path: Path,
    recorded_commands,
):
    example_config = tmp_path / "config.example.yml"
    example_config.write_text(
        """
version: 1
local_sync:
  candidate_cap: 200
  timeout_seconds: 120
""",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.yml"
    launch_agent_path = tmp_path / "agent.plist"
    prompt_output = io.StringIO()
    responses = iter(["", ""])

    setup_local_sync.install_local_sync_launch_agent(
        config_path=config_path,
        example_config_path=example_config,
        launch_agent_path=launch_agent_path,
        repo_root=tmp_path,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
        prompt_user=True,
        input_fn=lambda prompt: next(responses),
        output=prompt_output,
    )

    config = read_yaml(config_path)

    assert config["local_sync"]["project"] is None
    assert config["local_sync"]["move_to_project"] is None
    assert config["local_sync"]["mode"] == "dry-run"
    assert "Local sync setup" in prompt_output.getvalue()


def test_install_local_sync_launch_agent_prompt_updates_project_scope_and_move_target(
    tmp_path: Path,
    recorded_commands,
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
version: 1
local_sync:
  project: "School"
  move_to_project: "Deadlines"
  mode: "apply"
""",
        encoding="utf-8",
    )
    example_config = tmp_path / "config.example.yml"
    example_config.write_text("version: 1\n", encoding="utf-8")
    launch_agent_path = tmp_path / "agent.plist"
    responses = iter(["", "", ""])

    setup_local_sync.install_local_sync_launch_agent(
        config_path=config_path,
        example_config_path=example_config,
        launch_agent_path=launch_agent_path,
        repo_root=tmp_path,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
        prompt_user=True,
        input_fn=lambda prompt: next(responses),
        output=io.StringIO(),
    )

    config = read_yaml(config_path)

    assert config["local_sync"]["project"] == "School"
    assert config["local_sync"]["move_to_project"] == "Deadlines"
    assert config["local_sync"]["mode"] == "dry-run"


def test_enable_automatic_writes_switches_launch_agent_and_runs_one_apply_sync(
    tmp_path: Path,
    recorded_commands,
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        """
version: 1
local_sync:
  mode: "dry-run"
""",
        encoding="utf-8",
    )
    launch_agent_path = tmp_path / "agent.plist"
    dry_run_payload = setup_local_sync.build_launch_agent_plist(
        config_path=config_path,
        mode="dry-run",
        repo_root=tmp_path,
        interval_seconds=86400,
        stdout_log_path=tmp_path / "logs" / "local_sync.out.log",
        stderr_log_path=tmp_path / "logs" / "local_sync.err.log",
    )
    setup_local_sync.write_launch_agent_plist(launch_agent_path, dry_run_payload)

    enable_local_sync_apply.enable_automatic_writes(
        config_path=config_path,
        launch_agent_path=launch_agent_path,
    )

    config = read_yaml(config_path)
    plist = read_plist(launch_agent_path)

    assert config["local_sync"]["mode"] == "apply"
    assert plist["StartInterval"] == 86400
    assert plist["ProgramArguments"][-1] == "--apply"
    assert recorded_commands[0][0][-1] == "--apply"
    assert recorded_commands[0][1] is not None
    assert recorded_commands[0][1]["PYTHONPATH"].startswith(str((tmp_path / "src").resolve()))
    assert recorded_commands[1][0][:2] == ("launchctl", "bootout")
    assert recorded_commands[2][0][:2] == ("launchctl", "bootstrap")


def test_enable_automatic_writes_requires_existing_launch_agent(tmp_path: Path):
    config_path = tmp_path / "config.yml"
    config_path.write_text("version: 1\nlocal_sync:\n  mode: dry-run\n", encoding="utf-8")

    with pytest.raises(setup_local_sync.LocalSyncSetupError, match="Run scripts/setup_local_sync.py first"):
        enable_local_sync_apply.enable_automatic_writes(
            config_path=config_path,
            launch_agent_path=tmp_path / "missing.plist",
        )
