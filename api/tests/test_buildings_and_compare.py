"""The four capabilities the MCP tool surface needs (MCP_DESIGN.md §10a).

Two of these encode judgments rather than mechanics, and those are the ones
worth failing a build over:

- A building must **refuse** a yield rather than return null for it. Null reads
  as "no data yet"; the truth is "this can never exist", and an agent that
  confuses the two will keep retrying and eventually report a gap that isn't.
- A cross-type comparison must say the grains differ. An area aggregates every
  property inside it, so placing it beside one project without comment invites
  a false equivalence.
"""

from __future__ import annotations

import pytest

from dxb_api import deps
from dxb_api.errors import ValidationError
from dxb_api.repositories.analytics import BUILDING_NO_INCOME, AnalyticsRepository


class _Repo(AnalyticsRepository):
    """Analytics repository with the two data paths stubbed out.

    Both `_series` (monthly) and `_building_summaries` (summary mart) are
    replaced, so the branching, refusals and caveat assembly are tested without
    a database — which is where the actual decisions live.
    """

    def __init__(self, *, series=None, buildings=None, settings=None):
        self._session = None
        self._settings = settings or _Settings()
        self._stub_series = series or {}
        self._stub_buildings = buildings or {}
        self.resolved = []

    async def _series(self, **kw):
        return self._stub_series

    async def _building_summaries(self, *, ids, usage, min_sample):
        if ids:
            return {
                i: self._stub_buildings[i] for i in ids if i in self._stub_buildings
            }
        return dict(self._stub_buildings)

    async def _resolve_named(self, dimension, value):
        self.resolved.append((dimension, value))
        return (int(value) if value.isdigit() else 1), None

    # Area-code migration helpers (AREA_CODE_MIGRATION_ANALYSIS.md) hit the
    # session directly in the real repository; stubbed here as a no-op
    # identity mapping (no area in this suite is ever superseded), which is
    # exactly the "unaffected areas behave identically to before" case.
    async def resolve_canonical_area_id(self, area_id):
        return area_id

    async def expand_area_ids(self, area_ids):
        return list(area_ids)

    async def superseded_predecessors(self, canonical_area_id):
        return []


class _Settings:
    default_min_sample = 20
    max_entity_ids = 200


def _building(id=1, name="LAKE TERRACE", cagr=7.5, price=12000.0, n=40):
    return {
        "id": id,
        "name_en": name,
        "usage": "Residential",
        "capital_growth_cagr_pct": cagr,
        "median_sale_aed_m2": price,
        "sample_size": n,
        "sample_tier": "strong" if n >= 20 else "thin",
        "income_metrics_unavailable": BUILDING_NO_INCOME,
    }


# ------------------------------------------------- buildings in ranking


async def test_building_ranking_refuses_yield_with_a_reason():
    """Not a null, not an empty list — an error that says the metric cannot
    exist and names the ones that can."""
    repo = _Repo(buildings={1: _building()})
    with pytest.raises(ValidationError) as exc:
        await repo.ranking(entity="building", metric="gross_yield")
    message = str(exc.value)
    assert "no rent contract" in message.lower()
    assert "permanent" in message.lower()


async def test_building_ranking_refuses_total_return_too():
    repo = _Repo(buildings={1: _building()})
    with pytest.raises(ValidationError):
        await repo.ranking(entity="building", metric="total_return")


async def test_building_ranking_supports_capital_growth():
    repo = _Repo(buildings={1: _building(cagr=3.0), 2: _building(id=2, cagr=9.0)})
    result = await repo.ranking(entity="building", metric="capital_growth")
    assert [i["id"] for i in result["items"]] == [2, 1]
    assert result["entity"] == "building"


async def test_building_ranking_carries_the_income_caveat():
    """The refusal is stated even on a successful call, because the agent's
    next question is usually 'and what about yield?'"""
    repo = _Repo(buildings={1: _building()})
    result = await repo.ranking(entity="building", metric="capital_growth")
    assert any("rent contract" in c.lower() for c in result["caveats"])


async def test_building_ranking_excludes_entities_without_the_metric():
    """A null CAGR means the mart's guards were not met; ranking it as zero
    would invent a flat trend."""
    repo = _Repo(buildings={1: _building(cagr=None), 2: _building(id=2, cagr=4.0)})
    result = await repo.ranking(entity="building", metric="capital_growth")
    assert [i["id"] for i in result["items"]] == [2]
    assert result["entities_excluded_insufficient_data"] == 1


async def test_ranking_rejects_unknown_entity_type():
    repo = _Repo()
    with pytest.raises(ValidationError):
        await repo.ranking(entity="developer", metric="capital_growth")


# ------------------------------------------------------ cross-type compare


async def test_mixed_compare_accepts_different_types_together():
    repo = _Repo(
        series={1: {"id": 1, "name_en": "DUBAI MARINA", "points": []}},
        buildings={2: _building(id=2)},
    )
    result = await repo.compare(dimension="mixed", values=["area:1", "building:2"])
    assert [g["entity_type"] for g in result["groups"]] == ["area", "building"]


