"""Transform: stg_raw JSONB -> dims & facts.

Upsert strategy (plan §3):
- dims: get-or-create + in-place update (living entities);
- facts: batched INSERT .. ON CONFLICT ON CONSTRAINT <natural key> DO UPDATE,
  guarded by IS DISTINCT FROM so unchanged rows are not rewritten;
  updated_at fires only on real changes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterator

from dxb_core.models import (
    DimArea,
    DimBuilding,
    DimDeveloper,
    DimProject,
    DimPropertyType,
    FactRentContract,
    FactSaleTransaction,
    StgRaw,
)
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from dxb.transform.area_resolve import (
    canonical_area,
    project_actual_reviewed_map,
    project_area_map,
    unambiguous_successor_map,
)
from dxb.transform.keys import txn_key_from_gateway

log = logging.getLogger(__name__)

BATCH = 5000


# ------------------------------------------------------------- small utils


def norm_name(value: str | None) -> str | None:
    if value is None:
        return None
    name = " ".join(str(value).split()).upper()
    return name or None


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    f = to_float(value)
    return int(f) if f is not None else None


def to_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    return str(value) in ("1", "True", "true")


def to_date(value: str | None):
    if not value:
        return None
    return datetime.fromisoformat(value).date()


def to_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# ------------------------------------------------------------- dim caches


class DimCaches:
    """In-memory id lookups; dim cardinalities are tiny (areas ~300,
    projects ~few thousand), so caching them whole is fine."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.areas: dict[str, int] = {
            name: id_
            for name, id_ in session.execute(select(DimArea.name_en, DimArea.id))
        }
        self.ptypes: dict[tuple, int] = {
            (u, t, s): id_
            for u, t, s, id_ in session.execute(
                select(
                    DimPropertyType.usage,
                    DimPropertyType.prop_type,
                    DimPropertyType.prop_subtype,
                    DimPropertyType.id,
                )
            )
        }
        self.projects: dict[tuple[str, bool], int] = {
            (name, bool(master)): id_
            for name, master, id_ in session.execute(
                select(DimProject.name_en, DimProject.is_master, DimProject.id)
            )
        }
        self.developers: dict[int, int] = {
            num: id_
            for num, id_ in session.execute(
                select(DimDeveloper.dld_number, DimDeveloper.id)
            )
            if num is not None
        }
        # Three resolution tiers (docs/AREA_CODE_MIGRATION_ANALYSIS.md "One
        # mechanism, used everywhere" / "Schema (revised)"): (1) project_id
        # -> new_area_id for reviewed project_area_actual rows — read-time
        # indirection, never written by this mechanism; (2) project_id ->
        # dim_project.area_id, exactly as stored; (3) old_area_id -> its
        # single unambiguous reviewed successor. Together these resolve the
        # canonical area key a building lookup should use, so a building
        # historically stored under an old area code is still found (not
        # duplicated) once its project's reviewed mapping — or raw area, or
        # its own area's single successor — points elsewhere. See
        # _canonical_area()/building() below.
        self._project_actual: dict[int, int] = project_actual_reviewed_map(session)
        self._project_area: dict[int, int | None] = project_area_map(session)
        self._area_single_successor: dict[int, int] = unambiguous_successor_map(session)
        # (name_en, canonical_area_id) -> building id. Distinct buildings are
        # ~120k, so caching whole is fine and avoids a per-row SELECT on the
        # hot path. Keyed on the CANONICAL area (not the raw stored area_id)
        # so a building found under either an old or new area code for the
        # same community resolves to the same row — see building() below.
        self.buildings: dict[tuple[str, int | None], int] = {
            (name, self._canonical_area(area_id, project_id)): id_
            for name, area_id, project_id, id_ in session.execute(
                select(
                    DimBuilding.name_en,
                    DimBuilding.area_id,
                    DimBuilding.project_id,
                    DimBuilding.id,
                )
            )
        }
        # buildings whose project_id we've already backfilled this run.
        self._building_projects: dict[int, int] = {}

    def _canonical_area(
        self, area_id: int | None, project_id: int | None = None
    ) -> int | None:
        """Resolve the match-key area for a building lookup: prefer a
        reviewed project_area_actual mapping for the row's project when one
        exists; else the project's own stored area_id, untouched; else
        redirect through the row's own area's single reviewed successor, if
        unambiguous; else leave area_id unchanged (never guess between
        several successors)."""
        return canonical_area(
            area_id,
            project_id,
            self._project_actual,
            self._project_area,
            self._area_single_successor,
        )

    def area(self, name: str | None, name_ar: str | None = None) -> int | None:
        name = norm_name(name)
        if not name:
            return None
        if name not in self.areas:
            area = DimArea(name_en=name, name_ar=to_text(name_ar))
            self.session.add(area)
            self.session.flush()
            self.areas[name] = area.id
        return self.areas[name]

    def ptype(self, usage: Any, prop_type: Any, subtype: Any) -> int | None:
        usage, prop_type, subtype = to_text(usage), to_text(prop_type), to_text(subtype)
        if not usage and not prop_type:
            return None
        key = (usage or "Unknown", prop_type or "Unknown", subtype)
        if key not in self.ptypes:
            pt = DimPropertyType(usage=key[0], prop_type=key[1], prop_subtype=key[2])
            self.session.add(pt)
            self.session.flush()
            self.ptypes[key] = pt.id
        return self.ptypes[key]

    def project(
        self,
        name: str | None,
        *,
        is_master: bool = False,
        master_name: str | None = None,
        area_id: int | None = None,
        name_ar: str | None = None,
    ) -> int | None:
        name = norm_name(name)
        if not name:
            return None
        key = (name, is_master)
        if key not in self.projects:
            master_id = (
                self.project(master_name, is_master=True) if master_name else None
            )
            project = DimProject(
                name_en=name,
                name_ar=to_text(name_ar),
                is_master=is_master,
                master_project_id=master_id,
                master_project_en=to_text(master_name),
                area_id=area_id,
            )
            self.session.add(project)
            self.session.flush()
            self.projects[key] = project.id
            # Keep the project-first canonicalization tier in sync for a
            # project created mid-run (e.g. by a later batch's building
            # lookup) — otherwise it would key-miss the preloaded map.
            self._project_area[project.id] = project.area_id
        return self.projects[key]

    def developer(
        self, dld_number: Any, name_en: Any, name_ar: Any = None
    ) -> int | None:
        num = to_int(dld_number)
        name = to_text(name_en)
        if num is None and not name:
            return None
        if num is not None and num in self.developers:
            return self.developers[num]
        dev = DimDeveloper(
            dld_number=num, name_en=name or f"#{num}", name_ar=to_text(name_ar)
        )
        self.session.add(dev)
        self.session.flush()
        if num is not None:
            self.developers[num] = dev.id
        return dev.id

    def building(
        self,
        name: str | None,
        *,
        area_id: int | None,
        project_id: int | None = None,
        name_ar: str | None = None,
    ) -> int | None:
        """Resolve/create a dim_building by (name_en, area_id).

        Stubbed here from a transaction's building_name — location and physical
        attributes are filled later (Makani geocoding, CSV enrichment). Lazily
        backfills project_id if this row knows it and the stub did not.

        The lookup/cache KEY canonicalizes area_id via the project-first rule
        (docs/AREA_CODE_MIGRATION_ANALYSIS.md) so a building already stored
        under an old area code is found and reused when the same building
        name later shows up under its new canonical code — instead of
        creating a second, duplicate row. This does NOT change what gets
        stored in a genuinely NEW row's own area_id column: that still holds
        whatever area_id the source actually reported.
        """
        name = norm_name(name)
        if not name:
            return None
        key = (name, self._canonical_area(area_id, project_id))
        if key not in self.buildings:
            b = DimBuilding(
                name_en=name,
                name_ar=to_text(name_ar),
                area_id=area_id,
                project_id=project_id,
            )
            self.session.add(b)
            self.session.flush()
            self.buildings[key] = b.id
        elif project_id is not None:
            self._backfill_building_project(self.buildings[key], project_id)
        return self.buildings[key]

    def _backfill_building_project(self, building_id: int, project_id: int) -> None:
        if self._building_projects.get(building_id) is None:
            b = self.session.get(DimBuilding, building_id)
            if b is not None and b.project_id is None:
                b.project_id = project_id
            self._building_projects[building_id] = project_id


