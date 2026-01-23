"""State persistence for Canvas → Things Mail Bridge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .canvas_client import Assignment


class StateStore:
    """JSON-backed storage of assignment fingerprints and pending assignments."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: Dict[str, Any] = {}
        self._pending: List[Dict[str, Any]] = []

    def load(self) -> None:
        if not self.path.exists():
            self._data = {}
            self._pending = []
            return
        with self.path.open("r", encoding="utf-8") as fh:
            try:
                raw = json.load(fh)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"State file {self.path} is corrupted: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeError(f"State file {self.path} must contain an object.")
        
        # Handle legacy format (just fingerprints) and new format (with pending)
        if "notified" in raw:
            self._data = {str(k): str(v) for k, v in raw.get("notified", {}).items()}
            self._pending = raw.get("pending", [])
        else:
            # Legacy format: just a dict of fingerprints
            self._data = {str(k): str(v) for k, v in raw.items()}
            self._pending = []

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump({"notified": self._data, "pending": self._pending}, fh, indent=2, sort_keys=True)

    def should_notify(self, key: str, updated_at: str) -> bool:
        previous = self._data.get(key)
        return previous is None or updated_at > previous

    def mark_notified(self, key: str, updated_at: str) -> None:
        self._data[key] = updated_at

    def bulk_mark(self, entries: Iterable[tuple[str, str]]) -> None:
        for key, updated_at in entries:
            self.mark_notified(key, updated_at)

    def snapshot(self) -> Dict[str, str]:
        return dict(self._data)

    def add_pending(self, assignment: Assignment) -> None:
        """Add an assignment to the pending queue for retry."""
        assignment_dict = {
            "course_id": assignment.course_id,
            "course_alias": assignment.course_alias,
            "assignment_id": assignment.assignment_id,
            "title": assignment.title,
            "html_url": assignment.html_url,
            "updated_at": assignment.updated_at,
            "due_at": assignment.due_at,
            "lock_at": assignment.lock_at,
            "unlock_at": assignment.unlock_at,
            "description": assignment.description,
            "points_possible": assignment.points_possible,
            "submission_types": assignment.submission_types,
            "published": assignment.published,
        }
        self._pending.append(assignment_dict)

    def get_pending(self) -> List[Assignment]:
        """Retrieve all pending assignments."""
        assignments = []
        for data in self._pending:
            assignments.append(
                Assignment(
                    course_id=data["course_id"],
                    course_alias=data["course_alias"],
                    assignment_id=data["assignment_id"],
                    title=data["title"],
                    html_url=data["html_url"],
                    updated_at=data["updated_at"],
                    due_at=data.get("due_at"),
                    lock_at=data.get("lock_at"),
                    unlock_at=data.get("unlock_at"),
                    description=data.get("description"),
                    points_possible=data.get("points_possible"),
                    submission_types=data.get("submission_types", []),
                    published=data.get("published", True),
                )
            )
        return assignments

    def clear_pending(self) -> None:
        """Clear all pending assignments."""
        self._pending = []

    def remove_pending(self, assignment: Assignment) -> None:
        """Remove a specific assignment from pending by fingerprint."""
        fingerprint = assignment.fingerprint()
        self._pending = [
            p for p in self._pending
            if f"{p['course_id']}:{p['assignment_id']}:{p['updated_at']}" != fingerprint
        ]
