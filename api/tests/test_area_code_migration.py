"""Area-code migration (docs/AREA_CODE_MIGRATION_ANALYSIS.md), revision 2.

DLD started re-coding ~89 established communities under new area codes from
2026-07-20 onward, and running the detector against live data found this is a
SUBDIVISION, not a rename: 21 of 48 split old areas fan out into more than one
new code (old code 20 "MARSA DUBAI" alone splits into 4). So there is no
scalar "canonical id" on `dim_area` to redirect through — `AreaCodeEvidence`
(`reviewed = true` rows) *is* the old->new relationship, one-to-many.

Three cases per old area id, exercised throughout this file:
  0 reviewed successors -> not a split area at all: itself.
  1 reviewed successor  -> unambiguous: redirects transparently.
  2+ reviewed successors -> ambiguous: `AmbiguousEntityError`, naming every
                            successor, rather than a guess or a silent partial
                            answer.

These tests use small in-memory fakes that evaluate the real SQLAlchemy
`Select` objects the repositories build — no real DB or network, matching the
existing pattern in test_routers.py's
`test_missing_ids_are_computed_across_the_whole_result_not_the_page`.

Fixture world, used throughout:
  - area 20  "MARSA DUBAI"        -> sole reviewed successor: 292
  - area 292 "DUBAI MARINA" (canonical, unambiguous successor of 20)
  - area 500 "STANDALONE AREA"     -> never split (canonical-only)
  - area 600 "OLD SPLIT AREA"      -> TWO reviewed successors: 701, 702
  - area 701 "SPLIT AREA A" (one of 600's successors)
  - area 702 "SPLIT AREA B" (the other)
"""

from __future__ import annotations

import re
from datetime import date
from types import SimpleNamespace

import pytest

from dxb_api.errors import AmbiguousEntityError
from dxb_api.repositories.analytics import AnalyticsRepository
from dxb_api.repositories.base import BaseRepository
from dxb_api.repositories.dimensions import DimensionRepository
from dxb_api.repositories.facts import FactRepository

# project_id -> (old_area_id, new_area_id): the read-time indirection table.
# Both entries are reviewed (see EVIDENCE, defined below) — the detector's
# per-code project sets are disjoint by construction, so a project resolves
# to exactly one place, never ambiguously.
PROJECT_AREA_ACTUAL = {
    9001: (600, 701),  # historically filed under old 600, resolves to 701
    9002: (600, 702),  # historically filed under old 600, resolves to 702
}

# id -> (dld_area_code, name_en)
AREA_TABLE = {
    20: ("A-020", "MARSA DUBAI"),
    292: ("C-292", "DUBAI MARINA"),
    500: ("A-500", "STANDALONE AREA"),
    600: ("A-600", "OLD SPLIT AREA"),
    701: ("C-701", "SPLIT AREA A"),
    702: ("C-702", "SPLIT AREA B"),
}

# (old_area_id, new_area_id) -> reviewed. All reviewed here; nothing in these
# tests exercises an *unreviewed* row (revision 1 already covered "reviewed
# gates trust" at the evidence-table level, unchanged in spirit here).
EVIDENCE: dict[tuple[int, int], bool] = {
    (20, 292): True,
    (600, 701): True,
    (600, 702): True,
}


class _Settings:
    default_page_limit = 50
    max_page_limit = 1000
    max_entity_ids = 200
    default_min_sample = 1
    trgm_threshold = 0.3
    trgm_ambiguity_margin = 0.05


def _ints(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"-?\d+", text)]


def _reviewed_successors(old_id: int) -> list[int]:
    return sorted(
        new for (old, new), reviewed in EVIDENCE.items() if reviewed and old == old_id
    )