# --------------------------------------------------------- staging reader


def _staged_batches(session: Session, endpoint: str) -> Iterator[list[StgRaw]]:
    while True:
        batch = list(
            session.scalars(
                select(StgRaw)
                .where(StgRaw.endpoint == endpoint, StgRaw.processed_at.is_(None))
                .order_by(StgRaw.id)
                .limit(BATCH)
            )
        )
        if not batch:
            return
        yield batch


def _mark_processed(session: Session, batch: list[StgRaw]) -> None:
    ids = [s.id for s in batch]
    session.execute(
        update(StgRaw)
        .where(StgRaw.id.in_(ids))
        .values(processed_at=datetime.now(timezone.utc))
    )


# ------------------------------------------------------------- transforms


def transform_areas(session: Session, caches: DimCaches) -> int:
    """carea-lookup rows -> dim_area (adds official code + Arabic name)."""
    n = 0
    for batch in _staged_batches(session, "carea-lookup"):
        for stg in batch:
            row = stg.payload_json
            name = norm_name(row.get("NAME_EN"))
            code = to_text(row.get("AREA_ID"))
            if not name:
                continue
            area = session.scalar(select(DimArea).where(DimArea.dld_area_code == code))
            if area is None and name in caches.areas:
                area = session.get(DimArea, caches.areas[name])
            if area is None:
                area = DimArea(name_en=name)
                session.add(area)
                session.flush()
            area.dld_area_code = code
            area.name_ar = to_text(row.get("NAME_AR")) or area.name_ar
            if area.name_en != name and name not in caches.areas:
                pass  # keep first-seen spelling as canonical; code is the anchor
            caches.areas[area.name_en] = area.id
            n += 1
        _mark_processed(session, batch)
        session.commit()
    return n


