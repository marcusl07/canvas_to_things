"""Configuration contract for the local Things deadline sync companion."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path("config/config.yml")
DEFAULT_MODE = "dry-run"
DEFAULT_CANDIDATE_CAP = 200
DEFAULT_TIMEOUT_SECONDS = 120.0
SUPPORTED_VERSION = 1
VALID_MODES = frozenset({"dry-run", "apply"})


class LocalSyncConfigError(RuntimeError):
    """Raised when the local-sync configuration is invalid."""


class LocalSyncExitCode(IntEnum):
    """Stable process exit codes for the local-sync command."""

    SUCCESS = 0
    CONFIG_ERROR = 2
    PRECONDITION_ERROR = 3
    PARTIAL_FAILURE = 4
    TIMEOUT = 5
    UNEXPECTED_ERROR = 6


EXIT_CODE_MEANINGS = {
    LocalSyncExitCode.SUCCESS: "Run completed successfully.",
    LocalSyncExitCode.CONFIG_ERROR: "CLI usage or configuration validation failed.",
    LocalSyncExitCode.PRECONDITION_ERROR: "A required precondition failed before or during planning.",
    LocalSyncExitCode.PARTIAL_FAILURE: "The run completed but one or more task mutations failed.",
    LocalSyncExitCode.TIMEOUT: "The run stopped because the wall-clock timeout was exceeded.",
    LocalSyncExitCode.UNEXPECTED_ERROR: "An unexpected internal error interrupted the run.",
}


@dataclass(frozen=True)
class LocalSyncOverrides:
    project: str | None = None
    move_to_project: str | None = None
    mode: str | None = None
    candidate_cap: int | None = None
    timeout_seconds: float | None = None


@dataclass(frozen=True)
class LocalSyncSettings:
    version: int
    project: str | None
    move_to_project: str | None
    mode: str
    candidate_cap: int
    timeout_seconds: float
    config_path: Path

    @property
    def dry_run(self) -> bool:
        return self.mode == "dry-run"

    @property
    def apply_changes(self) -> bool:
        return self.mode == "apply"


def load_local_sync_config(
    path: Path | None = None,
    *,
    overrides: LocalSyncOverrides | None = None,
) -> LocalSyncSettings:
    """Load local-sync settings from YAML and apply CLI overrides."""

    config_path = path or CONFIG_PATH
    if not config_path.exists():
        raise LocalSyncConfigError(
            f"Configuration file {config_path} not found. Copy config.example.yml to config.yml first."
        )

    with config_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    if not isinstance(data, dict):
        raise LocalSyncConfigError("Configuration file must contain a top-level mapping.")

    version = _parse_version(data.get("version"))
    local_sync_data = data.get("local_sync") or {}
    if not isinstance(local_sync_data, dict):
        raise LocalSyncConfigError("local_sync must be a mapping when present.")

    settings_data = {
        "project": _clean_optional_text(local_sync_data.get("project"), field_name="local_sync.project"),
        "move_to_project": _clean_optional_text(
            local_sync_data.get("move_to_project"),
            field_name="local_sync.move_to_project",
        ),
        "mode": _parse_mode(local_sync_data.get("mode", DEFAULT_MODE)),
        "candidate_cap": _parse_candidate_cap(
            local_sync_data.get("candidate_cap", DEFAULT_CANDIDATE_CAP)
        ),
        "timeout_seconds": _parse_timeout_seconds(
            local_sync_data.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
        ),
    }

    if overrides is not None:
        if overrides.project is not None:
            settings_data["project"] = _clean_optional_text(overrides.project, field_name="--project")
        if overrides.move_to_project is not None:
            settings_data["move_to_project"] = _clean_optional_text(
                overrides.move_to_project,
                field_name="--move-to-project",
            )
        if overrides.mode is not None:
            settings_data["mode"] = _parse_mode(overrides.mode)
        if overrides.candidate_cap is not None:
            settings_data["candidate_cap"] = _parse_candidate_cap(overrides.candidate_cap)
        if overrides.timeout_seconds is not None:
            settings_data["timeout_seconds"] = _parse_timeout_seconds(overrides.timeout_seconds)

    return LocalSyncSettings(
        version=version,
        project=settings_data["project"],
        move_to_project=settings_data["move_to_project"],
        mode=settings_data["mode"],
        candidate_cap=settings_data["candidate_cap"],
        timeout_seconds=settings_data["timeout_seconds"],
        config_path=config_path,
    )


def _parse_version(value: Any) -> int:
    try:
        version = int(value)
    except (TypeError, ValueError) as exc:
        raise LocalSyncConfigError("Local sync requires a top-level 'version: 1'.") from exc
    if version != SUPPORTED_VERSION:
        raise LocalSyncConfigError(
            f"Unsupported local sync config version {version}. Expected version: {SUPPORTED_VERSION}."
        )
    return version


def _parse_mode(value: Any) -> str:
    if not isinstance(value, str):
        raise LocalSyncConfigError("local_sync.mode must be 'dry-run' or 'apply'.")
    mode = value.strip().lower()
    if mode not in VALID_MODES:
        raise LocalSyncConfigError("local_sync.mode must be 'dry-run' or 'apply'.")
    return mode


def _parse_candidate_cap(value: Any) -> int:
    try:
        cap = int(value)
    except (TypeError, ValueError) as exc:
        raise LocalSyncConfigError("candidate_cap must be an integer greater than 0.") from exc
    if cap <= 0:
        raise LocalSyncConfigError("candidate_cap must be an integer greater than 0.")
    return cap


def _parse_timeout_seconds(value: Any) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise LocalSyncConfigError("timeout_seconds must be a number greater than 0.") from exc
    if timeout <= 0:
        raise LocalSyncConfigError("timeout_seconds must be a number greater than 0.")
    return timeout


def _clean_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LocalSyncConfigError(f"{field_name} must be a string when provided.")
    cleaned = value.strip()
    if not cleaned:
        raise LocalSyncConfigError(f"{field_name} cannot be blank.")
    return cleaned


__all__ = [
    "CONFIG_PATH",
    "DEFAULT_CANDIDATE_CAP",
    "DEFAULT_MODE",
    "DEFAULT_TIMEOUT_SECONDS",
    "EXIT_CODE_MEANINGS",
    "LocalSyncConfigError",
    "LocalSyncExitCode",
    "LocalSyncOverrides",
    "LocalSyncSettings",
    "SUPPORTED_VERSION",
    "load_local_sync_config",
]