def _evidence_execute(sql: str):
    """Shared dispatch for the two `area_code_evidence`-joined query shapes
    `BaseRepository`'s area-code-migration helpers issue, against the module
    fixtures above:

      - `_successors_map`: joins `dim_area` on `new_area_id` -- "every
        reviewed successor of these old ids".
      - `_sole_predecessors`: joins `dim_area` on `old_area_id` -- "every old
        area (with its own code/name) pointing at these new ids".
    """
    if "dim_area_1.id = area_code_evidence.new_area_id" in sql:
        m = re.search(r"old_area_id IN \(([^)]*)\)", sql)
        wanted = set(_ints(m.group(1))) if m else set()
        rows = [
            SimpleNamespace(
                old_area_id=old,
                id=new,
                dld_area_code=AREA_TABLE[new][0],
                name_en=AREA_TABLE[new][1],
            )
            for (old, new), reviewed in EVIDENCE.items()
            if reviewed and old in wanted
        ]
        return SimpleNamespace(all=lambda: rows)

    if "dim_area_1.id = area_code_evidence.old_area_id" in sql:
        m = re.search(r"new_area_id IN \(([^)]*)\)", sql)
        wanted = set(_ints(m.group(1))) if m else set()
        rows = [
            SimpleNamespace(
                old_area_id=old,
                new_area_id=new,
                dld_area_code=AREA_TABLE[old][0],
                name_en=AREA_TABLE[old][1],
            )
            for (old, new), reviewed in EVIDENCE.items()
            if reviewed and new in wanted
        ]
        return SimpleNamespace(all=lambda: rows)

    raise AssertionError(f"unexpected evidence-table query shape: {sql}")


class FakeAreaSession:
    """Backs `BaseRepository`'s area-code-migration helpers directly."""

    def _sql(self, stmt) -> str:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    async def execute(self, stmt):
        return _evidence_execute(self._sql(stmt))


# ------------------------------------------------------------------ item 1
# expand_area_ids / resolve_canonical_area_id / superseded_predecessors /
# successors_of -- and the new ambiguity behaviour


async def test_expand_area_ids_resolves_old_id_to_include_canonical_and_siblings():
    repo = BaseRepository(FakeAreaSession(), _Settings())
    assert await repo.expand_area_ids([20]) == [20, 292]


async def test_expand_area_ids_from_the_canonical_id_returns_the_same_set():
    """Requesting either code returns the identical combined set."""
    repo = BaseRepository(FakeAreaSession(), _Settings())
    assert await repo.expand_area_ids([292]) == [20, 292]


async def test_expand_area_ids_for_an_unsplit_area_returns_just_itself():
    """Regression: an area that was never split behaves exactly as before —
    no unexpected expansion for the ~all areas untouched by the migration."""
    repo = BaseRepository(FakeAreaSession(), _Settings())
    assert await repo.expand_area_ids([500]) == [500]


async def test_expand_area_ids_deduplicates_and_sorts_a_mixed_batch():
    repo = BaseRepository(FakeAreaSession(), _Settings())
    assert await repo.expand_area_ids([20, 292, 500]) == [20, 292, 500]


async def test_resolve_canonical_area_id():
    repo = BaseRepository(FakeAreaSession(), _Settings())
    assert await repo.resolve_canonical_area_id(20) == 292
    assert await repo.resolve_canonical_area_id(292) == 292
    assert await repo.resolve_canonical_area_id(500) == 500


async def test_superseded_predecessors():
    repo = BaseRepository(FakeAreaSession(), _Settings())
    assert await repo.superseded_predecessors(292) == [
        {"id": 20, "dld_area_code": "A-020", "name_en": "MARSA DUBAI"}
    ]
    assert await repo.superseded_predecessors(500) == []


async def test_successors_of_lists_the_full_set_even_for_an_unambiguous_old_area():
    repo = BaseRepository(FakeAreaSession(), _Settings())
    assert await repo.successors_of(20) == [
        {"id": 292, "dld_area_code": "C-292", "name_en": "DUBAI MARINA"}
    ]
    assert await repo.successors_of(500) == []


# -------------------------------------------- the new one-to-many behaviour


