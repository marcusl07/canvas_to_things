from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import List

import pytest

from canvas_things import config
from canvas_things.canvas_client import Assignment
from canvas_things.notifier import NotificationResult, Notifier


class StubTransport:
    def __init__(self) -> None:
        self.messages: List[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


def make_settings(include_description: bool = True, dry_run: bool = False) -> config.Settings:
    return config.Settings(
        canvas=config.CanvasConfig(base_url="https://canvas.example.com", courses=[]),
        email=config.EmailConfig(
            from_name="Bot",
            subject_template="{course_alias}: {title}",
            include_description=include_description,
            max_description_chars=20,
        ),
        run=config.RunConfig(timezone="UTC", dry_run=dry_run, state_file=Path("state.json")),
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_pass="pass",
        things_email="user@things.email",
        canvas_token="token",
    )


def make_assignment(description: str | None = "Long description text") -> Assignment:
    return Assignment(
        course_id=1,
        course_alias="CS",
        assignment_id=5,
        title="Project",
        html_url="https://canvas.example.com/a/5",
        updated_at="2025-01-01T00:00:00Z",
        due_at="2025-01-02T00:00:00Z",
        lock_at=None,
        unlock_at=None,
        description=description,
        points_possible=50.0,
        submission_types=["online_upload"],
        published=True,
    )


def test_notifier_formats_email_and_sends_via_transport() -> None:
    settings = make_settings()
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    result = notifier.notify([make_assignment()])

    assert isinstance(result, NotificationResult)
    assert result.sent == ["1:5:2025-01-01T00:00:00Z"]
    assert result.skipped == []

    assert len(transport.messages) == 1
    message = transport.messages[0]
    assert message["To"] == settings.things_email
    assert "CS: Project" == str(message["Subject"])
    assert "Due:" in message.get_content()
    assert "Submission:" in message.get_content()


def test_notifier_trims_description_and_respects_toggle() -> None:
    settings = make_settings(include_description=True)
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    long_description = "Line1\nLine2\nLine3"
    assignment = make_assignment(description=long_description)
    notifier.notify([assignment])

    body = transport.messages[0].get_content()
    assert "Line1" in body and "Line3" in body

    settings_no_desc = make_settings(include_description=False)
    notifier_no_desc = Notifier(settings=settings_no_desc, transport=transport)
    transport.messages.clear()
    assignment_no_desc = make_assignment(description="Should hide")
    notifier_no_desc.notify([assignment_no_desc])
    body_no_desc = transport.messages[0].get_content()
    assert "Should hide" not in body_no_desc


def test_notifier_dry_run_skips_sending() -> None:
    settings = make_settings(dry_run=True)
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    result = notifier.notify([make_assignment()])

    assert result.sent == []
    assert result.skipped == ["1:5:2025-01-01T00:00:00Z"]
    assert transport.messages == []
