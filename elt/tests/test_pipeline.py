"""Unit tests for dxb.pipeline.run_with_retries (job/retry tier).

run_pipeline itself is patched out; get_session is replaced by an in-memory
fake, and tenacity waits are zeroed via env so tests are instant.
"""

from __future__ import annotations

import threading

import pytest

from dxb import pipeline


@pytest.fixture(autouse=True)
def instant_retries(monkeypatch):
    monkeypatch.setenv("DXB_JOB_WAIT_MIN_SECONDS", "0")
    monkeypatch.setenv("DXB_JOB_WAIT_MAX_SECONDS", "0")


def test_succeeds_after_two_failures(monkeypatch, mocker, fake_session_store):
    monkeypatch.setenv("DXB_JOB_ATTEMPTS", "3")
    mocker.patch("dxb.pipeline.get_session", fake_session_store)
    report = {"kind": "daily", "ok": True}
    run_pipeline = mocker.patch(
        "dxb.pipeline.run_pipeline",
        side_effect=[RuntimeError("net down"), RuntimeError("net down"), report],
    )
    notify_success = mocker.patch("dxb.pipeline.alerts.notify_success")
    notify_failure = mocker.patch("dxb.pipeline.alerts.notify_failure")

    result = pipeline.run_with_retries("daily")

    assert result == report
    assert run_pipeline.call_count == 3
    notify_failure.assert_not_called()
    notify_success.assert_called_once()
    assert notify_success.call_args[0][0] == report  # report
    assert notify_success.call_args[0][1] == 3  # attempts

    run = fake_session_store.store[1]
    assert run.status == "ok"
    assert run.attempts == 3
    assert run.report == report
    assert run.finished_at is not None


def test_fails_after_exhausting_attempts(monkeypatch, mocker, fake_session_store):
    monkeypatch.setenv("DXB_JOB_ATTEMPTS", "2")
    mocker.patch("dxb.pipeline.get_session", fake_session_store)
    run_pipeline = mocker.patch(
        "dxb.pipeline.run_pipeline", side_effect=RuntimeError("boom")
    )
    notify_success = mocker.patch("dxb.pipeline.alerts.notify_success")
    notify_failure = mocker.patch("dxb.pipeline.alerts.notify_failure")

    result = pipeline.run_with_retries("daily")

    assert result is None
    assert run_pipeline.call_count == 2
    notify_success.assert_not_called()
    notify_failure.assert_called_once()
    error_text, attempts = (
        notify_failure.call_args[0][0],
        notify_failure.call_args[0][1],
    )
    assert attempts == 2
    assert "boom" in error_text

    run = fake_session_store.store[1]
    assert run.status == "failed"
    assert run.attempts == 2
    assert "boom" in run.error
    assert run.finished_at is not None


def test_single_attempt_success(monkeypatch, mocker, fake_session_store):
    monkeypatch.setenv("DXB_JOB_ATTEMPTS", "3")
    mocker.patch("dxb.pipeline.get_session", fake_session_store)
    report = {"ok": 1}
    run_pipeline = mocker.patch("dxb.pipeline.run_pipeline", return_value=report)
    notify_success = mocker.patch("dxb.pipeline.alerts.notify_success")
    mocker.patch("dxb.pipeline.alerts.notify_failure")

    result = pipeline.run_with_retries("daily")

    assert result == report
    assert run_pipeline.call_count == 1
    assert notify_success.call_args[0][1] == 1  # attempts=1
    assert fake_session_store.store[1].status == "ok"


# --------------------------------------------------------- cancellation


def test_run_cancelled_marks_status_cancelled_not_failed(
    monkeypatch, mocker, fake_session_store
):
    """RunCancelled (raised by the SIGTERM handler mid-run) must not be
    treated as an ordinary failure: no retry, no failure alert, status
    'cancelled' rather than 'failed'."""
    monkeypatch.setenv("DXB_JOB_ATTEMPTS", "3")
    mocker.patch("dxb.pipeline.get_session", fake_session_store)
    run_pipeline = mocker.patch(
        "dxb.pipeline.run_pipeline",
        side_effect=pipeline.RunCancelled("received signal 15"),
    )
    notify_success = mocker.patch("dxb.pipeline.alerts.notify_success")
    notify_failure = mocker.patch("dxb.pipeline.alerts.notify_failure")

    result = pipeline.run_with_retries("backfill")

    assert result is None
    assert run_pipeline.call_count == 1  # not retried
    notify_success.assert_not_called()
    notify_failure.assert_not_called()

    run = fake_session_store.store[1]
    assert run.status == "cancelled"
    assert run.attempts == 1
    assert "received signal 15" in run.error
    assert run.finished_at is not None


def test_keyboard_interrupt_also_marks_cancelled(
    monkeypatch, mocker, fake_session_store
):
    monkeypatch.setenv("DXB_JOB_ATTEMPTS", "3")
    mocker.patch("dxb.pipeline.get_session", fake_session_store)
    run_pipeline = mocker.patch(
        "dxb.pipeline.run_pipeline", side_effect=KeyboardInterrupt
    )

    result = pipeline.run_with_retries("backfill")

    assert result is None
    assert run_pipeline.call_count == 1
    assert fake_session_store.store[1].status == "cancelled"


def test_signal_handler_skipped_off_main_thread(
    monkeypatch, mocker, fake_session_store
):
    """APScheduler runs daily jobs in a worker thread, where Python forbids
    installing a signal handler (ValueError: signal only works in main
    thread). run_with_retries must detect this and skip installation rather
    than crash."""
    monkeypatch.setenv("DXB_JOB_ATTEMPTS", "1")
    mocker.patch("dxb.pipeline.get_session", fake_session_store)
    mocker.patch("dxb.pipeline.run_pipeline", return_value={"ok": 1})
    mocker.patch("dxb.pipeline.alerts.notify_success")

    outcome = {}

    def worker():
        try:
            outcome["result"] = pipeline.run_with_retries("daily")
        except Exception as e:  # pragma: no cover - failure path only
            outcome["error"] = e

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=5)

    assert "error" not in outcome
    assert outcome["result"] == {"ok": 1}