async def test_successors_of_a_many_successor_old_area_lists_every_successor():
    repo = BaseRepository(FakeAreaSession(), _Settings())
    result = await repo.successors_of(600)
    assert {r["id"] for r in result} == {701, 702}
    assert {r["dld_area_code"] for r in result} == {"C-701", "C-702"}


async def test_superseded_predecessors_excludes_a_many_successor_old_area():
    """601 is only ONE of two successors of 600 -- 600 does not exclusively
    belong to 701 (or 702), so neither may list it as their predecessor."""
    repo = BaseRepository(FakeAreaSession(), _Settings())
    assert await repo.superseded_predecessors(701) == []
    assert await repo.superseded_predecessors(702) == []


async def test_resolve_canonical_area_id_raises_for_a_many_successor_old_area():
    repo = BaseRepository(FakeAreaSession(), _Settings())
    with pytest.raises(AmbiguousEntityError) as exc_info:
        await repo.resolve_canonical_area_id(600)
    err = exc_info.value
    assert err.details["old_area_id"] == 600
    assert {c["id"] for c in err.details["candidates"]} == {701, 702}


async def test_expand_area_ids_raises_for_a_many_successor_old_area():
    repo = BaseRepository(FakeAreaSession(), _Settings())
    with pytest.raises(AmbiguousEntityError) as exc_info:
        await repo.expand_area_ids([600])
    assert exc_info.value.details["old_area_id"] == 600


async def test_expand_area_ids_raises_even_when_mixed_with_unambiguous_ids():
    """The ambiguity is not swallowed just because other ids in the same
    batch resolve fine."""
    repo = BaseRepository(FakeAreaSession(), _Settings())
    with pytest.raises(AmbiguousEntityError):
        await repo.expand_area_ids([20, 500, 600])


# ------------------------------------------------------------------ item 2
# facts.py: the single most important test — combined result, no duplicates


SALE_ROWS = [
    SimpleNamespace(_mapping={"id": 1, "area_id": 20, "txn_date": date(2026, 1, 5)}),
    SimpleNamespace(_mapping={"id": 2, "area_id": 20, "txn_date": date(2026, 2, 5)}),
    SimpleNamespace(_mapping={"id": 3, "area_id": 20, "txn_date": date(2026, 3, 5)}),
    SimpleNamespace(_mapping={"id": 4, "area_id": 292, "txn_date": date(2026, 7, 25)}),
    SimpleNamespace(_mapping={"id": 5, "area_id": 292, "txn_date": date(2026, 7, 26)}),
    # Control group: an unrelated, unsplit area — must never leak in.
    SimpleNamespace(_mapping={"id": 6, "area_id": 500, "txn_date": date(2026, 1, 1)}),
    SimpleNamespace(_mapping={"id": 7, "area_id": 500, "txn_date": date(2026, 1, 2)}),
]

RENT_ROWS = [
    SimpleNamespace(
        _mapping={"id": 101, "area_id": 20, "start_date": date(2026, 1, 1)}
    ),
    SimpleNamespace(
        _mapping={"id": 102, "area_id": 292, "start_date": date(2026, 7, 20)}
    ),
    SimpleNamespace(
        _mapping={"id": 103, "area_id": 500, "start_date": date(2026, 1, 1)}
    ),
]


def _filter_by_area(rows: list, sql: str) -> list:
    m = re.search(r"area_id IN \(([^)]*)\)", sql, re.IGNORECASE)
    wanted = {int(x) for x in re.findall(r"-?\d+", m.group(1))} if m else None
    if wanted is None:
        return list(rows)
    return [r for r in rows if r._mapping["area_id"] in wanted]


class FakeFactsSession:
    """`FactRepository`'s own queries (fact_sale_transaction /
    fact_rent_contract, joined to dim_area etc.) plus the area-code-evidence
    queries its internal `expand_area_ids([area_id])` call issues.
    """

    def _sql(self, stmt) -> str:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    async def execute(self, stmt):
        sql = self._sql(stmt)
        if "fact_sale_transaction" in sql:
            return SimpleNamespace(all=lambda: _filter_by_area(SALE_ROWS, sql))
        if "fact_rent_contract" in sql:
            return SimpleNamespace(all=lambda: _filter_by_area(RENT_ROWS, sql))
        return _evidence_execute(sql)


