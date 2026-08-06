"""
Email alerts for scheduled sync failures.

Alerting on every failed run is useless: a 30-minute schedule that breaks and
stays broken would produce 48 identical messages a day. This module sends on the
*transition* into failure, repeats at most once per `reminder_interval_hours`
while the problem persists, and sends one message when the sync recovers.

Sending never raises into the caller - a mail server being down must not turn a
partially successful sync into a failed one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import smtplib
import ssl
from dataclasses import asdict, dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate

from icloudbridge.core.config import NotificationsConfig
from icloudbridge.utils.settings_db import get_settings_db

logger = logging.getLogger(__name__)

SETTINGS_KEY = "notification_failure_state"
SMTP_TIMEOUT_SECONDS = 30


@dataclass
class FailureState:
    """What we know about an alert target that is currently failing."""

    first_failed_at: float
    last_alert_at: float
    failure_count: int = 1
    last_error: str = ""


@dataclass
class NotificationState:
    """Per-target failure state, keyed by "<schedule_id>:<service>"."""

    targets: dict[str, FailureState] = field(default_factory=dict)

    @classmethod
    def load(cls) -> NotificationState:
        raw = get_settings_db().get(SETTINGS_KEY)
        if not raw:
            return cls()
        try:
            parsed = json.loads(raw)
            return cls(
                targets={
                    key: FailureState(**value)
                    for key, value in (parsed.get("targets") or {}).items()
                }
            )
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(f"Discarding unreadable notification state: {e}")
            return cls()

    def save(self) -> None:
        payload = {"targets": {key: asdict(value) for key, value in self.targets.items()}}
        get_settings_db().set(SETTINGS_KEY, json.dumps(payload))


def _target_key(schedule_id: int, service: str) -> str:
    return f"{schedule_id}:{service}"


def _send_message(config: NotificationsConfig, subject: str, body: str) -> None:
    """Deliver one message over SMTP. Blocking; call from a worker thread."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.from_address
    message["To"] = ", ".join(config.to_addresses)
    message["Date"] = formatdate(localtime=True)
    message.set_content(body)

    password = config.get_smtp_password()

    if config.smtp_use_ssl:
        context = ssl.create_default_context()
        server = smtplib.SMTP_SSL(
            config.smtp_host, config.smtp_port, timeout=SMTP_TIMEOUT_SECONDS, context=context
        )
    else:
        server = smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=SMTP_TIMEOUT_SECONDS)

    with server:
        server.ehlo()
        if config.smtp_use_tls and not config.smtp_use_ssl:
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
        if config.smtp_username and password:
            server.login(config.smtp_username, password)
        server.send_message(message)


async def send_email(config: NotificationsConfig, subject: str, body: str) -> None:
    """Send a message off the event loop, raising on failure."""
    await asyncio.to_thread(_send_message, config, subject, body)


def _format_failure_body(
    schedule_name: str,
    service: str,
    failures: list[str],
    units: int,
    total_failure: bool,
    failure_count: int,
    first_failed_at: float,
) -> str:
    scope = (
        f"All {units} folder(s)/calendar(s) failed."
        if total_failure
        else f"{len(failures)} failure(s) across {units} folder(s)/calendar(s)."
    )

    lines = [
        f"Scheduled sync '{schedule_name}' ({service}) reported failures.",
        "",
        scope,
        f"First failed: {datetime.fromtimestamp(first_failed_at):%Y-%m-%d %H:%M:%S}",
        f"Failed runs since: {failure_count}",
        "",
        "Failures:",
    ]
    lines.extend(f"  - {failure}" for failure in failures[:25])
    if len(failures) > 25:
        lines.append(f"  ... and {len(failures) - 25} more")

    # The single most common cause of a total failure is macOS having revoked
    # the backend's permissions after a Python upgrade, so point at it directly.
    if total_failure:
        lines += [
            "",
            "If every unit failed at once, the backend has most likely lost its "
            "macOS permissions - this happens when a Homebrew Python upgrade "
            "replaces the interpreter it is running from. Restarting iCloudBridge "
            "rebuilds the environment and restores access.",
        ]

    lines += ["", "-- iCloudBridge"]
    return "\n".join(lines)


