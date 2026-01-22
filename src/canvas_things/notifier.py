"""Email notifications via Mail to Things.

Formats assignments into plain-text emails and delivers them through SMTP to
`@things.email` inboxes. Supports dry-runs for local testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage
from smtplib import SMTP, SMTPException
from ssl import create_default_context
from typing import Iterable, List, Optional, Protocol

import logging
import textwrap
import time

from . import config
from .canvas_client import Assignment

logger = logging.getLogger(__name__)


class EmailTransport(Protocol):
    def send(self, message: EmailMessage) -> None:
        ...


@dataclass
class NotificationResult:
    sent: List[str]
    skipped: List[str]


class SMTPTransport:
    """Simple SMTP TLS transport with retry support."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        retries: int = 3,
        backoff_seconds: float = 2.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.retries = retries
        self.backoff_seconds = backoff_seconds

    def send(self, message: EmailMessage) -> None:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.retries + 1):
            try:
                context = create_default_context()
                with SMTP(self.host, self.port, timeout=30) as smtp:
                    smtp.starttls(context=context)
                    smtp.login(self.username, self.password)
                    smtp.send_message(message)
                return
            except (OSError, SMTPException) as exc:
                last_error = exc
                logger.warning("SMTP send attempt %s/%s failed: %s", attempt, self.retries, exc)
                if attempt < self.retries:
                    time.sleep(self.backoff_seconds * attempt)
        raise RuntimeError(f"Failed to send email after {self.retries} attempts: {last_error}")


class Notifier:
    def __init__(self, settings: config.Settings, transport: Optional[EmailTransport] = None) -> None:
        self.settings = settings
        if transport is None:
            transport = SMTPTransport(
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=settings.smtp_pass,
            )
        self.transport = transport

    def notify(self, assignments: Iterable[Assignment]) -> NotificationResult:
        sent: List[str] = []
        skipped: List[str] = []
        for assignment in assignments:
            message = self._build_message(assignment)
            if self.settings.run.dry_run:
                logger.info("[dry-run] Would send assignment %s", assignment.fingerprint())
                skipped.append(assignment.fingerprint())
                continue
            self.transport.send(message)
            sent.append(assignment.fingerprint())
        return NotificationResult(sent=sent, skipped=skipped)

    def _build_message(self, assignment: Assignment) -> EmailMessage:
        subject = self.settings.email.subject_template.format(
            course_alias=assignment.course_alias,
            title=assignment.title,
        )
        body_lines = [
            assignment.title,
            f"Course: {assignment.course_alias}",
        ]
        if assignment.due_at:
            body_lines.append(f"Due: {assignment.due_at}")
        if assignment.unlock_at:
            body_lines.append(f"Opens: {assignment.unlock_at}")
        if assignment.lock_at:
            body_lines.append(f"Closes: {assignment.lock_at}")
        if assignment.points_possible is not None:
            body_lines.append(f"Points: {assignment.points_possible:g}")
        if assignment.submission_types:
            body_lines.append(f"Submission: {', '.join(assignment.submission_types)}")
        if assignment.html_url:
            body_lines.append(f"Link: {assignment.html_url}")

        description = self._trim_description(assignment.description)
        if description:
            body_lines.append("")
            body_lines.append(description)

        body = "\n".join(body_lines).strip()

        message = EmailMessage()
        message["From"] = f"{self.settings.email.from_name} <{self.settings.smtp_user}>"
        message["To"] = self.settings.things_email
        message["Subject"] = subject
        message.set_content(body)
        return message

    def _trim_description(self, description: Optional[str]) -> Optional[str]:
        if not description or not self.settings.email.include_description:
            return None
        summary = textwrap.dedent(description).strip()
        limit = self.settings.email.max_description_chars
        if limit <= 0 or len(summary) <= limit:
            return summary
        return summary[: limit - 1] + "…"