async def test_transactions_by_old_or_new_area_id_return_the_same_combined_set():
    """The single most important test: querying by either the pre-migration
    or post-migration code must return the full combined set of underlying
    sales, each counted exactly once — never unioned, never duplicated."""
    repo = FactRepository(FakeFactsSession(), _Settings())

    by_old_code = await repo.list_transactions(area_id=20)
    by_new_code = await repo.list_transactions(area_id=292)

    old_ids = sorted(item["id"] for item in by_old_code["items"])
    new_ids = sorted(item["id"] for item in by_new_code["items"])

    assert old_ids == new_ids == [1, 2, 3, 4, 5]
    # No duplication: five underlying rows, five distinct ids back.
    assert len(old_ids) == len(set(old_ids)) == 5


async def test_transactions_by_an_unaffected_area_are_unchanged():
    """Regression: an ordinary, never-split area still returns exactly its
    own rows — the migration changes nothing for it."""
    repo = FactRepository(FakeFactsSession(), _Settings())
    result = await repo.list_transactions(area_id=500)
    assert sorted(item["id"] for item in result["items"]) == [6, 7]


async def test_rents_by_old_or_new_area_id_return_the_same_combined_set_once_each():
    """Rents are unaffected by the migration today (Ejari has not adopted the
    new codes) but the redirect applies unconditionally, per direction — no
    special-casing."""
    repo = FactRepository(FakeFactsSession(), _Settings())

    by_old_code = await repo.list_rents(area_id=20)
    by_new_code = await repo.list_rents(area_id=292)

    old_ids = sorted(item["id"] for item in by_old_code["items"])
    new_ids = sorted(item["id"] for item in by_new_code["items"])

    assert old_ids == new_ids == [101, 102]
    assert len(old_ids) == len(set(old_ids)) == 2


async def test_transactions_by_a_many_successor_old_area_raise_ambiguous():
    """The ambiguity error applies to every area-scoped endpoint, not just
    dimension lookup — facts included."""
    repo = FactRepository(FakeFactsSession(), _Settings())
    with pytest.raises(AmbiguousEntityError) as exc_info:
        await repo.list_transactions(area_id=600)
    assert exc_info.value.details["old_area_id"] == 600


async def test_rents_by_a_many_successor_old_area_raise_ambiguous():
    repo = FactRepository(FakeFactsSession(), _Settings())
    with pytest.raises(AmbiguousEntityError):
        await repo.list_rents(area_id=600)


# ------------------------------------------------------------------ item 3
# dimensions.py: get_area lineage fields


DIM_AREA_ROWS = {
    20: dict(
        id=20,
        dld_area_code="A-020",
        name_en="MARSA DUBAI",
        name_ar=None,
        zone_name=None,
        geo_match_method="name_hint",
        has_centroid=True,
        has_boundary=False,
    ),
    292: dict(
        id=292,
        dld_area_code="C-292",
        name_en="DUBAI MARINA",
        name_ar=None,
        zone_name=None,
        geo_match_method="exact",
        has_centroid=True,
        has_boundary=True,
    ),
    500: dict(
        id=500,
        dld_area_code="A-500",
        name_en="STANDALONE AREA",
        name_ar=None,
        zone_name=None,
        geo_match_method="exact",
        has_centroid=True,
        has_boundary=True,
    ),
    600: dict(
        id=600,
        dld_area_code="A-600",
        name_en="OLD SPLIT AREA",
        name_ar=None,
        zone_name=None,
        geo_match_method="name_hint",
        has_centroid=True,
        has_boundary=False,
    ),
}


class FakeDimAreaSession:
    def _sql(self, stmt) -> str:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    async def execute(self, stmt):
        sql = self._sql(stmt)
        if "has_centroid" in sql:
            # DimensionRepository._area_cols() full lookup, by id.
            area_id = _ints(sql.split("WHERE", 1)[1])[0]
            row = DIM_AREA_ROWS.get(area_id)
            result = SimpleNamespace(**row) if row else None
            return SimpleNamespace(first=lambda: result)
        return _evidence_execute(sql)