def transform_projects(session: Session, caches: DimCaches, source_url: str) -> int:
    """projects rows -> dim_project upsert by dld_project_number."""
    n = 0
    for batch in _staged_batches(session, "projects"):
        for stg in batch:
            row = stg.payload_json
            number = to_int(row.get("PROJECT_NUMBER"))
            name = norm_name(row.get("PROJECT_EN"))
            if not name:
                continue
            project = None
            if number is not None:
                project = session.scalar(
                    select(DimProject).where(DimProject.dld_project_number == number)
                )
            if project is None and (name, False) in caches.projects:
                project = session.get(DimProject, caches.projects[(name, False)])
            if project is None:
                project = DimProject(name_en=name, is_master=False)
                session.add(project)
            area_id = caches.area(row.get("AREA_EN"), row.get("AREA_AR"))
            master_name = to_text(row.get("MASTER_PROJECT_EN"))
            project.dld_project_number = number
            project.name_ar = to_text(row.get("PROJECT_AR"))
            project.master_project_en = master_name
            project.master_project_id = (
                caches.project(master_name, is_master=True) if master_name else None
            )
            project.area_id = area_id
            project.developer_id = caches.developer(
                row.get("DEVELOPER_NUMBER"),
                row.get("DEVELOPER_EN"),
                row.get("DEVELOPER_AR"),
            )
            project.status = to_text(row.get("PROJECT_STATUS"))
            project.project_type = to_text(row.get("PRJ_TYPE_EN"))
            project.project_value_aed = to_float(row.get("PROJECT_VALUE"))
            project.start_date = to_date(row.get("START_DATE"))
            project.end_date = to_date(row.get("END_DATE"))
            project.completion_date = to_date(row.get("COMPLETION_DATE"))
            project.percent_completed = to_float(row.get("PERCENT_COMPLETED"))
            project.cnt_units = to_int(row.get("CNT_UNIT"))
            project.cnt_villas = to_int(row.get("CNT_VILLA"))
            project.cnt_buildings = to_int(row.get("CNT_BUILDING"))
            project.description = to_text(row.get("DESCRIPTION_EN"))
            project.source_id = stg.source_id
            project.source_url = source_url
            session.flush()
            caches.projects[(name, False)] = project.id
            caches._project_area[project.id] = area_id
            n += 1
        _mark_processed(session, batch)
        session.commit()
    return n


