"""Post-load bookkeeping: the analytic cutover and the gateway collection cursor.

Two different dates, deliberately kept apart (see models.EtlSourceCutover):

  cutover  = max *valid fact date* loaded from data.dubai, on the mart axis
             (txn_date / start_date). Using the max data date rather than the
             export date avoids a hole: the transactions export is stamped
             07-21 but holds data through 07-20, so an export-date cutover
             would exclude 07-21 from data.dubai (no data) *and* from the
             gateway (<= cutover).

  watermark = the export "as of" date, parsed from the filename. That is the
             point the gateway must resume collecting from, on its own
             registration-date axis.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

from dxb_core.models import (
    EtlSourceCutover,
    EtlWatermark,
    FactRentContract,
    FactSaleTransaction,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dxb.datadubai.sources import DATASETS, FACT_DATASETS, files_for
from dxb.db.engine import source_id as resolve_source_id

log = logging.getLogger(__name__)

# transactions_2026-07-21_17-31-33_0001.csv -> 2026-07-21
_EXPORT_DATE = re.compile(r"_(\d{4}-\d{2}-\d{2})_")

# The gateway endpoint each dataset feeds into.
_GATEWAY_ENDPOINT = {"transactions": "transactions", "rents": "rents"}


def export_date_for(key: str) -> date | None:
    """The export 'as of' date, from the part filenames."""
    files = files_for(DATASETS[key])
    dates = []
    for path in files:
        m = _EXPORT_DATE.search(path.name)
        if m:
            dates.append(date.fromisoformat(m.group(1)))
    return max(dates) if dates else None


def _cutover_value(session: Session, key: str, source_id: int) -> date | None:
    """The boundary past which the *gateway* becomes authoritative.

    Each dataset uses the boundary that matches how data.dubai's completeness
    is actually defined:

      transactions - max valid txn_date. The transaction date is effectively
                     the registration date, so the data boundary and the mart
                     axis coincide.

      rents        - the export date. A rent export is a snapshot of everything
                     *registered* by that date, but leases are routinely signed
                     to start weeks or years later, so max(start_date) is NOT
                     the boundary: it lands on today or beyond and would wrongly
                     exclude gateway contracts registered after the export that
                     start sooner. The gateway side is therefore compared on its
                     own registration_date (see marts._precedence).
    """
    if key == "rents":
        return export_date_for("rents")

    today = date.today()
    value = session.scalar(
        select(func.max(FactSaleTransaction.txn_date)).where(
            FactSaleTransaction.source_id == source_id,
            FactSaleTransaction.txn_date
            <= datetime.combine(today, datetime.max.time()),
        )
    )
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def set_cutover(session: Session, key: str) -> dict:
    """Record how far data.dubai is authoritative for this dataset."""
    source_id = resolve_source_id(session, DATASETS[key].source_code)
    cutover_date = _cutover_value(session, key, source_id)
    if cutover_date is None:
        log.warning("no %s facts loaded from data.dubai — cutover not set", key)
        return {"dataset": key, "cutover_date": None}

    row = session.get(EtlSourceCutover, key)
    if row is None:
        session.add(
            EtlSourceCutover(
                dataset=key, source_id=source_id, cutover_date=cutover_date
            )
        )
    else:
        row.source_id = source_id
        row.cutover_date = cutover_date
    session.commit()
    log.info("cutover %s = %s", key, cutover_date)
    return {"dataset": key, "cutover_date": str(cutover_date)}


def set_gateway_watermark(session: Session, key: str) -> dict:
    """Point the gateway's collection cursor at the export date so the next
    daily run picks up exactly where data.dubai stops."""
    export_date = export_date_for(key)
    if export_date is None:
        log.warning("no export date parsed for %s — watermark not set", key)
        return {"endpoint": key, "last_date": None}

    gateway_id = resolve_source_id(session, "dld_gateway")
    endpoint = _GATEWAY_ENDPOINT[key]
    wm = session.get(EtlWatermark, (gateway_id, endpoint))
    if wm is None:
        session.add(
            EtlWatermark(source_id=gateway_id, endpoint=endpoint, last_date=export_date)
        )
    else:
        wm.last_date = export_date
        wm.updated_at = datetime.now(timezone.utc)
    session.commit()
    log.info("gateway watermark %s = %s", endpoint, export_date)
    return {"endpoint": endpoint, "last_date": str(export_date)}


def finalize(session: Session) -> dict:
    """Set the cutover + gateway watermark for each fact dataset after a bulk
    load. The building register is enrichment, not a fact, so it is excluded."""
    return {
        "cutovers": [set_cutover(session, k) for k in FACT_DATASETS],
        "watermarks": [set_gateway_watermark(session, k) for k in FACT_DATASETS],
    }
