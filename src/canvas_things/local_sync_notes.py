"""Parse Canvas-managed note markers and Due lines for local sync."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .managed_notes import (
    DueAtInfo,
    DUE_LINE_PREFIX,
    LEGACY_DUE_AT_PREFIX,
    MANAGED_NOTE_MARKER,
    extract_legacy_due_date_text,
    find_prefixed_note_lines,
    parse_due_at_info,
)


@dataclass(frozen=True)
class NoteDiagnostic:
    """Structured parse diagnostic for a Things task note."""

    code: str
    message: str
    line_number: int | None = None
    line: str | None = None


@dataclass(frozen=True)
class ParsedTaskNote:
    """Management and due-date state extracted from a task note."""

    managed: bool
    writable: bool
    due_date: date | None
    effective_deadline_date: date | None
    early_schedule_date: date | None
    due_text: str | None
    due_at_info: DueAtInfo | None
    weird_due_time: bool
    weird_due_display_time: str | None
    marker_line_number: int | None
    due_line_number: int | None
    diagnostics: tuple[NoteDiagnostic, ...]


def parse_task_note(note: str | None) -> ParsedTaskNote:
    """Parse note management and Due-line state using the v1 rules."""

    lines = (note or "").splitlines()
    marker_like_lines = _find_marker_like_lines(lines)
    valid_markers = [entry for entry in marker_like_lines if entry[2] == MANAGED_NOTE_MARKER]
    diagnostics: list[NoteDiagnostic] = []

    for line_number, raw_line, stripped_line in marker_like_lines:
        if stripped_line != MANAGED_NOTE_MARKER:
            diagnostics.append(
                NoteDiagnostic(
                    code="malformed_marker",
                    message=f"Marker-like lines must be exactly {MANAGED_NOTE_MARKER!r}.",
                    line_number=line_number,
                    line=raw_line,
                )
            )

    if len(marker_like_lines) > 1:
        diagnostics.append(
            NoteDiagnostic(
                code="multiple_markers",
                message="Exactly one marker-like line is allowed in a managed note.",
            )
        )

    if len(marker_like_lines) != 1 or len(valid_markers) != 1:
        if marker_like_lines:
            return ParsedTaskNote(
                managed=False,
                writable=False,
                due_date=None,
                effective_deadline_date=None,
                early_schedule_date=None,
                due_text=None,
                due_at_info=None,
                weird_due_time=False,
                weird_due_display_time=None,
                marker_line_number=None,
                due_line_number=None,
                diagnostics=tuple(diagnostics),
            )
        return ParsedTaskNote(
            managed=False,
            writable=False,
            due_date=None,
            effective_deadline_date=None,
            early_schedule_date=None,
            due_text=None,
            due_at_info=None,
            weird_due_time=False,
            weird_due_display_time=None,
            marker_line_number=None,
            due_line_number=None,
            diagnostics=(),
        )

    marker_line_number = valid_markers[0][0]
    last_nonempty_line_number = _last_nonempty_line_number(lines)
    if last_nonempty_line_number != marker_line_number:
        diagnostics.append(
            NoteDiagnostic(
                code="marker_not_last",
                message="The marker must be the last non-empty line in the note.",
                line_number=marker_line_number,
                line=valid_markers[0][1],
            )
        )
        return ParsedTaskNote(
            managed=False,
            writable=False,
            due_date=None,
            effective_deadline_date=None,
            early_schedule_date=None,
            due_text=None,
            due_at_info=None,
            weird_due_time=False,
            weird_due_display_time=None,
            marker_line_number=marker_line_number,
            due_line_number=None,
            diagnostics=tuple(diagnostics),
        )

    due_lines = _find_due_lines(lines)
    if not due_lines:
        diagnostics.append(
            NoteDiagnostic(
                code="missing_due",
                message="Managed notes need exactly one parseable Due: line to be writable.",
            )
        )
        return ParsedTaskNote(
            managed=True,
            writable=False,
            due_date=None,
            effective_deadline_date=None,
            early_schedule_date=None,
            due_text=None,
            due_at_info=None,
            weird_due_time=False,
            weird_due_display_time=None,
            marker_line_number=marker_line_number,
            due_line_number=None,
            diagnostics=tuple(diagnostics),
        )

    if len(due_lines) > 1:
        diagnostics.append(
            NoteDiagnostic(
                code="multiple_due_lines",
                message="Managed notes must contain exactly one Due: line.",
            )
        )
        return ParsedTaskNote(
            managed=True,
            writable=False,
            due_date=None,
            effective_deadline_date=None,
            early_schedule_date=None,
            due_text=None,
            due_at_info=None,
            weird_due_time=False,
            weird_due_display_time=None,
            marker_line_number=marker_line_number,
            due_line_number=None,
            diagnostics=tuple(diagnostics),
        )

    due_line_number, raw_due_line, stripped_due_line = due_lines[0]
    due_text = stripped_due_line[len(DUE_LINE_PREFIX) :].strip()
    if not due_text:
        diagnostics.append(
            NoteDiagnostic(
                code="malformed_due",
                message="Due: must contain an ISO date in YYYY-MM-DD format.",
                line_number=due_line_number,
                line=raw_due_line,
            )
        )
        return ParsedTaskNote(
            managed=True,
            writable=False,
            due_date=None,
            effective_deadline_date=None,
            early_schedule_date=None,
            due_text=None,
            due_at_info=None,
            weird_due_time=False,
            weird_due_display_time=None,
            marker_line_number=marker_line_number,
            due_line_number=due_line_number,
            diagnostics=tuple(diagnostics),
        )

    due_at_info = _parse_due_at_info_from_lines(lines)
    effective_due_text = (
        due_at_info.due_date.isoformat()
        if due_at_info is not None
        else _prefer_local_due_text_from_due_at(lines, fallback_due_text=due_text)
    )

    try:
        due_date = date.fromisoformat(effective_due_text)
    except ValueError:
        diagnostics.append(
            NoteDiagnostic(
                code="malformed_due",
                message="Due: must contain an ISO date in YYYY-MM-DD format.",
                line_number=due_line_number,
                line=raw_due_line,
            )
        )
        return ParsedTaskNote(
            managed=True,
            writable=False,
            due_date=None,
            effective_deadline_date=None,
            early_schedule_date=None,
            due_text=effective_due_text,
            due_at_info=due_at_info,
            weird_due_time=False,
            weird_due_display_time=None,
            marker_line_number=marker_line_number,
            due_line_number=due_line_number,
            diagnostics=tuple(diagnostics),
        )

    weird_due_time = due_at_info.is_weird_time if due_at_info is not None else False
    early_schedule_date = due_date - timedelta(days=1) if weird_due_time else None

    return ParsedTaskNote(
        managed=True,
        writable=True,
        due_date=due_date,
        effective_deadline_date=due_date,
        early_schedule_date=early_schedule_date,
        due_text=effective_due_text,
        due_at_info=due_at_info,
        weird_due_time=weird_due_time,
        weird_due_display_time=due_at_info.display_time if weird_due_time and due_at_info is not None else None,
        marker_line_number=marker_line_number,
        due_line_number=due_line_number,
        diagnostics=tuple(diagnostics),
    )


def _find_marker_like_lines(lines: list[str]) -> list[tuple[int, str, str]]:
    return find_prefixed_note_lines(lines, MANAGED_NOTE_MARKER)


def _find_due_lines(lines: list[str]) -> list[tuple[int, str, str]]:
    return find_prefixed_note_lines(lines, DUE_LINE_PREFIX)


def _find_due_at_lines(lines: list[str]) -> list[tuple[int, str, str]]:
    return find_prefixed_note_lines(lines, LEGACY_DUE_AT_PREFIX)


def _parse_due_at_info_from_lines(lines: list[str]) -> DueAtInfo | None:
    for _, _, stripped_due_at_line in _find_due_at_lines(lines):
        due_at_text = stripped_due_at_line[len(LEGACY_DUE_AT_PREFIX) :].strip()
        due_at_info = parse_due_at_info(due_at_text)
        if due_at_info is not None:
            return due_at_info
    return None


def _prefer_local_due_text_from_due_at(lines: list[str], *, fallback_due_text: str) -> str:
    for _, _, stripped_due_at_line in _find_due_at_lines(lines):
        due_at_text = stripped_due_at_line[len(LEGACY_DUE_AT_PREFIX) :].strip()
        preferred_due_text = extract_legacy_due_date_text(due_at_text)
        if preferred_due_text is not None:
            return preferred_due_text
    return fallback_due_text


def _last_nonempty_line_number(lines: list[str]) -> int | None:
    for index in range(len(lines), 0, -1):
        if lines[index - 1].strip():
            return index
    return None


__all__ = [
    "DUE_LINE_PREFIX",
    "MANAGED_NOTE_MARKER",
    "NoteDiagnostic",
    "ParsedTaskNote",
    "parse_task_note",
]