_SALE_UPDATE_COLS = [
    "txn_key",
    "txn_group",
    "procedure_name",
    "is_offplan",
    "is_freehold",
    "property_type_id",
    "rooms",
    "parking",
    "area_id",
    "project_id",
    "parcel_id",
    "amount_aed",
    "source_ref",
]


def _sale_values(stg: StgRaw, caches: DimCaches, source_url: str) -> dict | None:
    row = stg.payload_json
    txn_number = to_text(row.get("TRANSACTION_NUMBER"))
    txn_date = to_text(row.get("INSTANCE_DATE"))
    amount = to_float(row.get("TRANS_VALUE"))
    area_id = caches.area(row.get("AREA_EN"), row.get("AREA_AR"))
    if not txn_number or not txn_date or amount is None or area_id is None:
        return None
    return {
        "txn_number": txn_number,
        "txn_key": txn_key_from_gateway(txn_number),
        "txn_date": datetime.fromisoformat(txn_date),
        "txn_group": to_text(row.get("GROUP_EN")) or "Unknown",
        "procedure_name": to_text(row.get("PROCEDURE_EN")),
        "is_offplan": bool(to_bool(row.get("IS_OFFPLAN"))),
        "is_freehold": bool(to_bool(row.get("IS_FREE_HOLD"))),
        "property_type_id": caches.ptype(
            row.get("USAGE_EN"), row.get("PROP_TYPE_EN"), row.get("PROP_SB_TYPE_EN")
        ),
        "rooms": to_text(row.get("ROOMS_EN")),
        "parking": to_text(row.get("PARKING")),
        "area_id": area_id,
        "project_id": caches.project(
            row.get("PROJECT_EN"),
            master_name=to_text(row.get("MASTER_PROJECT_EN")),
            area_id=area_id,
        ),
        "parcel_id": to_int(row.get("PARCEL_ID")),
        "actual_area_m2": to_float(row.get("ACTUAL_AREA")),
        "amount_aed": amount,
        "source_id": stg.source_id,
        "source_url": source_url,
        "source_ref": txn_number,
    }


_RENT_UPDATE_COLS = [
    "registration_date",
    "start_date",
    "end_date",
    "version",
    "is_freehold",
    "property_type_id",
    "rooms",
    "area_id",
    "project_id",
    "parcel_id",
    "actual_area_m2",
    "annual_amount_aed",
    "contract_amount_aed",
]


def _gateway_rent_source_ref(row: dict) -> str:
    """The gateway exposes no contract number (CONTRACT_NUMBER is always
    null), so identity is a deterministic composite of the fields that
    describe the contract. Same components as the pre-data.dubai natural key,
    collapsed into the single source_ref the table now keys on."""
    parts = [
        to_text(row.get("REGISTRATION_DATE")) or "",
        to_text(row.get("AREA_EN")) or "",
        to_text(row.get("ANNUAL_AMOUNT")) or "",
        to_text(row.get("ACTUAL_AREA")) or "",
        to_text(row.get("START_DATE")) or "",
        to_text(row.get("END_DATE")) or "",
    ]
    return "|".join(parts)


def _rent_values(stg: StgRaw, caches: DimCaches, source_url: str) -> dict | None:
    row = stg.payload_json
    registration = to_text(row.get("REGISTRATION_DATE"))
    annual = to_float(row.get("ANNUAL_AMOUNT"))
    area_id = caches.area(row.get("AREA_EN"), row.get("AREA_AR"))
    if not registration or annual is None or area_id is None:
        return None
    return {
        "registration_date": datetime.fromisoformat(registration),
        "start_date": to_date(row.get("START_DATE")),
        "end_date": to_date(row.get("END_DATE")),
        "version": to_text(row.get("VERSION_EN")),
        "is_freehold": to_bool(row.get("IS_FREE_HOLD")),
        "property_type_id": caches.ptype(
            row.get("USAGE_EN"), row.get("PROP_TYPE_EN"), row.get("PROP_SUB_TYPE_EN")
        ),
        "rooms": to_text(row.get("ROOMS")),
        "area_id": area_id,
        "project_id": caches.project(
            row.get("PROJECT_EN"),
            master_name=to_text(row.get("MASTER_PROJECT_EN")),
            area_id=area_id,
        ),
        "parcel_id": to_int(row.get("PARCEL_ID")),
        "actual_area_m2": to_float(row.get("ACTUAL_AREA")),
        "annual_amount_aed": annual,
        "contract_amount_aed": to_float(row.get("CONTRACT_AMOUNT")),
        # The gateway returns one row per contract and no property count.
        "contract_id": None,
        "line_number": None,
        "no_of_prop": None,
        "source_id": stg.source_id,
        "source_url": source_url,
        "source_ref": _gateway_rent_source_ref(row),
    }


