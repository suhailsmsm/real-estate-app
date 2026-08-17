"""Pipeline: collect -> transform -> marts, with job-level retries + alerting.

Two-tier retries (plan §3): DldClient retries individual HTTP calls; this
module retries the WHOLE run via tenacity (APScheduler has no retries of its
own). Every attempt is idempotent: staging is hash-deduped and facts upsert on
natural keys, so a crashed attempt simply continues on the next one.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
from datetime import date, datetime, timezone

from dxb_core.models import EtlRun
from tenacity import (
    RetryError,
    Retrying,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from dxb import alerts
from dxb.area_codes import detect_area_code_splits_safe
from dxb.collectors.client import DldClient
from dxb.collectors.dld import (
    collect_areas,
    collect_projects,
    collect_windowed,
    default_window,
)
from dxb.config import get_settings
from dxb.db.engine import get_session, source_id
from dxb.geo.buildings import enrich_missing_building_geo
from dxb.marts import rebuild_marts
from dxb.osm_geo.enrich import enrich_missing_areas
from dxb.osm_geo.projects import enrich_missing_project_geo
from dxb.transform.dld import transform_all

log = logging.getLogger(__name__)


class RunCancelled(Exception):
    """Raised when the process receives SIGTERM mid-run (e.g. `docker stop`
    on a one-off backfill container), so the run gets marked 'cancelled'
    instead of sitting at 'running' forever."""


def run_pipeline(
    kind: str = "daily",
    date_from: date | None = None,
    date_to: date | None = None,
    with_projects: bool = True,
) -> dict:
    """One full pipeline attempt. Windows default to [watermark - 2d, today]."""
    settings = get_settings()
    started = time.monotonic()
    report: dict = {"kind": kind, "collect": {}, "transform": {}, "marts": {}}

    with get_session() as session:
        sid = source_id(session)
        with DldClient(
            page_size=settings.page_size,
            throttle_seconds=settings.throttle_seconds,
            max_concurrency=settings.max_concurrency,
        ) as client:
            report["collect"]["areas"] = collect_areas(session, client, sid)
            if with_projects:
                report["collect"]["projects"] = collect_projects(session, client, sid)
            for endpoint in ("transactions", "rents"):
                d_from, d_to = (
                    (date_from, date_to)
                    if date_from and date_to
                    else default_window(session, sid, endpoint)
                )
                report["collect"][endpoint] = collect_windowed(
                    session, client, sid, endpoint, d_from, d_to
                )
        report["transform"] = transform_all(session, settings.source_url)
        # Non-fatal, like the geo hooks below: finds candidate area-code
        # splits and refreshes area_code_evidence + project_area_actual only
        # — never touches dim_project, dim_building, or dim_area itself, so
        # it can never change what rebuild_marts aggregates. Run right before
        # the mart rebuild (build order per docs/AREA_CODE_MIGRATION_ANALYSIS.md)
        # so same-day detections are available for review before the next
        # rebuild if desired.
        report["area_code_splits"] = detect_area_code_splits_safe(session)
        report["marts"] = rebuild_marts(session)
        # Non-fatal: a newly-seen area name can create an un-enriched
        # dim_area stub on any run; never lets a geocoding hiccup fail or
        # retry today's actual data collection.
        report["geo_enrich"] = enrich_missing_areas(session)
        # Precise tier: Makani-geocode newly-seen buildings, then roll their
        # points up into project locations (geometric median). Incremental —
        # only new buildings, a few throttled calls per run.
        report["building_geo"] = enrich_missing_building_geo(session)
        # Coarse fallback last: fills the area centroid only for projects that
        # buildings did not already place (backfill_area_centroids fills NULL
        # locations only, so it never downgrades a building-derived point).
        report["project_geo"] = enrich_missing_project_geo(session)

    report["duration_seconds"] = round(time.monotonic() - started, 1)
    return report


def run_with_retries(
    kind: str = "daily",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict | None:
    """Job tier: tenacity around the whole run; one etl_run row + one alert
    per invocation, whatever the outcome."""
    settings = get_settings()

    with get_session() as session:
        run = EtlRun(
            kind=kind,
            started_at=datetime.now(timezone.utc),
            status="running",
            attempts=0,
        )
        session.add(run)
        session.commit()
        run_id = run.id

    # SIGTERM -> RunCancelled, so `docker stop` on a one-off backfill
    # container marks the run 'cancelled' instead of leaving it stuck at
    # 'running' forever. Python only allows installing signal handlers from
    # the main thread; scheduled daily runs execute inside an APScheduler
    # worker thread, so this is skipped there (a plain `Exception` traceback
    # is still preferable to a crash — those runs just won't get the nicer
    # 'cancelled' status if their container is killed mid-flight).
    on_main_thread = threading.current_thread() is threading.main_thread()
    previous_handler = None
    if on_main_thread:

        def _on_sigterm(signum, _frame):
            raise RunCancelled(f"received signal {signum}")

        previous_handler = signal.signal(signal.SIGTERM, _on_sigterm)

    attempts = 0
    try:
        try:
            for attempt in Retrying(
                stop=stop_after_attempt(settings.job_attempts),
                wait=wait_exponential(
                    multiplier=1,
                    min=settings.job_wait_min_seconds,
                    max=settings.job_wait_max_seconds,
                ),
                retry=retry_if_not_exception_type((RunCancelled, KeyboardInterrupt)),
                reraise=False,
            ):
                with attempt:
                    attempts = attempt.retry_state.attempt_number
                    log.info(
                        "pipeline %s attempt %s/%s",
                        kind,
                        attempts,
                        settings.job_attempts,
                    )
                    report = run_pipeline(kind, date_from, date_to)
        except RetryError as retry_err:
            error_text = "".join(
                traceback.format_exception(retry_err.last_attempt.exception())
            )
            log.error("pipeline failed after %s attempts:\n%s", attempts, error_text)
            with get_session() as session:
                run = session.get(EtlRun, run_id)
                run.status = "failed"
                run.attempts = attempts
                run.finished_at = datetime.now(timezone.utc)
                run.error = error_text[-8000:]
                session.commit()
            alerts.notify_failure(error_text[-8000:], attempts, settings)
            return None
        except (RunCancelled, KeyboardInterrupt) as cancel_err:
            log.warning(
                "pipeline %s cancelled on attempt %s: %s", kind, attempts, cancel_err
            )
            with get_session() as session:
                run = session.get(EtlRun, run_id)
                run.status = "cancelled"
                run.attempts = attempts
                run.finished_at = datetime.now(timezone.utc)
                run.error = f"cancelled: {cancel_err}"
                session.commit()
            return None
    finally:
        if on_main_thread:
            signal.signal(signal.SIGTERM, previous_handler)

    with get_session() as session:
        run = session.get(EtlRun, run_id)
        run.status = "ok"
        run.attempts = attempts
        run.finished_at = datetime.now(timezone.utc)
        run.report = report
        session.commit()
    alerts.notify_success(report, attempts, settings)
    return report
