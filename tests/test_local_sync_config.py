from pathlib import Path

import pytest

from canvas_things.local_sync_config import (
    DEFAULT_CANDIDATE_CAP,
    DEFAULT_MODE,
    DEFAULT_TIMEOUT_SECONDS,
    LocalSyncConfigError,
    LocalSyncExitCode,
    LocalSyncOverrides,
    load_local_sync_config,
)


def write_config(tmp_path: Path, text: str) -> Path:
    file_path = tmp_path / "config.yml"
    file_path.write_text(text, encoding="utf-8")
    return file_path


def test_load_local_sync_config_uses_defaults_when_block_missing(tmp_path):
    path = write_config(
        tmp_path,
        """
version: 1
""",
    )

    settings = load_local_sync_config(path)

    assert settings.version == 1
    assert settings.project is None
    assert settings.move_to_project is None
    assert settings.mode == DEFAULT_MODE
    assert settings.candidate_cap == DEFAULT_CANDIDATE_CAP
    assert settings.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert settings.dry_run is True
    assert settings.apply_changes is False
    assert settings.config_path == path


def test_load_local_sync_config_applies_cli_precedence_over_file_and_defaults(tmp_path):
    path = write_config(
        tmp_path,
        """
version: 1
local_sync:
  project: "School"
  move_to_project: "Deadlines"
  mode: "dry-run"
  candidate_cap: 90
  timeout_seconds: 45
""",
    )

    settings = load_local_sync_config(
        path,
        overrides=LocalSyncOverrides(
            project="Inbox Overrides",
            mode="apply",
            candidate_cap=25,
        ),
    )

    assert settings.project == "Inbox Overrides"
    assert settings.move_to_project == "Deadlines"
    assert settings.mode == "apply"
    assert settings.candidate_cap == 25
    assert settings.timeout_seconds == 45.0
    assert settings.apply_changes is True
    assert settings.dry_run is False


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("local_sync: {}\n", "version: 1"),
        ("version: 2\n", "Unsupported local sync config version 2"),
        ("version: 1\nlocal_sync:\n  mode: go\n", "local_sync.mode"),
        ("version: 1\nlocal_sync:\n  candidate_cap: 0\n", "candidate_cap"),
        ("version: 1\nlocal_sync:\n  timeout_seconds: 0\n", "timeout_seconds"),
        ("version: 1\nlocal_sync:\n  project: \"   \"\n", "local_sync.project"),
    ],
)
def test_load_local_sync_config_rejects_invalid_values(tmp_path, text, match):
    path = write_config(tmp_path, text)

    with pytest.raises(LocalSyncConfigError, match=match):
        load_local_sync_config(path)


def test_load_local_sync_config_rejects_missing_file(tmp_path):
    path = tmp_path / "missing.yml"

    with pytest.raises(LocalSyncConfigError, match="not found"):
        load_local_sync_config(path)


def test_exit_code_values_are_stable():
    assert int(LocalSyncExitCode.SUCCESS) == 0
    assert int(LocalSyncExitCode.CONFIG_ERROR) == 2
    assert int(LocalSyncExitCode.PRECONDITION_ERROR) == 3
    assert int(LocalSyncExitCode.PARTIAL_FAILURE) == 4
    assert int(LocalSyncExitCode.TIMEOUT) == 5
    assert int(LocalSyncExitCode.UNEXPECTED_ERROR) == 6
