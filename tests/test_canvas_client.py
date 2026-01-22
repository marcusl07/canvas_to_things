from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List

import pytest

from canvas_things import config
from canvas_things.canvas_client import Assignment, CanvasAPIError, CanvasClient


class DummyResponse:
    def __init__(
        self,
        *,
        json_data: Iterable[Dict[str, Any]] | None = None,
        status_code: int = 200,
        headers: Dict[str, str] | None = None,
        text: str = "",
        url: str = "https://canvas.example.com/api",
    ) -> None:
        self._json_data = list(json_data or [])
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.request = SimpleNamespace(url=url)

    def json(self) -> List[Dict[str, Any]]:
        return list(self._json_data)


class FakeSession:
    def __init__(self, responses: Iterable[DummyResponse]):
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def get(self, url: str, params: Dict[str, Any] | None, headers: Dict[str, str], timeout: int = 30) -> DummyResponse:
        if not self.responses:
            raise AssertionError("No more responses queued for FakeSession")
        response = self.responses.pop(0)
        self.calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return response


@pytest.fixture
def settings() -> config.Settings:
    return config.Settings(
        canvas=config.CanvasConfig(base_url="https://canvas.example.com", courses=[]),
        email=config.EmailConfig(
            from_name="Bot",
            subject_template="{course_alias} – {title}",
            include_description=True,
            max_description_chars=500,
        ),
        run=config.RunConfig(timezone="UTC", dry_run=False, state_file=Path("data/state.json")),
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user",
        smtp_pass="pass",
        things_email="things@example.com",
        canvas_token="token",
    )


def test_fetch_assignments_handles_pagination_and_normalizes_fields(settings: config.Settings) -> None:
    course = config.CourseConfig(course_id=42, alias="MATH")
    first_page = DummyResponse(
        json_data=[
            {
                "id": "10",
                "name": " Assignment 1 ",
                "html_url": "https://canvas.example.com/assignments/10",
                "updated_at": "2024-01-01T00:00:00Z",
                "due_at": "2024-01-05T00:00:00Z",
                "description": "  <p>Hello world</p>  ",
                "points_possible": "10",
                "submission_types": ("online_upload", "discussion_topic"),
                "published": True,
            }
        ],
        headers={
            "Link": '<https://canvas.example.com/api/v1/courses/42/assignments?page=2>; rel="next"',
            "X-Rate-Limit-Remaining": "99",
            "X-Request-Cost": "0.38",
        },
    )
    second_page = DummyResponse(
        json_data=[
            {
                "id": 11,
                "name": None,
                "html_url": None,
                "updated_at": None,
                "submission_types": "online_text_entry",
                "published": False,
            }
        ],
        headers={"X-Rate-Limit-Remaining": "98"},
    )
    session = FakeSession([first_page, second_page])
    client = CanvasClient(settings=settings, session=session)

    assignments = client.fetch_assignments(course, per_page=2)

    assert [a.assignment_id for a in assignments] == [10, 11]
    first, second = assignments

    assert first.title == "Assignment 1"
    assert first.due_at == "2024-01-05T00:00:00Z"
    assert first.description == "<p>Hello world</p>"
    assert first.points_possible == 10.0
    assert first.submission_types == ["online_upload", "discussion_topic"]
    assert first.published is True

    assert second.title == "Untitled"
    assert second.html_url == ""
    assert second.updated_at == ""
    assert second.description is None
    assert second.submission_types == ["online_text_entry"]
    assert second.published is False

    assert session.calls[0]["params"] == {"per_page": 2, "order_by": "due_at"}
    assert session.calls[1]["params"] is None


def test_normalize_skips_description_when_disabled(settings: config.Settings) -> None:
    client = CanvasClient(settings=settings)
    course = config.CourseConfig(course_id=1, alias="PHY", include_description=False)
    payload = [
        {
            "id": 99,
            "name": "Test",
            "description": "should be skipped",
            "submission_types": None,
            "published": True,
        }
    ]

    assignments = client._normalize_assignments(payload, course)

    assert assignments[0].description is None
    assert assignments[0].submission_types == []


@pytest.mark.parametrize(
    "status,text,headers,expected",
    [
        (401, "bad token", {}, "Unauthorized"),
        (403, "forbidden", {}, "Forbidden"),
        (404, "missing", {}, "Not found"),
        (429, "too many", {"Retry-After": "10"}, "Retry after 10"),
    ],
)
def test_raise_for_status_raises_descriptive_errors(
    settings: config.Settings, status: int, text: str, headers: Dict[str, str], expected: str
) -> None:
    client = CanvasClient(settings=settings)
    response = DummyResponse(status_code=status, text=text, headers=headers)

    with pytest.raises(CanvasAPIError) as exc:
        client._raise_for_status(response, course_id=7)

    message = str(exc.value)
    assert f"{status}" in message
    assert expected in message