class FailureNotifier:
    """Decides when a failure is worth an email, and sends it."""

    def __init__(self, config: NotificationsConfig):
        self.config = config

    async def notify_failure(
        self,
        *,
        schedule_id: int,
        schedule_name: str,
        service: str,
        failures: list[str],
        units: int,
        total_failure: bool,
    ) -> bool:
        """Record a failed run and alert if this one warrants it.

        Returns True when a message was sent.
        """
        if not self.config.is_deliverable():
            return False
        if not total_failure and not self.config.notify_on_partial_failure:
            return False

        now = datetime.now().timestamp()
        state = NotificationState.load()
        key = _target_key(schedule_id, service)
        existing = state.targets.get(key)

        if existing is None:
            entry = FailureState(
                first_failed_at=now,
                last_alert_at=now,
                failure_count=1,
                last_error=failures[0] if failures else "",
            )
            state.targets[key] = entry
            state.save()
            return await self._deliver_failure(
                schedule_name, service, failures, units, total_failure, entry
            )

        existing.failure_count += 1
        existing.last_error = failures[0] if failures else existing.last_error

        # Already alerted; stay quiet until the repeat interval elapses.
        elapsed_hours = (now - existing.last_alert_at) / 3600
        if elapsed_hours < self.config.reminder_interval_hours:
            state.save()
            logger.debug(
                "Suppressing failure alert for %s (%.1fh since last, interval %dh)",
                key,
                elapsed_hours,
                self.config.reminder_interval_hours,
            )
            return False

        existing.last_alert_at = now
        state.save()
        return await self._deliver_failure(
            schedule_name, service, failures, units, total_failure, existing
        )

    async def notify_recovery(
        self, *, schedule_id: int, schedule_name: str, service: str
    ) -> bool:
        """Clear failure state and, if it was failing, say that it recovered."""
        state = NotificationState.load()
        key = _target_key(schedule_id, service)
        entry = state.targets.pop(key, None)

        if entry is None:
            return False

        state.save()

        if not self.config.is_deliverable() or not self.config.notify_on_recovery:
            return False

        body = "\n".join(
            [
                f"Scheduled sync '{schedule_name}' ({service}) is working again.",
                "",
                f"It had been failing since "
                f"{datetime.fromtimestamp(entry.first_failed_at):%Y-%m-%d %H:%M:%S} "
                f"({entry.failure_count} failed run(s)).",
                f"Last error was: {entry.last_error or 'unknown'}",
                "",
                "-- iCloudBridge",
            ]
        )

        return await self._try_send(f"[iCloudBridge] {schedule_name} recovered", body)

    async def send_test(self) -> None:
        """Send a test message, raising so the caller can report why it failed."""
        body = "\n".join(
            [
                "This is a test message from iCloudBridge.",
                "",
                "Failure alerts for scheduled syncs will be delivered to this address.",
                "",
                "-- iCloudBridge",
            ]
        )
        await send_email(self.config, "[iCloudBridge] Test notification", body)

    async def _deliver_failure(
        self,
        schedule_name: str,
        service: str,
        failures: list[str],
        units: int,
        total_failure: bool,
        entry: FailureState,
    ) -> bool:
        subject = f"[iCloudBridge] {schedule_name} ({service}) sync failed"
        if not total_failure:
            subject = f"[iCloudBridge] {schedule_name} ({service}) sync partly failed"

        body = _format_failure_body(
            schedule_name=schedule_name,
            service=service,
            failures=failures,
            units=units,
            total_failure=total_failure,
            failure_count=entry.failure_count,
            first_failed_at=entry.first_failed_at,
        )
        return await self._try_send(subject, body)

    async def _try_send(self, subject: str, body: str) -> bool:
        try:
            await send_email(self.config, subject, body)
            logger.info("Sent notification: %s", subject)
            return True
        except Exception as e:
            # Never let the mail path affect the sync outcome.
            logger.error("Failed to send notification '%s': %s", subject, e)
            return False
