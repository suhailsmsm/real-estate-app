"""Postgres star schema: dims, facts, staging and ETL bookkeeping.

Every table carries created_at/updated_at via TimestampMixin (plan §4):
updated_at stays NULL until the row actually changes.

--------------------------------------------------------------------------
DO NOT ADD `relationship()` TO THESE MODELS.  (Load-bearing — see CLAUDE.md.)

These definitions are shared by the synchronous ELT and the **async** API.
They are safe in async code precisely because they declare only columns and
FK constraints: with no ORM relationships there is no implicit lazy load, and
therefore no `MissingGreenlet` explosion inside an `AsyncSession`.

Adding a relationship would make every async query that touches it a latent
runtime error unless it eager-loads (`selectinload`/`joinedload`). If one ever
becomes genuinely necessary, add it deliberately with a comment saying so —
never casually. Repositories use explicit joins instead.
--------------------------------------------------------------------------
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )


# ---------------------------------------------------------------- staging


class StgRaw(TimestampMixin, Base):
    __tablename__ = "stg_raw"
    __table_args__ = (
        Index(
            "ix_stg_todo",
            "endpoint",
            postgresql_where="processed_at IS NULL",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("dim_source.id"))
    endpoint: Mapped[str] = mapped_column(Text)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    record_hash: Mapped[str] = mapped_column(Text, unique=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EtlWatermark(TimestampMixin, Base):
    __tablename__ = "etl_watermark"

    source_id: Mapped[int] = mapped_column(
        ForeignKey("dim_source.id"), primary_key=True
    )
    endpoint: Mapped[str] = mapped_column(Text, primary_key=True)
    last_date: Mapped[date] = mapped_column(Date)


class EtlSourceCutover(TimestampMixin, Base):
    """Analytic source-precedence boundary, per dataset.

    Deliberately separate from EtlWatermark — they are different concepts:
      EtlWatermark      = gateway *collection cursor*, by registration date
                          (the API's own filter semantics)
      EtlSourceCutover  = *aggregation* boundary, by the mart axis
                          (txn_date for sales, start_date for rents)

    Marts include a fact row iff:
        (source is data.dubai AND axis_date <= cutover_date)
     OR (source is gateway    AND axis_date >  cutover_date)

    This neutralizes cross-source duplicates without record-level matching
    (which is impossible for mortgages/gifts/rents — see
    docs/DATADUBAI_REBUILD_PLAN.md §1). Raw facts keep everything from both
    sources; only the aggregation picks a winner. Because marts are rebuilt
    wholesale, moving the cutover after a fresh data.dubai download
    re-segregates everything automatically.
    """

    __tablename__ = "etl_source_cutover"

    dataset: Mapped[str] = mapped_column(Text, primary_key=True)  # transactions | rents
    source_id: Mapped[int] = mapped_column(ForeignKey("dim_source.id"))
    cutover_date: Mapped[date] = mapped_column(Date)


class EtlRun(TimestampMixin, Base):
    __tablename__ = "etl_run"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text)  # daily | backfill | manual
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text)  # running | ok | failed
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)


# ------------------------------------------------------------- dimensions


class DimSource(TimestampMixin, Base):
    __tablename__ = "dim_source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    base_url: Mapped[str] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(Text)
    # Lets any query/mart filter to verified government data only vs. include
    # third-party historical mirrors (CSV imports) — the lightweight
    # trust-separation mechanism instead of a second database.
    is_government: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class DimArea(TimestampMixin, Base):
    __tablename__ = "dim_area"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dld_area_code: Mapped[str | None] = mapped_column(Text, unique=True)  # 'A-292'
    name_en: Mapped[str] = mapped_column(Text, unique=True)  # canonical UPPER-cased
    name_ar: Mapped[str | None] = mapped_column(Text)
    zone_name: Mapped[str | None] = mapped_column(Text)
    centroid: Mapped[Any | None] = mapped_column(Geography("POINT", srid=4326))
    boundary: Mapped[Any | None] = mapped_column(Geography("MULTIPOLYGON", srid=4326))
    # Provenance for centroid/boundary — which source populated them and how
    # confidently. Distinct from an area's DLD data provenance: a
    # gateway-sourced area can still get its geometry from OSM.
    geo_source_id: Mapped[int | None] = mapped_column(ForeignKey("dim_source.id"))
    geo_match_method: Mapped[str | None] = mapped_column(
        Text
    )  # exact | parent_fallback | manual | name_hint

    # NOTE on the area-code migration (docs/AREA_CODE_MIGRATION_ANALYSIS.md):
    # DLD re-coded ~89 established communities starting 2026-07-20, and an old
    # area can fan out into SEVERAL new ones (MARSA DUBAI alone split into 4
    # disjoint communities) — a scalar pointer on this row cannot represent
    # that, so there is deliberately none here. `AreaCodeEvidence` (the
    # header, reviewed-gated) and `ProjectAreaActual` (the operational,
    # project-level lookup) carry the relationship instead. Nothing on
    # DimArea itself is ever rewritten by this mechanism.


class AreaCodeEvidence(TimestampMixin, Base):
    """Why the detector believes `old_area_id` was (partly) re-coded as
    `new_area_id`, and everything that belonged to the old area at detection
    time — old-area metadata, e.g. for a future map view of a superseded
    area's contents. Not consulted by the resolution mechanism (see
    `ProjectAreaActual`) except as the *fallback* for rows with no project to
    anchor on, and only then when `old_area_id` has exactly one reviewed
    successor — never a guess between several.

    Purely additive audit data — inserted/updated by a non-fatal ELT pipeline
    step. Nothing merges off an unreviewed row: `reviewed` gates
    `ProjectAreaActual`'s apply step the same way `sample_tier` gates trust in
    the buildings mart, not by deleting or hiding the detection, just by
    refusing to act on it automatically.
    """

    __tablename__ = "area_code_evidence"

    old_area_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_area.id"), primary_key=True
    )
    new_area_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_area.id"), primary_key=True
    )
    evidence_project_overlap_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    evidence_txn_count: Mapped[int] = mapped_column(Integer)
    # Denormalized snapshot of the overlap project set at detection time —
    # display/metadata only. The operational lookup is ProjectAreaActual,
    # below, not this array.
    evidence_project_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    first_seen_new_code: Mapped[date | None] = mapped_column(Date)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)


class ProjectAreaActual(TimestampMixin, Base):
    """The operational old->new area lookup, at PROJECT grain.

    Why project, not area: each new area code's transacted projects are
    disjoint from every other new code's (measured directly — zero shared
    projects across MARSA DUBAI's four successors), so a project resolves to
    exactly one canonical area unambiguously, even when its OLD area does
    not (44% of split old areas have more than one successor — an old-area
    pointer cannot represent that, a project-level one does not need to).

    One row per project — `project_id` is the primary key because a project
    has exactly one current target, refreshable by upsert as the detector
    re-runs. Populated by the same detector run that writes
    `AreaCodeEvidence`. No `reviewed` flag of its own: trust flows from the
    parent `AreaCodeEvidence` row for `(old_area_id, new_area_id)` — a row
    here is only ever *applied* (used to backfill `DimProject.area_id`,
    cascading to that project's buildings) once that pair is reviewed.
    """

    __tablename__ = "project_area_actual"

    project_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dim_project.id"), primary_key=True
    )
    old_area_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_area.id"), nullable=False
    )
    new_area_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_area.id"), nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DimDeveloper(TimestampMixin, Base):
    __tablename__ = "dim_developer"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dld_number: Mapped[int | None] = mapped_column(Integer, unique=True)
    name_en: Mapped[str] = mapped_column(Text)
    name_ar: Mapped[str | None] = mapped_column(Text)


class DimProject(TimestampMixin, Base):
    __tablename__ = "dim_project"
    __table_args__ = (UniqueConstraint("name_en", "is_master", name="ux_project_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dld_project_number: Mapped[int | None] = mapped_column(Integer, unique=True)
    name_en: Mapped[str] = mapped_column(Text)  # canonical UPPER-cased
    name_ar: Mapped[str | None] = mapped_column(Text)
    master_project_id: Mapped[int | None] = mapped_column(ForeignKey("dim_project.id"))
    master_project_en: Mapped[str | None] = mapped_column(Text)
    is_master: Mapped[bool] = mapped_column(Boolean, default=False)
    area_id: Mapped[int | None] = mapped_column(ForeignKey("dim_area.id"))
    developer_id: Mapped[int | None] = mapped_column(ForeignKey("dim_developer.id"))
    status: Mapped[str | None] = mapped_column(Text)
    project_type: Mapped[str | None] = mapped_column(Text)
    project_value_aed: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    completion_date: Mapped[date | None] = mapped_column(Date)
    percent_completed: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    cnt_units: Mapped[int | None] = mapped_column(Integer)
    cnt_villas: Mapped[int | None] = mapped_column(Integer)
    cnt_buildings: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    location: Mapped[Any | None] = mapped_column(Geography("POINT", srid=4326))
    # Provenance for `location`, mirroring dim_area. The method is the
    # confidence signal, not decoration: 'nominatim_validated' is a real point
    # confirmed to fall inside the project's area; 'area_centroid' is a coarse
    # fallback shared by every project in that area. A map must never present
    # the latter as a precise location — see docs/PROJECT_GEO_ENRICHMENT.md.
    geo_source_id: Mapped[int | None] = mapped_column(ForeignKey("dim_source.id"))
    # building_point | building_centroid | master_of_children (Makani, precise)
    # | nominatim_validated (OSM name-match) | area_centroid (coarse fallback)
    geo_match_method: Mapped[str | None] = mapped_column(Text)
    # Project-level confidence for a building-derived location (docs
    # PROJECT_GEO_ENRICHMENT §6): how many validated buildings backed the
    # placement, and how dispersed they are. A large spread means the project
    # is a big footprint, not a precise dot — the map uses this to decide
    # between a pin and an area label.
    geo_building_count: Mapped[int | None] = mapped_column(Integer)
    geo_spread_m: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("dim_source.id"))
    source_url: Mapped[str | None] = mapped_column(Text)


Index("ix_project_area", DimProject.area_id)


class DimBuilding(TimestampMixin, Base):
    """A physical building — the addressable unit that carries a precise
    location (docs/PROJECT_GEO_ENRICHMENT.md §6).

    Populated from two sources with different strengths:
      * transactions   -> the searchable `name_en` (e.g. 'LAKE TERRACE') that
                          Makani geocodes; the building's project link.
      * data.dubai building CSVs -> the physical attributes below, joined by
                          (project_name + area) since the CSVs carry no clean
                          building name (only codes). See BUILDING_CSV_ANALYSIS.

    `location` is filled by Makani (Google-backed), accepted only if it falls
    inside the building's area (containment validation). The method column is
    the precise-vs-coarse signal, same discipline as dim_area/dim_project.
    """

    __tablename__ = "dim_building"
    __table_args__ = (
        UniqueConstraint("name_en", "area_id", name="ux_building_name_area"),
        Index("ix_building_project", "project_id"),
        Index("ix_building_area", "area_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name_en: Mapped[str] = mapped_column(Text)  # canonical UPPER-cased
    name_ar: Mapped[str | None] = mapped_column(Text)
    area_id: Mapped[int | None] = mapped_column(ForeignKey("dim_area.id"))
    # Nullable FK: the dominant project seen for this building in transactions.
    project_id: Mapped[int | None] = mapped_column(ForeignKey("dim_project.id"))

    # --- geolocation (Makani) ---
    location: Mapped[Any | None] = mapped_column(Geography("POINT", srid=4326))
    geo_source_id: Mapped[int | None] = mapped_column(ForeignKey("dim_source.id"))
    # makani_validated | makani_unvalidated | manual
    geo_match_method: Mapped[str | None] = mapped_column(Text)
    makani: Mapped[str | None] = mapped_column(Text)  # returned Makani no./community

    # --- physical attributes, enriched from the data.dubai building CSVs.
    # All nullable: a building stubbed from a transaction has only name+area
    # until the CSV enrichment (or never, if it is not in the CSVs).
    built_up_area: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    floors: Mapped[int | None] = mapped_column(Integer)
    flats: Mapped[int | None] = mapped_column(Integer)
    offices: Mapped[int | None] = mapped_column(Integer)
    shops: Mapped[int | None] = mapped_column(Integer)
    car_parks: Mapped[int | None] = mapped_column(Integer)
    elevators: Mapped[int | None] = mapped_column(Integer)
    swimming_pools: Mapped[int | None] = mapped_column(Integer)
    is_free_hold: Mapped[bool | None] = mapped_column(Boolean)
    building_status: Mapped[str | None] = mapped_column(Text)
    completion_date: Mapped[date | None] = mapped_column(Date)
    is_green_building: Mapped[bool | None] = mapped_column(Boolean)
    rooms: Mapped[str | None] = mapped_column(Text)  # villa-typical, e.g. '3 B/R'


class DimPropertyType(TimestampMixin, Base):
    __tablename__ = "dim_property_type"
    __table_args__ = (
        UniqueConstraint(
            "usage",
            "prop_type",
            "prop_subtype",
            name="ux_ptype",
            postgresql_nulls_not_distinct=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usage: Mapped[str] = mapped_column(Text)
    prop_type: Mapped[str] = mapped_column(Text)
    prop_subtype: Mapped[str | None] = mapped_column(Text)


class DimLocation(TimestampMixin, Base):
    __tablename__ = "dim_location"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    point: Mapped[Any] = mapped_column(Geography("POINT", srid=4326), nullable=False)
    geohash7: Mapped[str] = mapped_column(Text, index=True)
    area_id: Mapped[int | None] = mapped_column(ForeignKey("dim_area.id"))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("dim_source.id"))


class DimAddress(TimestampMixin, Base):
    __tablename__ = "dim_address"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_text: Mapped[str] = mapped_column(Text, unique=True)
    building_name: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("dim_project.id"))
    area_id: Mapped[int | None] = mapped_column(ForeignKey("dim_area.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("dim_location.id"))


# ------------------------------------------------------------------ facts


class FactSaleTransaction(TimestampMixin, Base):
    __tablename__ = "fact_sale_transaction"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "txn_number",
            "txn_date",
            "actual_area_m2",
            name="ux_sale_natural",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_sale_area_date", "area_id", "txn_date"),
        Index("ix_sale_project_date", "project_id", "txn_date"),
        Index("ix_sale_txn_key", "txn_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    txn_number: Mapped[str] = mapped_column(Text)
    # Normalized "{procedure_id}-{year}-{seq}", parsed from either id format:
    #   data.dubai "{group}-{proc}-{year}-{seq}"  e.g. 1-102-2026-59715
    #   gateway    "{proc}-{seq}-{year}"          e.g. 102-59715-2026
    # Verified to identify the same Sales transaction across both sources.
    # NOT a unique constraint: the numbering diverges for mortgages/gifts, so
    # this is a cross-source validation/merge tool for Sales, not an identity.
    txn_key: Mapped[str | None] = mapped_column(Text)
    txn_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    txn_group: Mapped[str] = mapped_column(Text)  # Sales / Mortgage / Gifts
    procedure_name: Mapped[str | None] = mapped_column(Text)
    is_offplan: Mapped[bool] = mapped_column(Boolean)
    # Nullable: the alexefimik historical CSV has no freehold-flag column at
    # all — NULL means "unknown", not "no", and is preferable to fabricating
    # a value. The live DLD gateway always supplies this, so existing rows
    # are unaffected.
    is_freehold: Mapped[bool | None] = mapped_column(Boolean)
    property_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("dim_property_type.id")
    )
    rooms: Mapped[str | None] = mapped_column(Text)
    parking: Mapped[str | None] = mapped_column(Text)
    area_id: Mapped[int] = mapped_column(ForeignKey("dim_area.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("dim_project.id"))
    building_id: Mapped[int | None] = mapped_column(ForeignKey("dim_building.id"))
    parcel_id: Mapped[int | None] = mapped_column(BigInteger)
    actual_area_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    amount_aed: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    price_per_m2: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        Computed(
            "CASE WHEN actual_area_m2 > 0 "
            "THEN round(amount_aed / actual_area_m2, 2) END",
            persisted=True,
        ),
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("dim_source.id"))
    source_url: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str | None] = mapped_column(Text)


class FactRentContract(TimestampMixin, Base):
    """One row = one *property* of a rent contract.

    data.dubai splits multi-property (portfolio) leases into one line per
    property, repeating the whole-contract amount on every line. Verified:
    0 of 917k contracts have a varying annual_amount across their lines. So
    the amounts stored here are **per property** (source total / no_of_prop),
    which makes rent_per_m2_year correct by construction.
    """

    __tablename__ = "fact_rent_contract"
    __table_args__ = (
        # Per-source identity. data.dubai: "{contract_id}:{line_number}"
        # (verified unique: 10,249,312/10,249,312, zero collisions).
        # Gateway: a deterministic composite string — it exposes no contract
        # number at all (CONTRACT_NUMBER is always null).
        UniqueConstraint("source_id", "source_ref", name="ux_rent_natural"),
        Index("ix_rent_contract_id", "contract_id"),
        # start_date is the mart axis (populated by BOTH sources), so it is
        # what the aggregation scans.
        Index("ix_rent_area_date", "area_id", "start_date"),
        Index("ix_rent_project_date", "project_id", "start_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Gateway-only: data.dubai exposes no registration date and we deliberately
    # do NOT overload this with the contract start date.
    registration_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    contract_id: Mapped[str | None] = mapped_column(Text)
    line_number: Mapped[int | None] = mapped_column(Integer)
    # Properties covered by the contract; 1 for gateway rows (it reports one
    # row per contract and no count). Used to divide the contract total into
    # per-property amounts at load time.
    no_of_prop: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[str | None] = mapped_column(Text)  # New / Renew
    is_freehold: Mapped[bool | None] = mapped_column(Boolean)
    property_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("dim_property_type.id")
    )
    rooms: Mapped[str | None] = mapped_column(Text)
    area_id: Mapped[int] = mapped_column(ForeignKey("dim_area.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("dim_project.id"))
    building_id: Mapped[int | None] = mapped_column(ForeignKey("dim_building.id"))
    parcel_id: Mapped[int | None] = mapped_column(BigInteger)
    actual_area_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    # Per property (source total / no_of_prop) — see class docstring.
    annual_amount_aed: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    contract_amount_aed: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    rent_per_m2_year: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        Computed(
            "CASE WHEN actual_area_m2 > 0 "
            "THEN round(annual_amount_aed / actual_area_m2, 2) END",
            persisted=True,
        ),
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("dim_source.id"))
    source_url: Mapped[str] = mapped_column(Text)
    # NOT NULL: this is the natural key. Every mapper must produce one.
    source_ref: Mapped[str] = mapped_column(Text)


class FactListing(TimestampMixin, Base):
    """Asking prices from listing portals — phase 2, defined up front."""

    __tablename__ = "fact_listing"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="ux_listing_natural"),
        Index("ix_listing_area", "area_id", "purpose"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("dim_source.id"))
    external_id: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(Text)  # for-sale / for-rent
    price_aed: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    rent_period: Mapped[str | None] = mapped_column(Text)
    beds: Mapped[int | None] = mapped_column(Integer)
    baths: Mapped[int | None] = mapped_column(Integer)
    size_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    property_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("dim_property_type.id")
    )
    address_id: Mapped[int | None] = mapped_column(ForeignKey("dim_address.id"))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("dim_location.id"))
    area_id: Mapped[int | None] = mapped_column(ForeignKey("dim_area.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("dim_project.id"))
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


# ------------------------------------------------------------------ marts


class MartAreaMonthly(TimestampMixin, Base):
    __tablename__ = "mart_area_monthly"

    area_id: Mapped[int] = mapped_column(ForeignKey("dim_area.id"), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    usage: Mapped[str] = mapped_column(Text, primary_key=True)
    sale_cnt: Mapped[int | None] = mapped_column(Integer)
    sale_median_price_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sale_p25_price_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sale_p75_price_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    rent_cnt: Mapped[int | None] = mapped_column(Integer)
    rent_median_annual_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    gross_yield_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))


class MartProjectMonthly(TimestampMixin, Base):
    __tablename__ = "mart_project_monthly"

    project_id: Mapped[int] = mapped_column(
        ForeignKey("dim_project.id"), primary_key=True
    )
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    usage: Mapped[str] = mapped_column(Text, primary_key=True)
    sale_cnt: Mapped[int | None] = mapped_column(Integer)
    sale_median_price_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sale_p25_price_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    sale_p75_price_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    rent_cnt: Mapped[int | None] = mapped_column(Integer)
    rent_median_annual_m2: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    gross_yield_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))


class MartBuildingSummary(TimestampMixin, Base):
    """Building price summary — deliberately NOT a monthly grain.

    Measured before building it (docs/BUILDING_MART_ANALYSIS.md): at building
    level a monthly grain collapses — 42% of (building, month, usage) cells hold
    exactly ONE sale, and only ~4% clear the default min_sample of 20. A monthly
    mart would have been mostly single-sale "medians" read as market rates.

    So this summarises instead: a trailing-12-month price level, lifetime
    coverage, and a coarse CAGR that stays **NULL** unless the sample genuinely
    supports it. A null we can explain honestly; a noisy number we cannot.

    Sales only, permanently: `fact_rent_contract.building_id` is NULL on all
    10.3M rows because the data.dubai rent export carries no building name.
    There is nothing to join on, so rent / yield / total-return columns cannot
    exist here — see the analysis doc §2 and §6.
    """

    __tablename__ = "mart_building_summary"

    building_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("dim_building.id"), primary_key=True
    )
    usage: Mapped[str] = mapped_column(Text, primary_key=True)

    # --- trailing 12 months: the current price level ---
    sale_cnt_12m: Mapped[int | None] = mapped_column(Integer)
    median_price_m2_12m: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    p25_price_m2_12m: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    p75_price_m2_12m: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))

    # --- lifetime coverage: what we know at all ---
    sale_cnt_total: Mapped[int] = mapped_column(Integer)
    median_price_m2_all: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    first_sale: Mapped[date | None] = mapped_column(Date)
    last_sale: Mapped[date | None] = mapped_column(Date)

    # --- coarse trend; NULL unless span and both anchors clear their floors ---
    price_m2_cagr_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    cagr_years: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    cagr_sample_size: Mapped[int | None] = mapped_column(Integer)

    # strong | thin | insufficient — reliability of the 12-month price level.
    # Independent of the CAGR guard: a building can have a trustworthy current
    # price and no computable trend, or the reverse.
    sample_tier: Mapped[str] = mapped_column(Text)
