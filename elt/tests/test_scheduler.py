"""Unit tests for dxb.scheduler (job registration + cron wiring)."""

from __future__ import annotations

from dxb import scheduler


def _cron_field(trigger, name: str) -> str:
    for field in trigger.fields:
        if field.name == name:
            return str(field)
    raise AssertionError(f"no cron field {name!r}")


def test_build_scheduler_registers_daily_job(monkeypatch):
    monkeypatch.setenv("DXB_SCHEDULE_HOUR", "7")
    monkeypatch.setenv("DXB_SCHEDULE_MINUTE", "15")

    sched = scheduler.build_scheduler()
    job = sched.get_job("daily_pipeline")

    assert job is not None
    assert job.id == "daily_pipeline"
    assert _cron_field(job.trigger, "hour") == "7"
    assert _cron_field(job.trigger, "minute") == "15"


def test_build_scheduler_uses_default_schedule(monkeypatch):
    monkeypatch.delenv("DXB_SCHEDULE_HOUR", raising=False)
    monkeypatch.delenv("DXB_SCHEDULE_MINUTE", raising=False)

    sched = scheduler.build_scheduler()
    job = sched.get_job("daily_pipeline")

    assert _cron_field(job.trigger, "hour") == "2"
    assert _cron_field(job.trigger, "minute") == "30"


def test_build_scheduler_job_func_is_daily_job(monkeypatch):
    sched = scheduler.build_scheduler()
    job = sched.get_job("daily_pipeline")
    assert job.func is scheduler.daily_job


def test_daily_job_calls_run_with_retries(mocker):
    run = mocker.patch("dxb.scheduler.run_with_retries")
    scheduler.daily_job()
    run.assert_called_once_with(kind="daily")
