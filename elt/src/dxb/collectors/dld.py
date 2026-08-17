"""Collectors: DLD gateway endpoints -> stg_raw (append-only, hash-deduped).

Windows are calendar months; the watermark advances after each completed
window, so an interrupted backfill resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone

from dxb_core.models import EtlWatermark, StgRaw
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dxb.collectors.client import DldClient

log = logging.getLogger(__name__)

# Query metadata that varies between fetches of the same record — must not
# participate in content hashing (plan §2.1 caveat).
VOLATILE_FIELDS = {
    "RN",
    "TOTAL",
    "TOTAL_SELLER",
    "TOTAL_BUYER",
    "TOTAL_PROPERTIES",
    "DEFAULT_SORT",
}

PROJECT_STATUSES = [
    "ACTIVE",
    "FINISHED",
    "FRIEZED",
    "CANCELLED",
    "UNDER_REVIEWING",
    "UNDER_CANCELATION_DECISION",
    "UNDER_CANCELATION_NOTIFICATION",
    "CONDITIONAL_ACTIVATING",
]


def fmt_date(d: date) -> str:
    """Gateway expects MM/DD/YYYY (verified; DD/MM silently 500s)."""
    return d.strftime("%m/%d/%Y")


def clean_row(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in VOLATILE_FIELDS}


def record_hash(endpoint: str, cleaned: dict) -> str:
    canonical = json.dumps(
        cleaned, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(f"{endpoint}|{canonical}".encode()).hexdigest()


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    """Inclusive [start, end] split on calendar-month boundaries."""
    if start > end:
        return []
    windows = []
    cur = start
    while cur <= end:
        if cur.month == 12:
            month_end = date(cur.year, 12, 31)
        else:
            month_end = date(cur.year, cur.month + 1, 1) - timedelta(days=1)
        windows.append((cur, min(month_end, end)))
        cur = month_end + timedelta(days=1)
    return windows


def stage_rows(
    session: Session,
    source_id: int,
    endpoint: str,
    request_payload: dict,
    rows: list[dict],
) -> int:
    """Insert cleaned rows into staging; hash conflict = identical content = skip.
    Returns the number of newly staged rows."""
    seen: dict[str, dict] = {}
    for row in rows:
        cleaned = clean_row(row)
        seen[record_hash(endpoint, cleaned)] = cleaned
    if not seen:
        return 0
    values = [
        {
            "source_id": source_id,
            "endpoint": endpoint,
            "request_json": request_payload,
            "payload_json": cleaned,
            "record_hash": h,
        }
        for h, cleaned in seen.items()
    ]
    stmt = (
        pg_insert(StgRaw.__table__)
        .values(values)
        .on_conflict_do_nothing(index_elements=["record_hash"])
        .returning(
            StgRaw.__table__.c.id
        )  # rowcount is unreliable for multi-VALUES inserts
    )
    result = session.execute(stmt)
    return len(result.fetchall())


def get_watermark(session: Session, source_id: int, endpoint: str) -> date | None:
    wm = session.get(EtlWatermark, (source_id, endpoint))
    return wm.last_date if wm else None


def set_watermark(
    session: Session, source_id: int, endpoint: str, last_date: date
) -> None:
    wm = session.get(EtlWatermark, (source_id, endpoint))
    if wm is None:
        session.add(
            EtlWatermark(source_id=source_id, endpoint=endpoint, last_date=last_date)
        )
    elif wm.last_date < last_date:
        wm.last_date = last_date
        wm.updated_at = datetime.now(timezone.utc)


def _transactions_payload(d_from: date, d_to: date) -> dict:
    return {
        "P_FROM_DATE": fmt_date(d_from),
        "P_TO_DATE": fmt_date(d_to),
        "P_GROUP_ID": "",
        "P_IS_OFFPLAN": "",
        "P_IS_FREE_HOLD": "",
        "P_AREA_ID": "",
        "P_USAGE_ID": "",
        "P_PROP_TYPE_ID": "",
        "P_SORT": "TRANSACTION_NUMBER_ASC",
    }


def _rents_payload(d_from: date, d_to: date) -> dict:
    return {
        "P_DATE_TYPE": "0",  # registration date
        "P_FROM_DATE": fmt_date(d_from),
        "P_TO_DATE": fmt_date(d_to),
        "P_IS_FREE_HOLD": "",
        "P_VERSION": "",
        "P_AREA_ID": "",
        "P_USAGE_ID": "",
        "P_PROP_TYPE_ID": "",
        "P_SORT": "REGISTRATION_DATE_DESC",
    }


_WINDOWED_PAYLOADS = {
    "transactions": _transactions_payload,
    "rents": _rents_payload,
}


def collect_windowed(
    session: Session,
    client: DldClient,
    source_id: int,
    endpoint: str,
    d_from: date,
    d_to: date,
) -> dict:
    """Collect a windowed endpoint (transactions / rents) month by month.
    Commits and advances the watermark after each window — resumable."""
    payload_fn = _WINDOWED_PAYLOADS[endpoint]
    staged = fetched = 0
    for w_from, w_to in month_windows(d_from, d_to):
        for request_payload, rows in client.pages(endpoint, payload_fn(w_from, w_to)):
            fetched += len(rows)
            staged += stage_rows(session, source_id, endpoint, request_payload, rows)
            session.commit()
        set_watermark(session, source_id, endpoint, w_to)
        session.commit()
        log.info(
            "%s %s..%s: fetched=%s staged(new)=%s",
            endpoint,
            w_from,
            w_to,
            fetched,
            staged,
        )
    return {
        "endpoint": endpoint,
        "fetched": fetched,
        "staged": staged,
        "from": str(d_from),
        "to": str(d_to),
    }


def collect_areas(session: Session, client: DldClient, source_id: int) -> dict:
    rows = client.post("carea-lookup", {})
    staged = stage_rows(session, source_id, "carea-lookup", {}, rows)
    session.commit()
    return {"endpoint": "carea-lookup", "fetched": len(rows), "staged": staged}


def collect_projects(session: Session, client: DldClient, source_id: int) -> dict:
    """Full refresh of the projects register (small; ~thousands of rows).
    Empty P_PRJ_STATUS returned 0 rows in probing — iterate known statuses."""
    d_from, d_to = date(1980, 1, 1), date.today()
    staged = fetched = 0
    for status in PROJECT_STATUSES:
        payload = {
            "P_FROM_DATE": fmt_date(d_from),
            "P_TO_DATE": fmt_date(d_to),
            "P_DATE_TYPE": "1",  # start date
            "P_PRJ_TYPE_ID": "",
            "P_PRJ_STATUS": status,
            "P_ZONE_ID": "",
            "P_AREA_ID": "",
            "P_SORT": "",
        }
        for request_payload, rows in client.pages("projects", payload):
            fetched += len(rows)
            staged += stage_rows(session, source_id, "projects", request_payload, rows)
            session.commit()
    return {"endpoint": "projects", "fetched": fetched, "staged": staged}


def default_window(
    session: Session, source_id: int, endpoint: str, overlap_days: int = 2
) -> tuple[date, date]:
    """Daily-run window: [watermark - overlap, today]; first run = last 30 days."""
    today = date.today()
    wm = get_watermark(session, source_id, endpoint)
    start = (wm - timedelta(days=overlap_days)) if wm else today - timedelta(days=30)
    return min(start, today), today