async def test_mixed_compare_warns_that_grains_differ():
    """The whole point of allowing it is the project-vs-its-area question, and
    that comparison is only honest if the scope difference is stated."""
    repo = _Repo(
        series={1: {"id": 1, "name_en": "X", "points": []}},
    )
    result = await repo.compare(dimension="mixed", values=["area:1", "project:1"])
    assert any("different scopes" in c for c in result["caveats"])


async def test_single_type_mixed_compare_does_not_warn():
    """No false alarm when the types happen to match."""
    repo = _Repo(series={1: {"id": 1, "name_en": "X", "points": []}})
    result = await repo.compare(dimension="mixed", values=["area:1", "area:2"])
    assert not any("different scopes" in c for c in result["caveats"])


async def test_mixed_compare_rejects_unprefixed_values():
    repo = _Repo()
    with pytest.raises(ValidationError) as exc:
        await repo.compare(dimension="mixed", values=["Dubai Marina"])
    assert "type:name" in str(exc.value)


async def test_mixed_compare_rejects_unknown_type_prefix():
    repo = _Repo()
    with pytest.raises(ValidationError):
        await repo.compare(dimension="mixed", values=["developer:5"])


async def test_mixed_compare_rejects_empty_value_after_colon():
    repo = _Repo()
    with pytest.raises(ValidationError):
        await repo.compare(dimension="mixed", values=["area:"])


async def test_building_in_compare_carries_the_income_caveat():
    repo = _Repo(buildings={2: _building(id=2)})
    result = await repo.compare(dimension="mixed", values=["building:2"])
    assert any("rent contract" in c.lower() for c in result["caveats"])


async def test_building_below_min_sample_withholds_the_price_level():
    """Thin evidence is suppressed rather than quoted — the same rule the
    monthly path applies, so a cross-type comparison stays consistent."""
    repo = _Repo(buildings={2: _building(id=2, n=3)})
    result = await repo.compare(dimension="mixed", values=["building:2"], min_sample=20)
    group = result["groups"][0]
    assert group["median_sale_aed_m2"] is None
    assert "below the requested min_sample" in group["note"]


async def test_building_with_no_sales_says_so():
    repo = _Repo(buildings={})
    result = await repo.compare(dimension="mixed", values=["building:9"])
    assert "No sales recorded" in result["groups"][0]["note"]


async def test_existing_single_dimension_compare_still_works():
    """The new dimension is additive: usage/area/project behave as before."""
    repo = _Repo(series={1: {"id": 1, "name_en": "X", "points": []}})
    result = await repo.compare(dimension="area", values=["1", "2"])
    assert result["dimension"] == "area"
    assert len(result["groups"]) == 2


# ------------------------------------------- response_model field survival


def test_ranking_response_model_keeps_the_building_honesty_fields():
    """FastAPI's response_model silently drops any field it does not declare.

    That is fine for noise and fatal for `sample_tier`: it is the signal that a
    building's price level rests on three sales rather than three hundred, and
    without it the two are presented identically. Found live — the repository
    was returning the fields correctly and the schema was eating them.
    """
    from dxb_api.schemas.analytics import RankedEntity

    declared = set(RankedEntity.model_fields)
    for field in (
        "sample_size",
        "sample_tier",
        "cagr_sample_size",
        "income_metrics_unavailable",
    ):
        assert field in declared, f"response_model would silently drop {field}"


def test_building_ranking_fields_survive_serialization():
    """The end-to-end version of the above: build a real payload and confirm
    the fields are still there after the model round-trips it."""
    from dxb_api.schemas.analytics import RankedEntity

    serialized = RankedEntity.model_validate(
        {**_building(), "id": 1, "name_en": "LAKE TERRACE"}
    ).model_dump()
    assert serialized["sample_tier"] == "strong"
    assert serialized["sample_size"] == 40
    assert "rent contract" in serialized["income_metrics_unavailable"].lower()


# --------------------------------------------------------- router wiring


class FakeFactsWithBuilding:
    def __init__(self):
        self.kwargs = None

    async def list_transactions(self, **kw):
        self.kwargs = kw
        return {
            "items": [],
            "limit": 100,
            "offset": 0,
            "has_more": False,
            "total": None,
        }


def test_building_id_reaches_the_transactions_repository(client, override):
    fake = FakeFactsWithBuilding()
    override(deps.FactRepoDep, fake)
    client.get("/facts/transactions?building_id=77")
    assert fake.kwargs["building_id"] == 77


def test_building_id_alone_satisfies_the_selectivity_requirement(client, override):
    """It is an indexed FK, so it is a legitimate narrowing filter on its own —
    the endpoint must not still demand an area or a date."""
    override(deps.FactRepoDep, FakeFactsWithBuilding())
    assert client.get("/facts/transactions?building_id=77").status_code == 200
