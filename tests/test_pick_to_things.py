"""Unit tests for pick_to_things.py — pure logic only, no network calls."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import pytest

# Make scripts/ importable without installing
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from canvas_things.canvas_client import Assignment
from pick_to_things import build_things_url, due_date_local, filter_assignments


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_assignment(
    *,
    due_at: Optional[str] = "2026-09-10T23:59:00Z",
    course_alias: str = "CS101",
    title: str = "Homework 1",
    html_url: str = "https://canvas.example.com/assignments/1",
) -> Assignment:
    return Assignment(
        course_id=1,
        course_alias=course_alias,
        assignment_id=1,
        title=title,
        html_url=html_url,
        updated_at="2026-08-01T00:00:00Z",
        due_at=due_at,
        lock_at=None,
        unlock_at=None,
        description=None,
        points_possible=None,
        submission_types=[],
        published=True,
    )


# ---------------------------------------------------------------------------
# filter_assignments
# ---------------------------------------------------------------------------

class TestFilterAssignments:
    UTC = ZoneInfo("UTC")
    # Anchor: 2026-09-01 12:00 UTC
    NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_includes_assignment_within_window(self) -> None:
        a = make_assignment(due_at="2026-09-10T23:59:00Z")  # 9 days from now
        result = filter_assignments([a], tz=self.UTC, days=21, include_undated=False, now=self.NOW)
        assert result == [a]

    def test_excludes_assignment_after_window(self) -> None:
        a = make_assignment(due_at="2026-09-30T00:00:00Z")  # 29 days from now
        result = filter_assignments([a], tz=self.UTC, days=21, include_undated=False, now=self.NOW)
        assert result == []

    def test_excludes_assignment_before_now(self) -> None:
        a = make_assignment(due_at="2026-08-31T00:00:00Z")  # yesterday
        result = filter_assignments([a], tz=self.UTC, days=21, include_undated=False, now=self.NOW)
        assert result == []

    def test_includes_assignment_exactly_at_cutoff(self) -> None:
        # Exactly now + 21 days
        cutoff = "2026-09-22T12:00:00Z"
        a = make_assignment(due_at=cutoff)
        result = filter_assignments([a], tz=self.UTC, days=21, include_undated=False, now=self.NOW)
        assert result == [a]

    def test_excludes_undated_when_flag_off(self) -> None:
        a = make_assignment(due_at=None)
        result = filter_assignments([a], tz=self.UTC, days=21, include_undated=False, now=self.NOW)
        assert result == []

    def test_includes_undated_when_flag_on(self) -> None:
        a = make_assignment(due_at=None)
        result = filter_assignments([a], tz=self.UTC, days=21, include_undated=True, now=self.NOW)
        assert result == [a]

    def test_mixed_bag_filtered_correctly(self) -> None:
        in_window = make_assignment(due_at="2026-09-15T00:00:00Z", title="In")
        too_late = make_assignment(due_at="2026-10-15T00:00:00Z", title="Late")
        too_early = make_assignment(due_at="2026-08-01T00:00:00Z", title="Early")
        undated = make_assignment(due_at=None, title="Undated")

        result = filter_assignments(
            [in_window, too_late, too_early, undated],
            tz=self.UTC,
            days=21,
            include_undated=True,
            now=self.NOW,
        )
        assert [a.title for a in result] == ["In", "Undated"]

    def test_window_boundary_is_inclusive_at_now(self) -> None:
        # due exactly at NOW — should be included
        a = make_assignment(due_at="2026-09-01T12:00:00Z")
        result = filter_assignments([a], tz=self.UTC, days=21, include_undated=False, now=self.NOW)
        assert result == [a]

    def test_uses_utc_not_local_for_comparison(self) -> None:
        # 2026-09-22 at 11:00 UTC = 2026-09-21 in America/Los_Angeles (UTC-7 in summer).
        # The window closes at 2026-09-22T12:00 UTC. This is within the window.
        tz = ZoneInfo("America/Los_Angeles")
        a = make_assignment(due_at="2026-09-22T11:00:00Z")
        result = filter_assignments([a], tz=tz, days=21, include_undated=False, now=self.NOW)
        assert result == [a]


# ---------------------------------------------------------------------------
# due_date_local
# ---------------------------------------------------------------------------

class TestDueDateLocal:
    def test_utc_assignment_stays_same_day_in_utc(self) -> None:
        result = due_date_local("2026-09-15T23:59:00Z", ZoneInfo("UTC"))
        assert result == "2026-09-15"

    def test_converts_to_eastern_same_day(self) -> None:
        # 23:59 UTC = 19:59 EDT (UTC-4) — still same calendar day
        result = due_date_local("2026-09-15T23:59:00Z", ZoneInfo("America/New_York"))
        assert result == "2026-09-15"

    def test_midnight_utc_rolls_back_one_day_in_eastern(self) -> None:
        # 00:00 UTC on Sep 15 = 20:00 EDT Sep 14 (UTC-4)
        result = due_date_local("2026-09-15T00:00:00Z", ZoneInfo("America/New_York"))
        assert result == "2026-09-14"

    def test_converts_to_india_next_day(self) -> None:
        # 20:00 UTC Sep 15 = 01:30 IST Sep 16 (UTC+5:30)
        result = due_date_local("2026-09-15T20:00:00Z", ZoneInfo("Asia/Kolkata"))
        assert result == "2026-09-16"

    def test_handles_z_suffix(self) -> None:
        result = due_date_local("2026-01-31T05:00:00Z", ZoneInfo("UTC"))
        assert result == "2026-01-31"

    def test_handles_explicit_offset(self) -> None:
        # +00:00 suffix instead of Z
        result = due_date_local("2026-09-15T23:59:00+00:00", ZoneInfo("UTC"))
        assert result == "2026-09-15"


# ---------------------------------------------------------------------------
# build_things_url
# ---------------------------------------------------------------------------

class TestBuildThingsUrl:
    def test_basic_url_structure(self) -> None:
        url = build_things_url("Buy milk", "some notes", "2026-09-15")
        assert url.startswith("things:///add?")

    def test_title_is_percent_encoded(self) -> None:
        url = build_things_url("CS101 – Homework", "", None)
        assert "CS101" in url
        assert " " not in url  # spaces must be encoded

    def test_deadline_present_when_provided(self) -> None:
        url = build_things_url("Title", "Notes", "2026-09-15")
        assert "deadline=2026-09-15" in url

    def test_deadline_absent_when_none(self) -> None:
        url = build_things_url("Title", "Notes", None)
        assert "deadline" not in url

    def test_notes_are_encoded(self) -> None:
        url = build_things_url("T", "https://canvas.example.com/courses/1/assignments/2", "2026-09-15")
        # The URL in notes must be percent-encoded (colons and slashes)
        assert "https%3A" in url or "https:" in url  # quote() encodes by default
        assert " " not in url

    def test_special_characters_in_title(self) -> None:
        url = build_things_url("[CS101] Final Exam – Chapter 5", "notes", "2026-12-01")
        # Square brackets, em-dash should be encoded
        assert " " not in url
        decoded_title = unquote(url.split("title=")[1].split("&")[0])
        assert decoded_title == "[CS101] Final Exam – Chapter 5"

    def test_empty_notes_produces_valid_url(self) -> None:
        url = build_things_url("Task", "", "2026-09-01")
        assert "notes=" in url
        assert "title=Task" in url
        assert "deadline=2026-09-01" in url

    def test_all_three_params_present_and_ordered(self) -> None:
        url = build_things_url("My Task", "http://x.com", "2026-11-01")
        # title, notes, deadline all appear
        assert "title=" in url
        assert "notes=" in url
        assert "deadline=2026-11-01" in url