async def test_get_area_on_a_canonical_area_lists_its_superseded_predecessor():
    repo = DimensionRepository(FakeDimAreaSession(), _Settings())
    result = await repo.get_area(292)
    assert result["superseded_areas"] == [
        {"id": 20, "dld_area_code": "A-020", "name_en": "MARSA DUBAI"}
    ]
    assert result["superseded_by"] is None


async def test_get_area_on_an_old_area_points_to_its_successor_without_hiding_itself():
    repo = DimensionRepository(FakeDimAreaSession(), _Settings())
    result = await repo.get_area(20)
    # The lookup is annotated, not redirected: own id/name/code unchanged.
    assert result["id"] == 20
    assert result["name_en"] == "MARSA DUBAI"
    assert result["dld_area_code"] == "A-020"
    # superseded_by is a LIST now, even for the unambiguous case.
    assert result["superseded_by"] == [
        {"id": 292, "dld_area_code": "C-292", "name_en": "DUBAI MARINA"}
    ]
    assert result["superseded_areas"] == []


async def test_get_area_on_an_unsplit_area_has_empty_lineage_both_ways():
    repo = DimensionRepository(FakeDimAreaSession(), _Settings())
    result = await repo.get_area(500)
    assert result["superseded_areas"] == []
    assert result["superseded_by"] is None


async def test_get_area_on_a_many_successor_old_area_lists_every_successor():
    """The one genuinely new lineage case: an old area with several current
    successors returns all of them as a list, and still does not raise —
    only the aggregated-figures endpoints (facts/marts/analytics) raise;
    dimension lookup of the old id itself always works, for audit/history."""
    repo = DimensionRepository(FakeDimAreaSession(), _Settings())
    result = await repo.get_area(600)
    assert result["id"] == 600
    assert result["name_en"] == "OLD SPLIT AREA"
    assert {s["id"] for s in result["superseded_by"]} == {701, 702}
    assert result["superseded_areas"] == []


# ------------------------------------------------------------------ item 4
# analytics.py: ranking dedup + growth/compare caveats + the ambiguity error


MART_AREA_ROWS = [
    # raw_area_id, month, sale_median_price_m2, sale_cnt, rent_median_m2, rent_cnt
    (20, date(2026, 5, 1), 15000.0, 40, 90000.0, 10),
    (20, date(2026, 6, 1), 15200.0, 35, 91000.0, 9),
    (292, date(2026, 7, 1), 15600.0, 5, None, 0),
    (500, date(2026, 5, 1), 8000.0, 30, 40000.0, 8),
    (500, date(2026, 6, 1), 8100.0, 28, 40500.0, 7),
    # A many-successor old area's small, honest residual — must never rank
    # as its own row (belt-and-suspenders dim_area exclusion).
    (600, date(2026, 5, 1), 20000.0, 3, None, 0),
]


def _mart_area_monthly_rows(sql: str) -> list:
    ids_match = re.search(r"mart_area_monthly\.area_id IN \(([^)]*)\)", sql)
    wanted = set(_ints(ids_match.group(1))) if ids_match else None
    # `_exclude_ambiguous_old_areas` (used by ranking's `ids is None` path)
    # is a GROUP BY ... HAVING count() > 1 query -- distinct from the broader
    # `_exclude_superseded_areas` (all old ids, no HAVING) used elsewhere.
    # Only a 2+-successor old area is dropped outright here; a 1-successor
    # old area's row must still canonicalize and merge into its successor's
    # bucket normally, not be excluded.
    exclude_ambiguous = "HAVING" in sql and "area_code_evidence" in sql
    ambiguous_old_ids = {
        old for old in {o for (o, _n) in EVIDENCE} if len(_reviewed_successors(old)) > 1
    }

    rows = []
    for raw_id, month, sale_med, sale_cnt, rent_med, rent_cnt in MART_AREA_ROWS:
        if wanted is not None and raw_id not in wanted:
            continue
        if exclude_ambiguous and raw_id in ambiguous_old_ids:
            continue
        successors = _reviewed_successors(raw_id)
        canonical = successors[0] if len(successors) == 1 else raw_id
        rows.append(
            SimpleNamespace(
                entity_id=canonical,
                name_en=AREA_TABLE[canonical][1],
                month=month,
                sale_median_price_m2=sale_med,
                sale_cnt=sale_cnt,
                rent_median_annual_m2=rent_med,
                rent_cnt=rent_cnt,
            )
        )
    return rows


