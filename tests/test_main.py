from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable, List

import pytest

from canvas_things import canvas_client, config, main, notifier, state


class DummyStore(state.StateStore):
    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._initial = dict(initial or {})
        self._data = dict(self._initial)
        self.saved = False
        self.mark_calls: List[tuple[str, str]] = []

    def load(self) -> None:  # type: ignore[override]
        self._data = dict(self._initial)

    def save(self) -> None:  # type: ignore[override]
        self.saved = True

    def should_notify(self, key: str, updated_at: str) -> bool:  # type: ignore[override]
        previous = self._data.get(key)
        return previous is None or updated_at > previous

    def mark_notified(self, key: str, updated_at: str) -> None:  # type: ignore[override]
        self._data[key] = updated_at
        self.mark_calls.append((key, updated_at))


class StubClient(canvas_client.CanvasClient):
    def __init__(self, assignments: List[canvas_client.Assignment]) -> None:
        self._assignments = assignments

    def fetch_assignments(self, course: config.CourseConfig, per_page: int = 50):  # type: ignore[override]
        return self._assignments


class StubNotifier(notifier.Notifier):
    def __init__(self, dry_run: bool = False) -> None:
        self.sent: List[str] = []
        self.skipped: List[str] = []
        self.dry_run = dry_run

    def notify(self, assignments: Iterable[canvas_client.Assignment]):  # type: ignore[override]
        if self.dry_run:
            self.skipped.extend(a.fingerprint() for a in assignments)
            return notifier.NotificationResult(sent=[], skipped=list(self.skipped))
        self.sent.extend(a.fingerprint() for a in assignments)
        return notifier.NotificationResult(sent=list(self.sent), skipped=list(self.skipped))


@pytest.fixture
def settings(tmp_path: Path) -> config.Settings:
    courses = [config.CourseConfig(course_id=1, alias="MATH")]
    canvas_cfg = config.CanvasConfig(base_url="https://canvas.example.com", courses=courses)
    email_cfg = config.EmailConfig(
        from_name="Bot",
        subject_template="{course_alias} – {title}",
        include_description=True,
        max_description_chars=500,
    )
    run_cfg = config.RunConfig(timezone="UTC", dry_run=False, state_file=tmp_path / "state.json")
    return config.Settings(
        canvas=canvas_cfg,
        email=email_cfg,
        run=run_cfg,
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_pass="pass",
        things_email="user@things.email",
        canvas_token="token",
    )


@pytest.fixture
def assignments() -> List[canvas_client.Assignment]:
    return [
        canvas_client.Assignment(
            course_id=1,
            course_alias="MATH",
            assignment_id=10,
            title="Homework",
            html_url="https://canvas.example.com/assignments/10",
            updated_at="2025-01-01T00:00:00Z",
            due_at=None,
            lock_at=None,
            unlock_at=None,
            description=None,
            points_possible=None,
            submission_types=[],
            published=True,
        ),
        canvas_client.Assignment(
            course_id=1,
            course_alias="MATH",
            assignment_id=11,
            title="Homework 2",
            html_url="https://canvas.example.com/assignments/11",
            updated_at="2025-01-02T00:00:00Z",
            due_at=None,
            lock_at=None,
            unlock_at=None,
            description=None,
            points_possible=None,
            submission_types=[],
            published=True,
        ),
    ]


def test_poll_filters_assignments_and_updates_state(monkeypatch, settings: config.Settings, assignments):
    store = DummyStore()
    client = StubClient(assignments)
    mailer = StubNotifier()

    monkeypatch.setattr(config, "load_config", lambda path=None: settings)
    monkeypatch.setattr(main.state, "StateStore", lambda path: store)
    monkeypatch.setattr(main.canvas_client, "CanvasClient", lambda settings: client)
    monkeypatch.setattr(main.notifier, "Notifier", lambda settings: mailer)

    exit_code = main.poll([])

    assert exit_code == 0
    assert store.saved is True
    assert mailer.sent == [a.fingerprint() for a in assignments]
    assert store.mark_calls == [(a.fingerprint(), a.updated_at) for a in assignments]


def test_poll_respects_dry_run(monkeypatch, settings: config.Settings, assignments):
    dry_settings = replace(settings, run=replace(settings.run, dry_run=True))
    store = DummyStore(initial={assignments[0].fingerprint(): assignments[0].updated_at})
    client = StubClient(assignments)
    mailer = StubNotifier(dry_run=True)

    monkeypatch.setattr(config, "load_config", lambda path=None: dry_settings)
    monkeypatch.setattr(main.state, "StateStore", lambda path: store)
    monkeypatch.setattr(main.canvas_client, "CanvasClient", lambda settings: client)
    monkeypatch.setattr(main.notifier, "Notifier", lambda settings: mailer)

    exit_code = main.poll([])

    assert exit_code == 0
    assert mailer.sent == []
    assert mailer.skipped == [assignments[1].fingerprint()]
    assert store.mark_calls == []
