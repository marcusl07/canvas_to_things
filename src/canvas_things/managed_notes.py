"""Shared primitives for Canvas-managed Things note formatting and parsing."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time

MANAGED_NOTE_MARKER = "Canvas:"
DUE_LINE_PREFIX = "Due:"
COURSE_LINE_PREFIX = "Course:"
LEGACY_DUE_AT_PREFIX = "Due At:"
WEIRD_DUE_TITLE_RE = re.compile(r"^\[DUE (?P<time>\d{4})\]\s+")
_LEGACY_DUE_DATE_RE = re.compile(r"^(?P<due_date>\d{4}-\d{2}-\d{2})(?:\b|T)")
_LEGACY_LOCAL_DUE_DATE_RE = re.compile(r"\((?P<due_date>\d{4}-\d{2}-\d{2})(?:\b|T)")
_LOCAL_DUE_AT_RE = re.compile(
    r"\((?P<due_date>\d{4}-\d{2}-\d{2})\s+"
    r"(?P<hour>\d{2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?"
)


@dataclass(frozen=True)
class DueAtInfo:
    """Local date/time metadata parsed from a Due At note line."""

    due_date: date
    due_time: time

    @property
    def display_time(self) -> str:
        return f"{self.due_time.hour:02d}{self.due_time.minute:02d}"

    @property
    def is_weird_time(self) -> bool:
        return self.due_time.hour != 23 or self.due_time.minute != 59


def normalize_note_line(raw_line: str) -> str:
    """Normalize whitespace for reserved managed-note line matching."""
    return raw_line.strip()


def format_prefixed_note_line(prefix: str, value: str) -> str:
    """Format a managed-note field using the canonical prefix spacing."""
    normalized_value = value.strip()
    if not normalized_value:
        return prefix
    return f"{prefix} {normalized_value}"


def format_due_line(due_text: str) -> str:
    """Format the managed Due line with canonical spacing."""
    return format_prefixed_note_line(DUE_LINE_PREFIX, due_text)


def format_managed_marker_line() -> str:
    """Return the exact managed-note marker line."""
    return MANAGED_NOTE_MARKER


def format_weird_due_title_prefix(display_time: str) -> str:
    """Return the visible Things title prefix for non-23:59 Canvas due times."""

    return f"[DUE {display_time}] "


def strip_weird_due_title_prefix(title: str) -> str:
    """Strip one leading weird due-time prefix from a title fragment."""

    return WEIRD_DUE_TITLE_RE.sub("", title, count=1)


def parse_due_at_info(due_at_text: str) -> DueAtInfo | None:
    """Parse local date/time metadata from the current Due At line format."""

    match = _LOCAL_DUE_AT_RE.search(due_at_text.strip())
    if match is None:
        return None

    try:
        due_date = date.fromisoformat(match.group("due_date"))
        due_time = time(
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second") or "0"),
        )
    except ValueError:
        return None
    return DueAtInfo(due_date=due_date, due_time=due_time)


def sanitize_freeform_note_line(raw_line: str) -> str:
    """Escape reserved managed-note prefixes when they appear in freeform content."""

    stripped_line = raw_line.lstrip()
    if stripped_line.startswith((DUE_LINE_PREFIX, MANAGED_NOTE_MARKER)):
        return f"- {stripped_line}"
    return raw_line


def sanitize_freeform_note_lines(lines: Sequence[str]) -> list[str]:
    """Escape reserved managed-note prefixes across a sequence of freeform lines."""

    return [sanitize_freeform_note_line(raw_line) for raw_line in lines]


def find_prefixed_note_lines(lines: list[str], prefix: str) -> list[tuple[int, str, str]]:
    """Find note lines whose normalized content starts with the given prefix."""
    matches: list[tuple[int, str, str]] = []
    for index, raw_line in enumerate(lines, start=1):
        normalized_line = normalize_note_line(raw_line)
        if normalized_line.startswith(prefix):
            matches.append((index, raw_line, normalized_line))
    return matches


def extract_legacy_due_date_text(due_value: str) -> str | None:
    """Extract the canonical local YYYY-MM-DD date from a legacy Due At value."""

    normalized_value = due_value.strip()

    local_match = _LEGACY_LOCAL_DUE_DATE_RE.search(normalized_value)
    if local_match is not None:
        return local_match.group("due_date")

    match = _LEGACY_DUE_DATE_RE.match(normalized_value)
    if match is None:
        return None
    return match.group("due_date")


def rewrite_legacy_mail_to_things_note(note: str | None) -> str | None:
    """Convert one legacy Mail-to-Things note body into the canonical managed-note form."""

    lines = (note or "").splitlines()
    if not lines:
        return None

    header_lines, tail_lines = _split_note_header_and_tail(lines)
    if not any(normalize_note_line(raw_line).startswith(COURSE_LINE_PREFIX) for raw_line in header_lines):
        return None

    due_indices = [
        index for index, raw_line in enumerate(header_lines) if normalize_note_line(raw_line).startswith(DUE_LINE_PREFIX)
    ]
    if len(due_indices) != 1:
        return None
    if any(normalize_note_line(raw_line).startswith(LEGACY_DUE_AT_PREFIX) for raw_line in header_lines):
        return None

    due_index = due_indices[0]
    due_line = normalize_note_line(header_lines[due_index])

    legacy_due_value = due_line[len(DUE_LINE_PREFIX) :].strip()
    if not legacy_due_value:
        return None

    due_text = extract_legacy_due_date_text(legacy_due_value)
    if due_text is None:
        return None

    rewritten_lines: list[str] = []
    for index, raw_line in enumerate(header_lines):
        if index == due_index:
            rewritten_lines.append(format_due_line(due_text))
            rewritten_lines.append(f"{LEGACY_DUE_AT_PREFIX} {legacy_due_value}")
            continue
        rewritten_lines.append(raw_line)

    trimmed_tail = list(tail_lines)
    while trimmed_tail and not trimmed_tail[-1].strip():
        trimmed_tail.pop()

    if trimmed_tail:
        rewritten_lines.append("")
        rewritten_lines.extend(sanitize_freeform_note_lines(trimmed_tail))

    rewritten_lines.append("")
    rewritten_lines.append(format_managed_marker_line())
    return "\n".join(rewritten_lines)


def _split_note_header_and_tail(lines: Sequence[str]) -> tuple[list[str], list[str]]:
    for index, raw_line in enumerate(lines):
        if not raw_line.strip():
            return list(lines[:index]), list(lines[index + 1 :])
    return list(lines), []


__all__ = [
    "COURSE_LINE_PREFIX",
    "DUE_LINE_PREFIX",
    "DueAtInfo",
    "LEGACY_DUE_AT_PREFIX",
    "MANAGED_NOTE_MARKER",
    "extract_legacy_due_date_text",
    "find_prefixed_note_lines",
    "format_due_line",
    "format_managed_marker_line",
    "format_prefixed_note_line",
    "format_weird_due_title_prefix",
    "normalize_note_line",
    "parse_due_at_info",
    "rewrite_legacy_mail_to_things_note",
    "sanitize_freeform_note_line",
    "sanitize_freeform_note_lines",
    "strip_weird_due_title_prefix",
]