class FakeAnalyticsSession:
    """Serves both the area-code-evidence migration-resolution queries and
    the `mart_area_monthly` canonical-join series query `AnalyticsRepository`
    issues, canonicalizing raw area ids in Python exactly as the real
    self-join (on each row's sole reviewed successor) does.
    """

    def _sql(self, stmt) -> str:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    async def execute(self, stmt):
        sql = self._sql(stmt)
        if "mart_area_monthly" in sql:
            return SimpleNamespace(all=lambda: _mart_area_monthly_rows(sql))
        return _evidence_execute(sql)


async def test_ranking_never_returns_a_superseded_area_as_its_own_row():
    """Old code 20's two mart rows fold into canonical 292's bucket — 20 must
    never surface as its own ranked entity, and 292 must appear exactly once,
    not once per underlying raw code. A many-successor old area's residual
    (600) must never rank as its own row either."""
    repo = AnalyticsRepository(FakeAnalyticsSession(), _Settings())
    result = await repo.ranking(entity="area", metric="sale_price_m2", min_sample=1)

    ids = [item["id"] for item in result["items"]]
    assert 20 not in ids
    assert 600 not in ids
    assert ids.count(292) == 1
    assert 500 in ids  # unaffected area still ranks normally


async def test_growth_for_a_split_area_carries_the_merge_caveat_and_combined_data():
    repo = AnalyticsRepository(FakeAnalyticsSession(), _Settings())

    by_old = await repo.growth(entity="area", entity_id=20, min_sample=1)
    by_new = await repo.growth(entity="area", entity_id=292, min_sample=1)

    # Both resolve to the canonical id and see the same combined series.
    assert by_old["id"] == by_new["id"] == 292
    assert by_old["months_covered"] == by_new["months_covered"] == 3
    assert any("AREA_CODE_MIGRATION_ANALYSIS.md" in c for c in by_old["caveats"])
    assert any("A-020" in c or "MARSA DUBAI" in c for c in by_old["caveats"])


async def test_growth_for_an_unsplit_area_has_no_merge_caveat():
    """Only add the caveat when it is actually relevant."""
    repo = AnalyticsRepository(FakeAnalyticsSession(), _Settings())
    result = await repo.growth(entity="area", entity_id=500, min_sample=1)
    assert result["id"] == 500
    assert not any("AREA_CODE_MIGRATION_ANALYSIS.md" in c for c in result["caveats"])


async def test_growth_for_a_many_successor_old_area_raises_ambiguous():
    """The merge caveat only ever fires for the unambiguous case — a
    many-successor old area raises before caveat-assembly is ever reached."""
    repo = AnalyticsRepository(FakeAnalyticsSession(), _Settings())
    with pytest.raises(AmbiguousEntityError) as exc_info:
        await repo.growth(entity="area", entity_id=600, min_sample=1)
    assert exc_info.value.details["old_area_id"] == 600


async def test_compare_area_group_carries_the_merge_caveat_when_relevant():
    repo = AnalyticsRepository(FakeAnalyticsSession(), _Settings())
    result = await repo.compare(dimension="area", values=["20", "500"], min_sample=1)

    merge_lines = [
        c for c in result["caveats"] if "AREA_CODE_MIGRATION_ANALYSIS.md" in c
    ]
    assert len(merge_lines) == 1  # one line, not one per group

    by_value = {g["value"]: g for g in result["groups"]}
    assert by_value["20"]["id"] == 292  # resolved to canonical
    assert by_value["500"]["id"] == 500
    # The internal marker never leaks into the response.
    assert "_merge_caveat" not in by_value["20"]


