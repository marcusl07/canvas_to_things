import os
from pathlib import Path

import pytest

from canvas_things import config as cfg
from canvas_things import state as st


def write_config(tmp_path: Path, text: str) -> Path:
    file_path = tmp_path / "config.yml"
    file_path.write_text(text, encoding="utf-8")
    return file_path


def test_load_config_with_env_placeholders(tmp_path, monkeypatch):
    path = write_config(
        tmp_path,
        """
canvas:
  base_url: ${CANVAS_BASE_URL}
  courses:
    - id: 42
      alias: "MATH"
email:
  from_name: "Bot"
run:
  timezone: "America/New_York"
  state_file: "custom_state.json"
  skip_undated_assignments: true
""",
    )

    env = {
        "CANVAS_BASE_URL": "https://school.instructure.com",
        "CANVAS_TOKEN": "token",
        "THINGS_EMAIL": "user@things.email",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user",
        "SMTP_PASS": "pass",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    settings = cfg.load_config(path)
    assert settings.canvas.base_url == "https://school.instructure.com"
    assert settings.canvas.courses[0].course_id == 42
    assert settings.email.from_name == "Bot"
    assert settings.run.timezone == "America/New_York"
    assert settings.run.state_file == Path("custom_state.json")
    assert settings.run.skip_undated_assignments is True


def test_load_config_defaults_to_importing_undated_assignments(tmp_path, monkeypatch):
    path = write_config(
        tmp_path,
        """
canvas:
  base_url: ${CANVAS_BASE_URL}
  courses:
    - id: 42
email:
  from_name: "Bot"
run:
  timezone: "America/New_York"
""",
    )

    env = {
        "CANVAS_BASE_URL": "https://school.instructure.com",
        "CANVAS_TOKEN": "token",
        "THINGS_EMAIL": "user@things.email",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user",
        "SMTP_PASS": "pass",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    settings = cfg.load_config(path)

    assert settings.run.skip_undated_assignments is False


def test_state_store_round_trip(tmp_path):
    path = tmp_path / "state.json"
    store = st.StateStore(path)
    store.load()
    first_key = "course:1:2026-01-01T00:00:00Z"
    second_key = "course:1:2026-01-01T00:00:01Z"
    assert store.should_notify(first_key, "2026-01-01T00:00:00Z")

    store.mark_notified(first_key, "2026-01-01T00:00:00Z")
    assert not store.should_notify(first_key, "2026-01-01T00:00:00Z")
    assert store.should_notify(second_key, "2026-01-01T00:00:01Z")

    store.save()

    store2 = st.StateStore(path)
    store2.load()
    assert store2.snapshot() == {first_key: "2026-01-01T00:00:00Z"}