# psycopg/libpq hard-caps a single statement at 65535 bind parameters. Leave
# headroom below that (some params are consumed by the ON CONFLICT / WHERE
# clause referencing `excluded`, not just the VALUES rows).
_MAX_BIND_PARAMS = 60000


def _upsert_facts(
    session: Session,
    table,
    constraint: str,
    key_cols: list[str],
    update_cols: list[str],
    values: list[dict],
) -> int:
    """Guarded upsert: update only when a non-key column actually differs.
    Batch is deduped on the natural key first (ON CONFLICT DO UPDATE cannot
    touch the same row twice in one statement), then chunked to stay under
    Postgres's per-statement bind-parameter limit."""
    if not values:
        return 0
    deduped = {tuple(v.get(k) for k in key_cols): v for v in values}
    deduped_values = list(deduped.values())

    n_cols = len(deduped_values[0])
    chunk_size = max(1, _MAX_BIND_PARAMS // n_cols)

    written = 0
    for i in range(0, len(deduped_values), chunk_size):
        chunk = deduped_values[i : i + chunk_size]
        stmt = pg_insert(table).values(chunk)
        excluded = stmt.excluded
        changed = or_(
            *[table.c[col].is_distinct_from(excluded[col]) for col in update_cols]
        )
        stmt = (
            stmt.on_conflict_do_update(
                constraint=constraint,
                set_={
                    **{c: excluded[c] for c in update_cols},
                    "updated_at": func.now(),
                },
                where=changed,
            )
            # rowcount is unreliable for chunked multi-row INSERT..ON CONFLICT
            # under SQLAlchemy's insertmanyvalues strategy (seen returning -1
            # per chunk, summing to negative totals); RETURNING is exact, and
            # as a bonus only counts rows that were actually inserted/updated
            # (conflicts skipped by the guard emit no RETURNING row).
            .returning(table.c.id)
        )
        result = session.execute(stmt)
        written += len(result.fetchall())
    return written


def transform_transactions(
    session: Session, caches: DimCaches, source_url: str
) -> dict:
    written = skipped = 0
    for batch in _staged_batches(session, "transactions"):
        values = []
        for stg in batch:
            v = _sale_values(stg, caches, source_url)
            if v is None:
                skipped += 1
            else:
                values.append(v)
        written += _upsert_facts(
            session,
            FactSaleTransaction.__table__,
            "ux_sale_natural",
            ["source_id", "txn_number", "txn_date", "actual_area_m2"],
            _SALE_UPDATE_COLS,
            values,
        )
        _mark_processed(session, batch)
        session.commit()
    return {"endpoint": "transactions", "written": written, "skipped": skipped}


def transform_rents(session: Session, caches: DimCaches, source_url: str) -> dict:
    written = skipped = 0
    for batch in _staged_batches(session, "rents"):
        values = []
        for stg in batch:
            v = _rent_values(stg, caches, source_url)
            if v is None:
                skipped += 1
            else:
                values.append(v)
        written += _upsert_facts(
            session,
            FactRentContract.__table__,
            "ux_rent_natural",
            ["source_id", "source_ref"],
            _RENT_UPDATE_COLS,
            values,
        )
        _mark_processed(session, batch)
        session.commit()
    return {"endpoint": "rents", "written": written, "skipped": skipped}


def transform_all(session: Session, source_url: str) -> dict:
    caches = DimCaches(session)
    report: dict = {}
    report["areas"] = transform_areas(session, caches)
    report["projects"] = transform_projects(session, caches, source_url)
    report["transactions"] = transform_transactions(session, caches, source_url)
    report["rents"] = transform_rents(session, caches, source_url)
    return report