async def test_compare_area_group_raises_ambiguous_for_a_many_successor_old_area():
    repo = AnalyticsRepository(FakeAnalyticsSession(), _Settings())
    with pytest.raises(AmbiguousEntityError):
        await repo.compare(dimension="area", values=["600"], min_sample=1)


# ------------------------------------------------------------------ item 5
# facts.py: project-anchored recovery for one successor of an ambiguous old
# area — the genuinely new piece from the "resolve through the project
# first" correction. Old area 600 itself always raises (tested above), but
# querying ONE of its successors (701) must still recover 600's pre-migration
# rows for the specific projects the detector confirmed belong to 701 —
# something the area-level widening alone cannot do, since 600 is never
# treated as 701's "sole predecessor".

SALE_ROWS_PROJECT_ANCHORED = [
    # Pre-migration rows, historically filed under old area 600 (never
    # rewritten), but whose PROJECT the detector has confirmed belongs to 701.
    SimpleNamespace(
        _mapping={
            "id": 201,
            "area_id": 600,
            "project_id": 9001,
            "txn_date": date(2026, 6, 1),
        }
    ),
    # Same old area, but project 9002 -- confirmed to belong to 702, not 701.
    SimpleNamespace(
        _mapping={
            "id": 202,
            "area_id": 600,
            "project_id": 9002,
            "txn_date": date(2026, 6, 2),
        }
    ),
    # Already filed directly under the new code (post-migration).
    SimpleNamespace(
        _mapping={
            "id": 203,
            "area_id": 701,
            "project_id": 9001,
            "txn_date": date(2026, 7, 25),
        }
    ),
    # Old-area row with a project the detector never flagged at all.
    SimpleNamespace(
        _mapping={
            "id": 204,
            "area_id": 600,
            "project_id": 9099,
            "txn_date": date(2026, 6, 3),
        }
    ),
]


def _filter_project_anchored(rows: list, sql: str) -> list:
    """Evaluate the `_area_scope_filter` OR condition
    (`f.area_id IN (...) OR f.project_id IN (project_area_actual subquery)`)
    against the fixture rows above, exactly as Postgres would.
    """
    area_match = re.search(r"fact_sale_transaction\.area_id IN \(([^)]*)\)", sql)
    expanded = set(_ints(area_match.group(1))) if area_match else set()
    proj_match = re.search(r"project_area_actual\.new_area_id = (-?\d+)", sql)
    canonical = int(proj_match.group(1)) if proj_match else None
    mapped_projects = (
        {
            pid
            for pid, (old, new) in PROJECT_AREA_ACTUAL.items()
            if new == canonical and EVIDENCE.get((old, new), False)
        }
        if canonical is not None
        else set()
    )
    return [
        r
        for r in rows
        if r._mapping["area_id"] in expanded
        or r._mapping["project_id"] in mapped_projects
    ]


class FakeProjectAnchoredFactsSession:
    def _sql(self, stmt) -> str:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    async def execute(self, stmt):
        sql = self._sql(stmt)
        if "fact_sale_transaction" in sql:
            return SimpleNamespace(
                all=lambda: _filter_project_anchored(SALE_ROWS_PROJECT_ANCHORED, sql)
            )
        return _evidence_execute(sql)


async def test_transactions_for_one_successor_recover_project_anchored_old_rows():
    """Querying 701 (one of 600's two successors) must recover 201 (old-code
    600, project 9001 -> confirmed 701) and the direct 203, but NOT 202
    (project 9002 -> confirmed 702, a sibling successor) nor 204 (old-code
    600, project never flagged by the detector)."""
    repo = FactRepository(FakeProjectAnchoredFactsSession(), _Settings())
    result = await repo.list_transactions(area_id=701)
    ids = sorted(item["id"] for item in result["items"])
    assert ids == [201, 203]


