from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Iterable, List

import pytest

from canvas_things import canvas_client, config, main, notifier, state


class DummyStore(state.StateStore):
    def __init__(
        self,
        initial: dict[str, str] | None = None,
        pending: List[canvas_client.Assignment] | None = None,
    ) -> None:
        self._initial = dict(initial or {})
        self._data = dict(self._initial)
        self._initial_pending = list(pending or [])
        self._pending: List[canvas_client.Assignment] = list(self._initial_pending)
        self.saved = False
        self.mark_calls: List[tuple[str, str]] = []
        self.removed_pending: List[str] = []
        self.email_count = 0

    def load(self) -> None:  # type: ignore[override]
        self._data = dict(self._initial)
        self._pending = list(self._initial_pending)

    def save(self) -> None:  # type: ignore[override]
        self.saved = True

    def should_notify(self, key: str, updated_at: str) -> bool:  # type: ignore[override]
        previous = self._data.get(key)
        return previous is None or updated_at > previous

    def mark_notified(self, key: str, updated_at: str) -> None:  # type: ignore[override]
        self._data[key] = updated_at
        self.mark_calls.append((key, updated_at))

    def is_known_assignment(self, course_id: int, assignment_id: int) -> bool:  # type: ignore[override]
        return any(key.startswith(f"{course_id}:{assignment_id}:") for key in self._data)

    def get_pending(self) -> List[canvas_client.Assignment]:  # type: ignore[override]
        return list(self._pending)

    def add_pending(self, assignment: canvas_client.Assignment) -> None:  # type: ignore[override]
        self._pending.append(assignment)

    def remove_pending(self, assignment: canvas_client.Assignment) -> None:  # type: ignore[override]
        fingerprint = assignment.fingerprint()
        self.removed_pending.append(fingerprint)
        self._pending = [item for item in self._pending if item.fingerprint() != fingerprint]

    def clear_pending(self) -> None:  # type: ignore[override]
        self._pending = []

    def should_send_email(self, timezone: str = "UTC") -> bool:  # type: ignore[override]
        return True

    def increment_email_count(self) -> int:  # type: ignore[override]
        self.email_count += 1
        return self.email_count

    def get_email_count(self) -> int:  # type: ignore[override]
        return self.email_count


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

    def notify(self, assignments: Iterable[canvas_client.Assignment], **kwargs):  # type: ignore[override]
        assignment_list = list(assignments)
        if self.dry_run:
            self.skipped.extend(a.fingerprint() for a in assignment_list)
            return notifier.NotificationResult(sent=[], skipped=list(self.skipped), failed=[])
        self.sent.extend(a.fingerprint() for a in assignment_list)
        return notifier.NotificationResult(sent=list(self.sent), skipped=list(self.skipped), failed=[])


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
    run_cfg = config.RunConfig(
        timezone="UTC",
        dry_run=False,
        state_file=tmp_path / "state.json",
        skip_undated_assignments=False,
    )
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


def test_poll_skips_undated_assignments_when_configured(
    monkeypatch,
    settings: config.Settings,
    assignments,
):
    skip_settings = replace(settings, run=replace(settings.run, skip_undated_assignments=True))
    store = DummyStore()
    client = StubClient(assignments)
    mailer = StubNotifier()

    monkeypatch.setattr(config, "load_config", lambda path=None: skip_settings)
    monkeypatch.setattr(main.state, "StateStore", lambda path: store)
    monkeypatch.setattr(main.canvas_client, "CanvasClient", lambda settings: client)
    monkeypatch.setattr(main.notifier, "Notifier", lambda settings: mailer)

    exit_code = main.poll([])

    assert exit_code == 0
    assert mailer.sent == []
    assert store.mark_calls == []


def test_poll_still_sends_dated_assignments_when_skipping_undated(
    monkeypatch,
    settings: config.Settings,
    assignments,
):
    dated_assignment = replace(assignments[0], due_at="2999-05-01T00:00:00Z")
    skip_settings = replace(settings, run=replace(settings.run, skip_undated_assignments=True))
    store = DummyStore()
    client = StubClient([dated_assignment, assignments[1]])
    mailer = StubNotifier()

    monkeypatch.setattr(config, "load_config", lambda path=None: skip_settings)
    monkeypatch.setattr(main.state, "StateStore", lambda path: store)
    monkeypatch.setattr(main.canvas_client, "CanvasClient", lambda settings: client)
    monkeypatch.setattr(main.notifier, "Notifier", lambda settings: mailer)

    exit_code = main.poll([])

    assert exit_code == 0
    assert mailer.sent == [dated_assignment.fingerprint()]
    assert store.mark_calls == [(dated_assignment.fingerprint(), dated_assignment.updated_at)]


def test_poll_removes_undated_pending_assignments_when_configured(
    monkeypatch,
    settings: config.Settings,
    assignments,
):
    skip_settings = replace(settings, run=replace(settings.run, skip_undated_assignments=True))
    store = DummyStore(pending=[assignments[0]])
    client = StubClient([])
    mailer = StubNotifier()

    monkeypatch.setattr(config, "load_config", lambda path=None: skip_settings)
    monkeypatch.setattr(main.state, "StateStore", lambda path: store)
    monkeypatch.setattr(main.canvas_client, "CanvasClient", lambda settings: client)
    monkeypatch.setattr(main.notifier, "Notifier", lambda settings: mailer)

    exit_code = main.poll([])

    assert exit_code == 0
    assert mailer.sent == []
    assert store.removed_pending == [assignments[0].fingerprint()]
    assert store._pending == []


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
