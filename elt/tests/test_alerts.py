"""Unit tests for dxb.alerts (SMTP send is fully mocked)."""

from __future__ import annotations

from conftest import make_settings

from dxb import alerts


def test_send_email_skips_when_no_smtp_host(mocker):
    smtp = mocker.patch("smtplib.SMTP")
    ok = alerts.send_email(
        "subj", "body", make_settings(smtp_host="", alert_to="a@b.c")
    )
    assert ok is False
    smtp.assert_not_called()


def test_send_email_skips_when_no_recipient(mocker):
    smtp = mocker.patch("smtplib.SMTP")
    ok = alerts.send_email(
        "subj", "body", make_settings(smtp_host="mail.test", alert_to="")
    )
    assert ok is False
    smtp.assert_not_called()


def test_send_email_sends_with_starttls_and_login(mocker):
    smtp_cls = mocker.patch("smtplib.SMTP")
    smtp = smtp_cls.return_value.__enter__.return_value
    settings = make_settings(
        smtp_host="mail.test",
        smtp_port=587,
        smtp_user="u",
        smtp_password="pw",
        smtp_starttls=True,
        alert_to="to@x.com",
        alert_from="from@x.com",
    )

    ok = alerts.send_email("subj", "body", settings)

    assert ok is True
    smtp_cls.assert_called_once_with("mail.test", 587, timeout=30)
    smtp.starttls.assert_called_once()
    smtp.login.assert_called_once_with("u", "pw")
    smtp.send_message.assert_called_once()
    msg = smtp.send_message.call_args[0][0]
    assert msg["Subject"] == "subj"
    assert msg["To"] == "to@x.com" and msg["From"] == "from@x.com"


def test_send_email_no_starttls_no_login_when_unconfigured(mocker):
    smtp_cls = mocker.patch("smtplib.SMTP")
    smtp = smtp_cls.return_value.__enter__.return_value
    settings = make_settings(
        smtp_host="mail.test", smtp_user="", smtp_starttls=False, alert_to="to@x.com"
    )

    ok = alerts.send_email("s", "b", settings)

    assert ok is True
    smtp.starttls.assert_not_called()
    smtp.login.assert_not_called()
    smtp.send_message.assert_called_once()


def test_send_email_swallows_exceptions(mocker):
    mocker.patch("smtplib.SMTP", side_effect=OSError("connection refused"))
    ok = alerts.send_email(
        "s", "b", make_settings(smtp_host="mail.test", alert_to="a@b.c")
    )
    assert ok is False  # no raise


def test_send_email_swallows_send_message_error(mocker):
    smtp_cls = mocker.patch("smtplib.SMTP")
    smtp_cls.return_value.__enter__.return_value.send_message.side_effect = (
        RuntimeError("nope")
    )
    ok = alerts.send_email(
        "s", "b", make_settings(smtp_host="mail.test", alert_to="a@b.c")
    )
    assert ok is False


def test_notify_success_subject_and_body(mocker):
    sent = mocker.patch("dxb.alerts.send_email", return_value=True)
    settings = make_settings()
    assert alerts.notify_success({"ok": 1}, attempts=3, settings=settings) is True

    subject, body = sent.call_args[0][0], sent.call_args[0][1]
    assert subject == "[dxb] daily run OK"
    assert "attempt 3" in body
    assert sent.call_args[0][2] is settings


def test_notify_failure_subject_and_body(mocker):
    sent = mocker.patch("dxb.alerts.send_email", return_value=False)
    assert alerts.notify_failure("Traceback: boom", attempts=2) is False

    subject, body = sent.call_args[0][0], sent.call_args[0][1]
    assert subject == "[dxb] daily run FAILED"
    assert "after 2 attempt" in body
    assert "boom" in body
