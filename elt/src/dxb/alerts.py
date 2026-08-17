"""SMTP alerting: exactly one mail per daily run (success OR failure-after-retries).

Send failures are logged and swallowed — alerting must never take down the
pipeline; etl_run is the durable audit trail either way.
"""

from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage

from dxb.config import Settings, get_settings

log = logging.getLogger(__name__)


def send_email(subject: str, body: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    if not settings.smtp_host or not settings.alert_to:
        log.info("SMTP not configured — alert skipped: %s", subject)
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.alert_from
    msg["To"] = settings.alert_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        log.info("alert sent: %s", subject)
        return True
    except Exception:
        log.exception("failed to send alert %r — pipeline continues", subject)
        return False


def notify_success(
    report: dict, attempts: int, settings: Settings | None = None
) -> bool:
    body = (
        f"Daily dxb pipeline finished successfully (attempt {attempts}).\n\n"
        f"Run report:\n{json.dumps(report, indent=2, default=str)}\n"
    )
    return send_email("[dxb] daily run OK", body, settings)


def notify_failure(error: str, attempts: int, settings: Settings | None = None) -> bool:
    body = (
        f"Daily dxb pipeline FAILED after {attempts} attempt(s).\n\n"
        f"Last error / traceback:\n{error}\n\n"
        "The scheduler is still running; the next daily run will fire as scheduled.\n"
        "Check the etl_run table for the full history."
    )
    return send_email("[dxb] daily run FAILED", body, settings)