async def test_transactions_for_the_sibling_successor_recover_its_own_project():
    repo = FactRepository(FakeProjectAnchoredFactsSession(), _Settings())
    result = await repo.list_transactions(area_id=702)
    ids = sorted(item["id"] for item in result["items"])
    assert ids == [202]


# ------------------------------------------------------------------ item 6
# dimensions.py: list_projects' new area_id filter -- OR'd with
# `project_area_actual` indirection, never ambiguous, never `expand_area_ids`
# (docs/AREA_CODE_MIGRATION_ANALYSIS.md, "OR'd with indirection, never
# ambiguous" -- the corrected shape for list_projects/resolve_project/
# list_buildings/resolve_building).

PROJECTS = {
    # id -> (name_en, area_id) -- area_id is the raw, literal, never-rewritten
    # column value.
    9001: ("MARINA TOWER OLD", 600),  # filed under old 600; detector -> 701
    9003: ("NEW MARINA PROJECT", 701),  # already filed under 701 directly
    9004: ("UNRELATED PROJECT", 500),
}

_PROJECT_DEFAULTS = dict(
    dld_project_number=None,
    name_ar=None,
    master_project_id=None,
    master_project_en=None,
    is_master=False,
    developer_id=None,
    status=None,
    project_type=None,
    percent_completed=None,
    cnt_units=None,
    completion_date=None,
    geo_match_method=None,
    geo_building_count=None,
    geo_spread_m=None,
    has_location=False,
)


def _list_projects_execute(sql: str):
    lit_m = re.search(r"dim_project\.area_id = (-?\d+)", sql)
    literal_area = int(lit_m.group(1)) if lit_m else None
    ind_m = re.search(r"project_area_actual\.new_area_id = (-?\d+)", sql)
    indirect_area = int(ind_m.group(1)) if ind_m else None

    rows = []
    for pid, (name, area_id) in PROJECTS.items():
        matched = literal_area is not None and area_id == literal_area
        if not matched and indirect_area is not None:
            mapping = PROJECT_AREA_ACTUAL.get(pid)
            if mapping and mapping[1] == indirect_area and EVIDENCE.get(mapping):
                matched = True
        if matched:
            rows.append(
                SimpleNamespace(
                    id=pid, name_en=name, area_id=area_id, **_PROJECT_DEFAULTS
                )
            )
    return rows


class FakeProjectsSession:
    def _sql(self, stmt) -> str:
        return str(stmt.compile(compile_kwargs={"literal_binds": True}))

    async def execute(self, stmt):
        sql = self._sql(stmt)
        if "dim_project" in sql:
            return SimpleNamespace(all=lambda: _list_projects_execute(sql))
        return _evidence_execute(sql)

    async def scalar(self, stmt):
        # list_projects' trailing `_count` query -- not the point of this
        # test, just needs to not blow up.
        return len(_list_projects_execute(self._sql(stmt)))


async def test_list_projects_by_new_area_id_includes_project_area_actual_matches():
    """9001 is literally filed under old 600 but the detector has confirmed
    (reviewed) it belongs to 701 -- querying 701 must return both it and the
    literally-matched 9003, deduplicated, never `expand_area_ids`, never an
    ambiguity error even though 600 (its literal area) has two successors."""
    repo = DimensionRepository(FakeProjectsSession(), _Settings())
    result = await repo.list_projects(area_id=701)
    ids = sorted(item["id"] for item in result["items"])
    assert ids == [9001, 9003]


async def test_list_projects_by_old_area_id_is_a_plain_literal_snapshot():
    """Querying the OLD id directly stays a plain, literal match -- no OR, no
    indirection, no ambiguity error despite 600 having two successors."""
    repo = DimensionRepository(FakeProjectsSession(), _Settings())
    result = await repo.list_projects(area_id=600)
    ids = sorted(item["id"] for item in result["items"])
    assert ids == [9001]
