"""Dataset coverage — what the data actually contains.

The point of this endpoint is negative information: it tells a client where
the data *stops*, so an LLM cannot answer confidently about a period or an
entity we have nothing for. The UI uses the same payload to bound its date
pickers and to warn about partial geometry.
"""

from __future__ import annotations

from dxb_core.constants import RENT_MAX_HORIZON_YEARS, SALE_MIN_DATE
from dxb_core.models import (
    DimArea,
    DimProject,
    DimSource,
    EtlRun,
    EtlSourceCutover,
    FactRentContract,
    FactSaleTransaction,
    MartAreaMonthly,
    MartProjectMonthly,
)
from sqlalchemy import case, func, select, text

from dxb_api.repositories.base import BaseRepository

# The same sanity bounds the mart rebuild applies — now imported from
# dxb_core.constants rather than re-declared here, which is what they were
# before and how they would eventually have drifted apart from the ELT's copy.
#
# Why they exist at all: the raw extremes are not usable as a coverage
# statement, because the source data holds a handful of impossible dates — a
# sale stamped 1416 (a Hijri year that leaked through as Gregorian) and leases
# starting in 2205. Reporting those as the range would tell a client we have
# six centuries of data, which is exactly the confident-wrong-answer this
# endpoint exists to prevent. So the usable range is reported and the excluded
# rows are counted, not hidden.


class MetaRepository(BaseRepository):
    async def coverage(self) -> dict:
        sale_in_range = FactSaleTransaction.txn_date >= func.date(SALE_MIN_DATE)
        sales = (
            await self._session.execute(
                select(
                    func.count(FactSaleTransaction.id),
                    func.min(case((sale_in_range, FactSaleTransaction.txn_date))),
                    func.max(case((sale_in_range, FactSaleTransaction.txn_date))),
                    func.count(case((~sale_in_range, 1))),
                )
            )
        ).one()

        rent_in_range = FactRentContract.start_date <= text(
            f"current_date + interval '{RENT_MAX_HORIZON_YEARS} years'"
        )
        rents = (
            await self._session.execute(
                select(
                    func.count(FactRentContract.id),
                    func.min(case((rent_in_range, FactRentContract.start_date))),
                    func.max(case((rent_in_range, FactRentContract.start_date))),
                    func.count(case((~rent_in_range, 1))),
                )
            )
        ).one()
        marts = (
            await self._session.execute(
                select(
                    func.count(MartAreaMonthly.area_id),
                    func.min(MartAreaMonthly.month),
                    func.max(MartAreaMonthly.month),
                )
            )
        ).one()
        project_marts = (
            await self._session.execute(
                select(func.count(MartProjectMonthly.project_id))
            )
        ).scalar_one()

        geo = (
            await self._session.execute(
                select(
                    func.count(DimArea.id),
                    func.count(DimArea.centroid),
                    func.count(DimArea.boundary),
                )
            )
        ).one()
        projects = (
            await self._session.execute(
                select(func.count(DimProject.id), func.count(DimProject.location))
            )
        ).one()

        cutovers = (
            await self._session.execute(
                select(
                    EtlSourceCutover.dataset,
                    EtlSourceCutover.cutover_date,
                    DimSource.code,
                ).join(DimSource, DimSource.id == EtlSourceCutover.source_id)
            )
        ).all()

        sources = (
            await self._session.execute(
                select(
                    DimSource.id,
                    DimSource.code,
                    DimSource.name,
                    DimSource.is_government,
                )
            )
        ).all()

        last_run = (
            await self._session.execute(
                select(EtlRun.id, EtlRun.kind, EtlRun.status, EtlRun.finished_at)
                .where(EtlRun.status == "ok")
                .order_by(EtlRun.finished_at.desc())
                .limit(1)
            )
        ).first()

        return {
            "datasets": {
                "sale_transactions": {
                    "row_count": int(sales[0] or 0),
                    "date_from": sales[1],
                    "date_to": sales[2],
                    "date_field": "txn_date",
                    "rows_excluded_implausible_date": int(sales[3] or 0),
                    "note": (
                        f"Range covers rows from {SALE_MIN_DATE} onward, the "
                        "same bound the marts apply. A few source rows carry "
                        "impossible dates (e.g. a Hijri year stored as "
                        "Gregorian) and are counted, not included."
                    ),
                },
                "rent_contracts": {
                    "row_count": int(rents[0] or 0),
                    "date_from": rents[1],
                    "date_to": rents[2],
                    "date_field": "start_date",
                    "rows_excluded_implausible_date": int(rents[3] or 0),
                    "note": (
                        "date_to is legitimately in the future: leases are "
                        "routinely registered ahead of their start date. The "
                        f"range is capped at today + {RENT_MAX_HORIZON_YEARS} "
                        "years, matching the marts; rows beyond that are "
                        "data errors and are counted, not included."
                    ),
                },
            },
            "marts": {
                "area_monthly_rows": int(marts[0] or 0),
                "project_monthly_rows": int(project_marts or 0),
                "month_from": marts[1],
                "month_to": marts[2],
            },
            "geo_coverage": {
                "areas_total": int(geo[0] or 0),
                "areas_with_centroid": int(geo[1] or 0),
                "areas_with_boundary": int(geo[2] or 0),
                "projects_total": int(projects[0] or 0),
                "projects_with_location": int(projects[1] or 0),
                "note": (
                    "Choropleths require a boundary and materially fewer areas "
                    "have one than have a centroid. Project locations are not "
                    "yet populated."
                ),
            },
            "source_cutovers": [
                {
                    "dataset": c.dataset,
                    "cutover_date": c.cutover_date,
                    "authoritative_source_before_cutover": c.code,
                    "note": (
                        "Aggregations use this source up to the cutover and the "
                        "live gateway after it, which is how cross-source "
                        "duplicates are neutralized."
                    ),
                }
                for c in cutovers
            ],
            "sources": [dict(s._mapping) for s in sources],
            "last_successful_etl_run": (
                {
                    "id": last_run.id,
                    "kind": last_run.kind,
                    "finished_at": last_run.finished_at,
                }
                if last_run
                else None
            ),
        }

    async def etl_version_token(self) -> str:
        """Cheap cache key: the data changes at most once a day."""
        row = (
            await self._session.execute(
                select(func.max(EtlRun.finished_at)).where(EtlRun.status == "ok")
            )
        ).scalar()
        return row.isoformat() if row else "no-run"
