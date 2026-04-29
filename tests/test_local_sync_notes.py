from pathlib import Path

from canvas_things import config
from canvas_things.canvas_client import Assignment
from datetime import date

from canvas_things.managed_notes import (
    DUE_LINE_PREFIX,
    MANAGED_NOTE_MARKER,
    format_due_line,
    format_managed_marker_line,
)
from canvas_things.local_sync_notes import parse_task_note
from canvas_things.notifier import Notifier


def diagnostic_codes(parsed_note) -> list[str]:
    return [diagnostic.code for diagnostic in parsed_note.diagnostics]


def emitted_note_body(
    *,
    description: str | None = None,
    is_update_notification: bool = False,
) -> str:
    settings = config.Settings(
        canvas=config.CanvasConfig(base_url="https://canvas.example.com", courses=[]),
        email=config.EmailConfig(
            from_name="Bot",
            subject_template="{course_alias}: {title}",
            include_description=True,
            max_description_chars=500,
        ),
        run=config.RunConfig(timezone="UTC", dry_run=False, state_file=Path("state.json")),
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="bot@example.com",
        smtp_pass="pass",
        things_email="user@things.email",
        canvas_token="token",
    )
    assignment = Assignment(
        course_id=1,
        course_alias="CS",
        assignment_id=5,
        title="Project",
        html_url="https://canvas.example.com/a/5",
        updated_at="2025-01-01T00:00:00Z",
        due_at="2026-04-15T23:59:59Z",
        lock_at=None,
        unlock_at=None,
        description=description,
        points_possible=50.0,
        submission_types=["online_upload"],
        published=True,
        is_update_notification=is_update_notification,
    )
    return Notifier(settings=settings)._build_message(assignment).get_content()


def test_managed_note_primitives_expose_canonical_marker_and_due_format():
    assert MANAGED_NOTE_MARKER == "Canvas:"
    assert DUE_LINE_PREFIX == "Due:"
    assert format_managed_marker_line() == "Canvas:"
    assert format_due_line("2026-04-15") == "Due: 2026-04-15"


def test_parse_task_note_accepts_valid_due_and_trailing_blank_lines():
    parsed = parse_task_note(
        """
Homework
Due: 2026-04-15
Canvas:


"""
    )

    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.due_date == date(2026, 4, 15)
    assert parsed.due_text == "2026-04-15"
    assert parsed.marker_line_number == 4
    assert parsed.due_line_number == 3
    assert parsed.diagnostics == ()


def test_parse_task_note_accepts_freeform_content_above_due_and_marker():
    parsed = parse_task_note(
        """
Homework
Bring calculator
Reference chapter 3
Due: 2026-04-15
Canvas:
"""
    )

    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.due_date == date(2026, 4, 15)
    assert parsed.due_text == "2026-04-15"
    assert parsed.marker_line_number == 6
    assert parsed.due_line_number == 5
    assert parsed.diagnostics == ()


def test_parse_task_note_accepts_notifier_emitted_normal_assignment_with_optional_description():
    parsed = parse_task_note(emitted_note_body(description="Bring calculator\nReference chapter 3"))

    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.due_date == date(2026, 4, 15)
    assert parsed.diagnostics == ()


def test_parse_task_note_accepts_notifier_emitted_update_assignment():
    parsed = parse_task_note(emitted_note_body(is_update_notification=True))

    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.due_date == date(2026, 4, 15)
    assert parsed.diagnostics == ()


def test_parse_task_note_accepts_notifier_emitted_reserved_prefix_description_lines():
    note = emitted_note_body(description="Due: mention this in class\nCanvas: cite the rubric")
    parsed = parse_task_note(note)

    assert "- Due: mention this in class" in note
    assert "- Canvas: cite the rubric" in note
    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.due_date == date(2026, 4, 15)
    assert parsed.diagnostics == ()


def test_parse_task_note_prefers_local_date_from_due_at_line_when_present() -> None:
    parsed = parse_task_note(
        """
Homework
Due: 2026-04-20
Due At: 2026-04-20 06:59:59 UTC (2026-04-19 23:59:59 PDT)
Canvas:
"""
    )

    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.due_text == "2026-04-19"
    assert parsed.due_date == date(2026, 4, 19)
    assert parsed.effective_deadline_date == date(2026, 4, 19)
    assert parsed.weird_due_time is False
    assert parsed.diagnostics == ()


