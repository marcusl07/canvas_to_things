from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path
from typing import List

from canvas_things import config
from canvas_things.canvas_client import Assignment
from canvas_things.local_sync_notes import parse_task_note
from canvas_things.managed_notes import format_due_line, format_managed_marker_line
from canvas_things.notifier import NotificationResult, Notifier


class StubTransport:
    def __init__(self) -> None:
        self.messages: List[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


def make_settings(
    include_description: bool = True,
    dry_run: bool = False,
    *,
    max_description_chars: int = 20,
    timezone: str = "UTC",
) -> config.Settings:
    return config.Settings(
        canvas=config.CanvasConfig(base_url="https://canvas.example.com", courses=[]),
        email=config.EmailConfig(
            from_name="Bot",
            subject_template="{course_alias}: {title}",
            include_description=include_description,
            max_description_chars=max_description_chars,
        ),
        run=config.RunConfig(timezone=timezone, dry_run=dry_run, state_file=Path("state.json")),
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
        due_at="2025-01-02T23:59:59Z",
        lock_at=None,
        unlock_at=None,
        description=description,
        points_possible=50.0,
        submission_types=["online_upload"],
        published=True,
    )


def body_lines(message: EmailMessage) -> list[str]:
    return message.get_content().splitlines()


def test_notifier_formats_email_and_sends_via_transport() -> None:
    settings = make_settings()
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    result = notifier.notify([make_assignment()])

    assert isinstance(result, NotificationResult)
    assert result.sent == ["1:5:2025-01-01T00:00:00Z"]
    assert result.skipped == []
    assert result.failed == []

    assert len(transport.messages) == 1
    message = transport.messages[0]
    assert message["To"] == settings.things_email
    assert "CS: Project" == str(message["Subject"])
    lines = body_lines(message)
    assert format_due_line("2025-01-02") in lines
    assert "Due At: 2025-01-02 23:59:59 UTC (2025-01-02 23:59:59 UTC)" in lines
    assert "Submission: online_upload" in lines
    assert lines[-1] == format_managed_marker_line()


def test_notifier_emits_local_sync_writable_note_body_for_standard_assignment() -> None:
    settings = make_settings()
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    notifier.notify([make_assignment()])

    parsed = parse_task_note(transport.messages[0].get_content())

    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.due_text == "2025-01-02"
    assert parsed.diagnostics == ()


def test_notifier_uses_local_calendar_date_for_managed_due_line() -> None:
    settings = make_settings(timezone="America/Los_Angeles")
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    assignment = make_assignment()
    assignment.due_at = "2025-01-02T07:59:59Z"
    notifier.notify([assignment])

    lines = body_lines(transport.messages[0])
    assert format_due_line("2025-01-01") in lines
    assert "Due At: 2025-01-02 07:59:59 UTC (2025-01-01 23:59:59 PST)" in lines

    parsed = parse_task_note(transport.messages[0].get_content())
    assert parsed.due_text == "2025-01-01"


def test_notifier_prefixes_weird_local_due_time_in_subject() -> None:
    settings = make_settings(timezone="America/Los_Angeles")
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)
    assignment = make_assignment()
    assignment.due_at = "2025-01-02T01:00:00Z"

    notifier.notify([assignment])

    assert str(transport.messages[0]["Subject"]) == "[DUE 1700] CS: Project"


def test_notifier_places_weird_due_prefix_after_update_prefix() -> None:
    settings = make_settings(timezone="America/Los_Angeles")
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)
    assignment = make_assignment()
    assignment.due_at = "2025-01-02T01:00:00Z"
    assignment.is_update_notification = True

    notifier.notify([assignment])

    assert str(transport.messages[0]["Subject"]) == "[UPDATE] [DUE 1700] CS: Project"


def test_notifier_places_description_before_trailing_managed_marker() -> None:
    settings = make_settings()
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    notifier.notify([make_assignment(description="First\nSecond")])

    lines = body_lines(transport.messages[0])
    assert lines[-5:] == ["", "First", "Second", "", format_managed_marker_line()]


def test_notifier_marks_update_notifications_in_subject_and_body() -> None:
    settings = make_settings()
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)
    assignment = make_assignment()
    assignment.is_update_notification = True

    notifier.notify([assignment])

    message = transport.messages[0]
    lines = body_lines(message)
    assert str(message["Subject"]) == "[UPDATE] CS: Project"
    assert lines[0] == "** UPDATE **"
    assert lines[-1] == format_managed_marker_line()
    parsed = parse_task_note(message.get_content())
    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.diagnostics == ()


def test_notifier_trims_description_and_respects_toggle() -> None:
    settings = make_settings(include_description=True)
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    long_description = "Line1\nLine2\nLine3"
    assignment = make_assignment(description=long_description)
    notifier.notify([assignment])

    body = transport.messages[0].get_content()
    assert "Line1" in body and "Line3" in body
    assert body_lines(transport.messages[0])[-1] == format_managed_marker_line()

    settings_no_desc = make_settings(include_description=False)
    notifier_no_desc = Notifier(settings=settings_no_desc, transport=transport)
    transport.messages.clear()
    assignment_no_desc = make_assignment(description="Should hide")
    notifier_no_desc.notify([assignment_no_desc])
    body_no_desc = transport.messages[0].get_content()
    assert "Should hide" not in body_no_desc
    assert body_lines(transport.messages[0])[-1] == format_managed_marker_line()


def test_notifier_escapes_reserved_description_prefixes_for_local_sync_compatibility() -> None:
    settings = make_settings(max_description_chars=500)
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    notifier.notify([make_assignment(description="Due: mention this in class\n  Canvas: quote the rubric")])

    lines = body_lines(transport.messages[0])
    assert "- Due: mention this in class" in lines
    assert "- Canvas: quote the rubric" in lines

    parsed = parse_task_note(transport.messages[0].get_content())
    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.diagnostics == ()


def test_notifier_dry_run_skips_sending() -> None:
    settings = make_settings(dry_run=True)
    transport = StubTransport()
    notifier = Notifier(settings=settings, transport=transport)

    result = notifier.notify([make_assignment()])

    assert result.sent == []
    assert result.skipped == ["1:5:2025-01-01T00:00:00Z"]
    assert result.failed == []
    assert transport.messages == []
