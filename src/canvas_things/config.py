"""Configuration loading helpers for the Canvas → Things Mail Bridge."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import os
import yaml

CONFIG_PATH = Path("config/config.yml")


class ConfigError(RuntimeError):
    """Raised when the configuration file or environment secrets are invalid."""


@dataclass
class CourseConfig:
    course_id: int
    alias: str
    include_description: bool = True


@dataclass
class CanvasConfig:
    base_url: str
    courses: List[CourseConfig]


@dataclass
class EmailConfig:
    from_name: str
    subject_template: str
    include_description: bool
    max_description_chars: int


@dataclass
class RunConfig:
    timezone: str
    dry_run: bool
    state_file: Path


@dataclass
class Settings:
    canvas: CanvasConfig
    email: EmailConfig
    run: RunConfig
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    things_email: str
    canvas_token: str


def load_config(path: Optional[Path] = None) -> Settings:
    """Load configuration from YAML and environment variables."""

    config_path = path or CONFIG_PATH
    if not config_path.exists():
        raise ConfigError(
            f"Configuration file {config_path} not found. Copy config.example.yml"
            " to config.yml and fill in your course information."
        )

    with config_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    canvas_data = data.get("canvas") or {}
    email_data = data.get("email") or {}
    run_data = data.get("run") or {}

    canvas_config = _parse_canvas(canvas_data)
    email_config = _parse_email(email_data)
    run_config = _parse_run(run_data)

    env = dict(os.environ)
    settings = Settings(
        canvas=canvas_config,
        email=email_config,
        run=run_config,
        smtp_host=_require_env(env, "SMTP_HOST"),
        smtp_port=int(_require_env(env, "SMTP_PORT")),
        smtp_user=_require_env(env, "SMTP_USER"),
        smtp_pass=_require_env(env, "SMTP_PASS"),
        things_email=_require_env(env, "THINGS_EMAIL"),
        canvas_token=_require_env(env, "CANVAS_TOKEN"),
    )
    return settings


def _parse_canvas(canvas_data: Dict[str, Any]) -> CanvasConfig:
    base_url = _resolve_placeholder(canvas_data.get("base_url"), "CANVAS_BASE_URL")
    if not base_url:
        raise ConfigError(
            "Canvas base_url is missing. Set it in config.yml or via CANVAS_BASE_URL."
        )

    raw_courses = canvas_data.get("courses") or []
    if not isinstance(raw_courses, list) or not raw_courses:
        raise ConfigError("At least one course must be defined under canvas.courses.")

    courses: List[CourseConfig] = []
    for entry in raw_courses:
        if not isinstance(entry, dict):
            raise ConfigError("Each course entry must be a mapping.")
        try:
            course_id = int(entry["id"])
        except (KeyError, ValueError) as exc:
            raise ConfigError("Course entry missing integer 'id'.") from exc

        alias = entry.get("alias") or str(course_id)
        include_desc = bool(entry.get("include_description", True))
        courses.append(CourseConfig(course_id=course_id, alias=alias, include_description=include_desc))

    return CanvasConfig(base_url=base_url.rstrip("/"), courses=courses)


def _parse_email(email_data: Dict[str, Any]) -> EmailConfig:
    subject_template = email_data.get("subject_template") or "{course_alias} – {title}"
    include_description = bool(email_data.get("include_description", True))
    max_chars = int(email_data.get("max_description_chars", 500))
    from_name = email_data.get("from_name") or "Canvas Bot"
    return EmailConfig(
        from_name=from_name,
        subject_template=subject_template,
        include_description=include_description,
        max_description_chars=max_chars,
    )


def _parse_run(run_data: Dict[str, Any]) -> RunConfig:
    timezone = run_data.get("timezone") or "UTC"
    dry_run = bool(run_data.get("dry_run", False))
    state_path_value = run_data.get("state_file") or "data/state.json"
    return RunConfig(timezone=timezone, dry_run=dry_run, state_file=Path(state_path_value))


def _resolve_placeholder(value: Optional[str], env_name: str) -> Optional[str]:
    if not value:
        return os.environ.get(env_name)
    if value.startswith("${") and value.endswith("}"):
        env_key = value[2:-1]
        return os.environ.get(env_key)
    return value


def _require_env(env: Dict[str, str], key: str) -> str:
    try:
        value = env[key]
    except KeyError as exc:
        raise ConfigError(
            f"Missing required environment variable {key}. Set it locally or as a GitHub secret."
        ) from exc
    if not value:
        raise ConfigError(f"Environment variable {key} is empty.")
    return value
