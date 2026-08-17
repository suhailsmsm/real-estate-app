"""Streaming bulk import of the data.dubai exports.

Rows go straight from the CSV into the guarded fact upsert in batches —
deliberately bypassing stg_raw. At ~12M rows persistent staging would double
storage (~6-10 GB) for no benefit: the CSV files on disk are the replay
source. This is a one-off historical path, distinct from the daily staged
gateway pipeline.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from dxb_core.models import FactRentContract, FactSaleTransaction
from sqlalchemy.orm import Session

from dxb.datadubai import transform as dd
from dxb.datadubai.sources import DATASETS, files_for
from dxb.db.engine import source_id as resolve_source_id
from dxb.transform.dld import DimCaches, _upsert_facts

log = logging.getLogger(__name__)

csv.field_size_limit(10_000_000)

BATCH = 5000

_SPEC = {
    "transactions": {
        "mapper": dd.sale_values,
        "table": FactSaleTransaction,
        "constraint": "ux_sale_natural",
        "key_cols": dd.SALE_KEY_COLS,
        "update_cols": dd.SALE_UPDATE_COLS,
    },
    "rents": {
        "mapper": dd.rent_values,
        "table": FactRentContract,
        "constraint": "ux_rent_natural",
        "key_cols": dd.RENT_KEY_COLS,
        "update_cols": dd.RENT_UPDATE_COLS,
    },
}


def import_file(
    session: Session,
    path: Path,
    spec: dict,
    caches: DimCaches,
    source_id: int,
    source_url: str,
    encoding: str = "utf-8-sig",
) -> dict:
    read = written = skipped = 0
    batch: list[dict] = []

    def flush() -> None:
        nonlocal written
        if not batch:
            return
        written += _upsert_facts(
            session,
            spec["table"].__table__,
            spec["constraint"],
            spec["key_cols"],
            spec["update_cols"],
            batch,
        )
        session.commit()
        batch.clear()

    with open(path, encoding=encoding, newline="") as fh:
        for row in csv.DictReader(fh):
            read += 1
            values = spec["mapper"](row, caches, source_id, source_url)
            if values is None:
                skipped += 1
                continue
            batch.append(values)
            if len(batch) >= BATCH:
                flush()
    flush()

    log.info("%s: read=%s written=%s skipped=%s", path.name, read, written, skipped)
    return {"file": path.name, "read": read, "written": written, "skipped": skipped}


def import_dataset(session: Session, key: str, source_url: str) -> dict:
    dataset = DATASETS[key]
    spec = _SPEC[key]
    files = files_for(dataset)
    if not files:
        raise FileNotFoundError(
            f"no files matching {dataset.pattern!r} under the data/raw mount — "
            "see docs/DATADUBAI_ANALYSIS.md"
        )

    sid = resolve_source_id(session, dataset.source_code)
    caches = DimCaches(session)

    report = {"dataset": key, "files": [], "read": 0, "written": 0, "skipped": 0}
    for path in files:
        part = import_file(
            session, path, spec, caches, sid, source_url, dataset.encoding
        )
        report["files"].append(part)
        for field in ("read", "written", "skipped"):
            report[field] += part[field]
    log.info(
        "%s import complete: files=%s read=%s written=%s skipped=%s",
        key,
        len(files),
        report["read"],
        report["written"],
        report["skipped"],
    )
    return report