def test_parse_task_note_marks_non_2359_due_at_as_weird_and_shifts_deadline() -> None:
    parsed = parse_task_note(
        """
Homework
Due: 2026-04-20
Due At: 2026-04-21 00:00:00 UTC (2026-04-20 17:00:00 PDT)
Canvas:
"""
    )

    assert parsed.managed is True
    assert parsed.writable is True
    assert parsed.due_text == "2026-04-20"
    assert parsed.due_date == date(2026, 4, 20)
    assert parsed.effective_deadline_date == date(2026, 4, 19)
    assert parsed.weird_due_time is True
    assert parsed.weird_due_display_time == "1700"
    assert parsed.diagnostics == ()


def test_parse_task_note_treats_2359_without_seconds_as_normal() -> None:
    parsed = parse_task_note(
        """
Homework
Due: 2026-04-20
Due At: 2026-04-21 06:59 UTC (2026-04-20 23:59 PDT)
Canvas:
"""
    )

    assert parsed.due_date == date(2026, 4, 20)
    assert parsed.effective_deadline_date == date(2026, 4, 20)
    assert parsed.weird_due_time is False


def test_parse_task_note_does_not_treat_due_at_without_local_time_as_weird() -> None:
    parsed = parse_task_note(
        """
Homework
Due: 2026-04-20
Due At: 2026-04-20 00:00:00 UTC
Canvas:
"""
    )

    assert parsed.due_date == date(2026, 4, 20)
    assert parsed.effective_deadline_date == date(2026, 4, 20)
    assert parsed.due_at_info is None
    assert parsed.weird_due_time is False


def test_parse_task_note_is_unmanaged_when_marker_is_missing():
    parsed = parse_task_note("Homework\nDue: 2026-04-15\n")

    assert parsed.managed is False
    assert parsed.writable is False
    assert parsed.diagnostics == ()


def test_parse_task_note_rejects_nonempty_content_after_marker():
    parsed = parse_task_note(
        """
Homework
Due: 2026-04-15
Canvas:
Trailing text
"""
    )

    assert parsed.managed is False
    assert parsed.writable is False
    assert diagnostic_codes(parsed) == ["marker_not_last"]


def test_parse_task_note_rejects_multiple_marker_like_lines():
    parsed = parse_task_note(
        """
Homework
Due: 2026-04-15
Canvas:
Canvas:
"""
    )

    assert parsed.managed is False
    assert parsed.writable is False
    assert diagnostic_codes(parsed) == ["multiple_markers"]


def test_parse_task_note_rejects_malformed_marker_like_line():
    parsed = parse_task_note(
        """
Homework
Due: 2026-04-15
Canvas: managed
"""
    )

    assert parsed.managed is False
    assert parsed.writable is False
    assert diagnostic_codes(parsed) == ["malformed_marker"]
    assert parsed.diagnostics[0].line_number == 4


def test_parse_task_note_keeps_missing_due_managed_but_non_writable():
    parsed = parse_task_note(
        """
Homework
Canvas:
"""
    )

    assert parsed.managed is True
    assert parsed.writable is False
    assert diagnostic_codes(parsed) == ["missing_due"]


def test_parse_task_note_keeps_malformed_due_managed_but_non_writable():
    parsed = parse_task_note(
        """
Homework
Due: tomorrow
Canvas:
"""
    )

    assert parsed.managed is True
    assert parsed.writable is False
    assert diagnostic_codes(parsed) == ["malformed_due"]
    assert parsed.due_line_number == 3


def test_parse_task_note_keeps_multiple_due_lines_managed_but_non_writable():
    parsed = parse_task_note(
        """
Homework
Due: 2026-04-15
Due: 2026-04-16
Canvas:
"""
    )

    assert parsed.managed is True
    assert parsed.writable is False
    assert diagnostic_codes(parsed) == ["multiple_due_lines"]


def test_parse_task_note_treats_indented_due_prefix_in_freeform_content_as_reserved():
    parsed = parse_task_note(
        """
Homework
  Due: mention this in class
Due: 2026-04-15
Canvas:
"""
    )

    assert parsed.managed is True
    assert parsed.writable is False
    assert diagnostic_codes(parsed) == ["multiple_due_lines"]


def test_parse_task_note_rejects_marker_like_freeform_content_even_with_trailing_marker():
    parsed = parse_task_note(
        """
Homework
Canvas: mention this in class
Due: 2026-04-15
Canvas:
"""
    )

    assert parsed.managed is False
    assert parsed.writable is False
    assert diagnostic_codes(parsed) == ["malformed_marker", "multiple_markers"]
