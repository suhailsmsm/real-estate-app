"""APScheduler entrypoint: fires the retry-wrapped daily pipeline.

Retries/alerts live in pipeline.run_with_retries (APScheduler itself has
neither); a crashed job never kills the scheduler process.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from dxb.config import get_settings
from dxb.pipeline import run_with_retries

log = logging.getLogger(__name__)


def daily_job() -> None:
    run_with_retries(kind="daily")


def build_scheduler() -> BlockingScheduler:
    settings = get_settings()
    scheduler = BlockingScheduler()
    scheduler.add_job(
        daily_job,
        CronTrigger(hour=settings.schedule_hour, minute=settings.schedule_minute),
        id="daily_pipeline",
        max_instances=1,
        coalesce=True,  # missed runs (host asleep) collapse into one
        misfire_grace_time=3600,
    )
    return scheduler


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    settings = get_settings()
    scheduler = build_scheduler()
    log.info(
        "scheduler started; daily pipeline at %02d:%02d (%s)",
        settings.schedule_hour,
        settings.schedule_minute,
        "container TZ",
    )
    scheduler.start()
