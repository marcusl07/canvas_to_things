"""Email notifications via Mail to Things.

Formats assignments into plain-text emails and delivers them through SMTP to
`@things.email` inboxes. Supports dry-runs for local testing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from smtplib import SMTP, SMTPException
from ssl import create_default_context
from typing import Callable, Iterable, List, Optional, Protocol

import logging
import pytz
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
    failed: List[Assignment]


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
            smtp = None
            try:
                logger.debug("SMTP send attempt %s/%s: connecting to %s:%s", attempt, self.retries, self.host, self.port)
                context = create_default_context()
                smtp = SMTP(self.host, self.port, timeout=30)
                logger.debug("SMTP connection established, starting TLS")
                smtp.starttls(context=context)
                logger.debug("TLS established, logging in as %s", self.username)
                smtp.login(self.username, self.password)
                logger.debug("Login successful, sending message")
                smtp.send_message(message)
                logger.debug("Message sent successfully")
                smtp.quit()
                return
            except (OSError, SMTPException) as exc:
                last_error = exc
                if smtp:
                    try:
                        smtp.quit()
                    except Exception:
                        pass
                logger.warning("SMTP send attempt %s/%s failed: %s", attempt, self.retries, exc)
                if attempt < self.retries:
                    time.sleep(self.backoff_seconds * attempt)
            finally:
                if smtp:
                    try:
                        smtp.close()
                    except Exception:
                        pass
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

    def notify(
        self,
        assignments: Iterable[Assignment],
        can_send: Callable[[], bool] | None = None,
        on_sent: Callable[[], None] | None = None,
    ) -> NotificationResult:
        sent: List[str] = []
        skipped: List[str] = []
        failed: List[Assignment] = []
        assignment_list = list(assignments)
        total = len(assignment_list)
        for idx, assignment in enumerate(assignment_list, 1):
            # Check limit before sending (if callback provided)
            if can_send is not None and not can_send():
                logger.warning("Mail to Things limit reached. Storing remaining %s assignments in pending.", total - idx + 1)
                # Store remaining assignments as failed (they'll be added to pending by caller)
                for remaining in assignment_list[idx - 1:]:
                    failed.append(remaining)
                break
            
            message = self._build_message(assignment)
            if self.settings.run.dry_run:
                logger.info("[dry-run] Would send assignment %s (%s/%s): %s", assignment.fingerprint(), idx, total, assignment.title)
                skipped.append(assignment.fingerprint())
                continue
            logger.info("Sending assignment %s (%s/%s): %s", assignment.fingerprint(), idx, total, assignment.title)
            try:
                self.transport.send(message)
                sent.append(assignment.fingerprint())
                # Call callback to track email count
                if on_sent is not None:
                    on_sent()
            except RuntimeError as exc:
                error_msg = str(exc).lower()
                # Detect rate limiting: connection closed after retries
                if "connection unexpectedly closed" in error_msg or "failed to send email after" in error_msg:
                    logger.warning("Rate limited or connection error for assignment %s: %s", assignment.fingerprint(), exc)
                    failed.append(assignment)
                else:
                    # Other errors - still add to failed but log differently
                    logger.error("Failed to send assignment %s: %s", assignment.fingerprint(), exc)
                    failed.append(assignment)
            # Delay between emails to avoid rate limiting and spam filters (except for last one)
            if idx < total:
                time.sleep(2.0)  # Increased to 2s to reduce spam filter triggers
        return NotificationResult(sent=sent, skipped=skipped, failed=failed)

    def _format_datetime(self, iso_string: str) -> str:
        """Format an ISO 8601 datetime string to show both UTC (Zulu) and local time."""
        try:
            # Parse the ISO 8601 string (e.g., "2026-01-15T23:59:59Z")
            dt_utc = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            if dt_utc.tzinfo is None:
                dt_utc = pytz.UTC.localize(dt_utc)
            
            # Convert to local timezone
            tz = pytz.timezone(self.settings.run.timezone)
            dt_local = dt_utc.astimezone(tz)
            
            # Format both times
            utc_str = dt_utc.strftime("%Y-%m-%d %H:%M:%S UTC")
            local_str = dt_local.strftime("%Y-%m-%d %H:%M:%S %Z")
            
            return f"{utc_str} ({local_str})"
        except (ValueError, AttributeError) as exc:
            logger.warning("Failed to parse datetime '%s': %s (using raw value)", iso_string, exc)
            return iso_string

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
            formatted_due = self._format_datetime(assignment.due_at)
            body_lines.append(f"Due: {formatted_due}")
        if assignment.unlock_at:
            formatted_unlock = self._format_datetime(assignment.unlock_at)
            body_lines.append(f"Opens: {formatted_unlock}")
        if assignment.lock_at:
            formatted_lock = self._format_datetime(assignment.lock_at)
            body_lines.append(f"Closes: {formatted_lock}")
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
        # Use simpler From format - just the email, no display name (can trigger spam filters)
        message["From"] = self.settings.smtp_user
        message["To"] = self.settings.things_email
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        message["Message-ID"] = make_msgid(domain="canvas-things")
        # Add User-Agent to look more legitimate
        message["User-Agent"] = "Canvas-Things-Bridge/1.0"
        message.set_content(body, charset="utf-8")
        return message

    def _trim_description(self, description: Optional[str]) -> Optional[str]:
        if not description or not self.settings.email.include_description:
            return None
        summary = textwrap.dedent(description).strip()
        limit = self.settings.email.max_description_chars
        if limit <= 0 or len(summary) <= limit:
            return summary
        return summary[: limit - 1] + "…"
