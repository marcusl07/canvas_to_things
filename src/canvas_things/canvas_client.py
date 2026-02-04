"""Canvas API client for fetching assignments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import logging
import re

import requests

from . import config

logger = logging.getLogger(__name__)

ASSIGNMENTS_ENDPOINT = "/api/v1/courses/{course_id}/assignments"
LINK_RE = re.compile(r'<([^>]+)>; rel="([^"]+)"')


@dataclass
class Assignment:
    course_id: int
    course_alias: str
    assignment_id: int
    title: str
    html_url: str
    updated_at: str
    due_at: Optional[str]
    lock_at: Optional[str]
    unlock_at: Optional[str]
    description: Optional[str]
    points_possible: Optional[float]
    submission_types: List[str]
    published: bool
    is_update_notification: bool = False

    def fingerprint(self) -> str:
        return f"{self.course_id}:{self.assignment_id}:{self.updated_at}"


class CanvasAPIError(RuntimeError):
    pass


class CanvasClient:
    def __init__(self, settings: config.Settings, session: Optional[requests.Session] = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()

    def fetch_assignments(self, course: config.CourseConfig, per_page: int = 50) -> List[Assignment]:
        url = self.settings.canvas.base_url + ASSIGNMENTS_ENDPOINT.format(course_id=course.course_id)
        params = {
            "per_page": per_page,
            "order_by": "due_at",
        }
        headers = {
            "Authorization": f"Bearer {self.settings.canvas_token}",
        }

        assignments: List[Assignment] = []
        next_url = url
        while next_url:
            response = self.session.get(next_url, params=params, headers=headers, timeout=30)
            self._raise_for_status(response, course.course_id)
            payload = response.json()
            assignments.extend(self._normalize_assignments(payload, course))
            next_url = self._extract_next_link(response.headers)
            params = None  # only use params on first request
        return assignments

    def _normalize_assignments(self, payload: Iterable[Mapping[str, Any]], course: config.CourseConfig) -> List[Assignment]:
        normalized: List[Assignment] = []
        for item in payload:
            assignment_id = self._to_int(item.get("id"))
            if assignment_id is None:
                logger.debug("Skipping assignment with missing id: %s", item)
                continue
            description: Optional[str] = None
            if course.include_description:
                description = self._to_str(item.get("description"))
            normalized.append(
                Assignment(
                    course_id=course.course_id,
                    course_alias=course.alias,
                    assignment_id=assignment_id,
                    title=self._to_str(item.get("name"), "Untitled") or "Untitled",
                    html_url=self._to_str(item.get("html_url"), "") or "",
                    updated_at=self._to_str(item.get("updated_at"), "") or "",
                    due_at=self._to_str(item.get("due_at")),
                    lock_at=self._to_str(item.get("lock_at")),
                    unlock_at=self._to_str(item.get("unlock_at")),
                    description=description,
                    points_possible=self._to_float(item.get("points_possible")),
                    submission_types=self._to_str_list(item.get("submission_types")),
                    published=bool(item.get("published", True)),
                )
            )
        return normalized

    def _to_str(self, value: object, default: Optional[str] = None) -> Optional[str]:
        if value is None:
            return default
        if isinstance(value, str):
            stripped = value.strip()
            return stripped if stripped else default
        try:
            return str(value)
        except Exception:  # pragma: no cover - extremely defensive
            return default

    def _to_str_list(self, value: object) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            single = self._to_str(value)
            return [single] if single else []
        if isinstance(value, Sequence):
            items: List[str] = []
            for entry in value:
                text = self._to_str(entry)
                if text:
                    items.append(text)
            return items
        return []

    def _to_float(self, value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _to_int(self, value: Any) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _extract_next_link(self, headers: Mapping[str, str]) -> Optional[str]:
        link_header = headers.get("Link")
        if not link_header:
            return None
        for part in link_header.split(","):
            match = LINK_RE.search(part)
            if match and match.group(2) == "next":
                return match.group(1)
        return None

    def _raise_for_status(self, response: requests.Response, course_id: int) -> None:
        self._log_rate_headers(response.headers)
        status = response.status_code
        if status < 400:
            return

        url = getattr(getattr(response, "request", None), "url", "unknown")
        snippet = (response.text or "").strip()
        if len(snippet) > 200:
            snippet = snippet[:197] + "..."

        if status == 401:
            detail = "Unauthorized – verify CANVAS_TOKEN permissions."
        elif status == 403:
            detail = "Forbidden – account lacks access to this course."
        elif status == 404:
            detail = "Not found – course or assignments endpoint missing."
        elif status == 429:
            retry_after = response.headers.get("Retry-After")
            detail = "Rate limited – Canvas returned HTTP 429."
            if retry_after:
                detail += f" Retry after {retry_after} seconds."
        else:
            detail = snippet or "Unexpected Canvas API error."

        message = f"Canvas API error {status} for course {course_id} ({url}): {detail}"
        raise CanvasAPIError(message)

    def _log_rate_headers(self, headers: Mapping[str, str]) -> None:
        remaining = headers.get("X-Rate-Limit-Remaining")
        cost = headers.get("X-Request-Cost")
        retry_after = headers.get("Retry-After")
        if remaining or cost or retry_after:
            logger.debug(
                "Canvas rate info: remaining=%s cost=%s retry_after=%s",
                remaining,
                cost,
                retry_after,
            )
