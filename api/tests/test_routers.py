"""Router behaviour with fake repositories.

Faking at the repository boundary is possible precisely because routers are
handed repository *instances* (API_DESIGN.md §3) — there is no session or SQL
to stub out.
"""

from __future__ import annotations

from datetime import date

import pytest

from dxb_api import deps
from dxb_api.errors import AmbiguousEntityError, NotFoundError, ValidationError


class FakeDimensions:
    def __init__(self):
        self.calls = []

    async def list_areas(self, **kw):
        self.calls.append(("list_areas", kw))
        return {
            "items": [
                {
                    "id": 1,
                    "dld_area_code": "A-1",
                    "name_en": "DUBAI MARINA",
                    "name_ar": None,
                    "zone_name": None,
                    "geo_match_method": "exact",
                    "has_geo_data": True,
                    "has_boundary": True,
                }
            ],
            "limit": 100,
            "offset": 0,
            "has_more": False,
            "total": 1,
        }

    async def get_area(self, area_id):
        if area_id != 1:
            raise NotFoundError(f"No area with id {area_id}", area_id=area_id)
        return (await self.list_areas())["items"][0]

    async def list_usages(self):
        return [{"usage": "Residential", "property_type_count": 12}]

    async def resolve_area(self, q):
        if q == "ambiguous":
            raise AmbiguousEntityError("too close", query=q, candidates=[])
        return 1, {
            "query": q,
            "id": 1,
            "name_en": "DUBAI MARINA",
            "similarity": 0.54,
            "runners_up": [],
        }


class FakeBuildings:
    def __init__(self):
        self.kwargs = None

    async def list_buildings(self, **kw):
        self.kwargs = kw
        return {
            "items": [
                {
                    "id": 1,
                    "name_en": "LAKE TERRACE",
                    "name_ar": None,
                    "area_id": 5,
                    "area_name_en": "AL THANYAH FIFTH",
                    "project_id": 9,
                    "project_name_en": "LAKE TERRACE",
                    "geo_match_method": "makani_validated",
                    "is_precise": True,
                    "has_geo_data": True,
                    "built_up_area": None,
                    "floors": 14,
                    "flats": None,
                    "offices": None,
                    "shops": None,
                    "car_parks": None,
                    "elevators": None,
                    "swimming_pools": None,
                    "is_free_hold": None,
                    "rooms": "3 B/R",
                }
            ],
            "limit": 100,
            "offset": 0,
            "has_more": False,
            "total": 1,
        }


class FakeMarts:
    def __init__(self):
        self.kwargs = None

    async def area_monthly(self, **kw):
        self.kwargs = kw
        return {
            "items": [],
            "limit": 100,
            "offset": 0,
            "has_more": False,
            "total": None,
            "applied": {
                "min_sample": kw.get("min_sample"),
                "include_future": kw.get("include_future", False),
            },
            "requested_ids": kw.get("area_ids"),
            "returned_ids": [],
            "missing_ids": kw.get("area_ids") or [],
        }


class FakeFacts:
    async def list_transactions(self, **kw):
        if not any(
            kw.get(k) is not None for k in ("area_id", "project_id", "date_from")
        ):
            raise ValidationError(
                "needs a selective filter", required_one_of=["area_id"]
            )
        return {
            "items": [],
            "limit": 100,
            "offset": 0,
            "has_more": False,
            "total": None,
        }


# ------------------------------------------------------------- meta/health


def test_health_is_public_and_needs_no_auth(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_health_touches_no_repository(client):
    """It is the compose healthcheck and the only unauthenticated route, so a
    DB query here would be a free amplifier."""
    response = client.get("/health")
    assert response.status_code == 200
    assert client.app.state.sessionmaker is None


def test_metrics_reference_is_served_without_a_repository(client):
    """Wired, and static: derived from constants, so it must answer with no
    repository override and no database at all."""
    body = client.get("/meta/metrics").json()
    assert {"metrics", "marts", "units", "caveats"} <= set(body)
    assert client.app.state.sessionmaker is None


def test_metrics_reference_is_cacheable(client):
    """An agent may read this every session and it changes only on deploy."""
    assert "max-age" in client.get("/meta/metrics").headers["cache-control"]


def test_docs_page_embeds_the_proxied_openapi_url(proxied_client):
    """Regression: through nginx (which mounts this app at /api and strips the
    prefix before forwarding) the rendered docs page embedded an unprefixed
    `/openapi.json` link. A browser following it hit nginx with no matching
    location and got a 404 nginx itself returned — invisible to a test that
    only curls each URL directly rather than reading what the page fetches.
    """
    html = proxied_client.get("/docs").text
    assert "url: '/api/openapi.json'" in html
    assert "url: '/openapi.json'" not in html


def test_openapi_schema_advertises_the_proxied_server_url(proxied_client):
    """The `servers` entry is what tools generating a client from this schema
    would use as the base URL — it must point through the proxy too."""
    schema = proxied_client.get("/openapi.json").json()
    servers = [s["url"] for s in schema.get("servers", [])]
    assert "/api" in servers


def test_unprefixed_app_embeds_no_prefix(client):
    """The counterpart to the two tests above: without DXB_API_ROOT_PATH set
    (host-network dev, or the test suite itself) the docs page must NOT gain a
    stray /api it was never given — root_path is proxy-driven, not hardcoded.
    """
    html = client.get("/docs").text
    assert "url: '/openapi.json'" in html


# ------------------------------------------------------------- dimensions


def test_list_areas_returns_geo_flags(client, override):
    override(deps.DimensionRepoDep, FakeDimensions())
    body = client.get("/dimensions/areas").json()
    assert body["items"][0]["name_en"] == "DUBAI MARINA"
    assert body["items"][0]["has_boundary"] is True


def test_geo_filters_are_passed_through_verbatim(client, override):
    fake = FakeDimensions()
    override(deps.DimensionRepoDep, fake)
    client.get("/dimensions/areas?has_geo_data=true&geo_level=polygon")
    _, kwargs = fake.calls[-1]
    assert kwargs["has_geo_data"] is True
    assert kwargs["geo_level"] == "polygon"


def test_omitting_geo_filters_means_no_filtering(client, override):
    """The API never silently hides data — map views opt in explicitly."""
    fake = FakeDimensions()
    override(deps.DimensionRepoDep, fake)
    client.get("/dimensions/areas")
    _, kwargs = fake.calls[-1]
    assert kwargs["has_geo_data"] is None
    assert kwargs["geo_level"] is None


def test_unknown_area_is_404(client, override):
    override(deps.DimensionRepoDep, FakeDimensions())
    response = client.get("/dimensions/areas/999")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


def test_usages_endpoint_exposes_the_raw_vocabulary(client, override):
    override(deps.DimensionRepoDep, FakeDimensions())
    assert client.get("/dimensions/usages").json()[0]["usage"] == "Residential"


# ------------------------------------------------------------------ marts


def test_mart_ids_are_parsed_from_csv(client, override):
    fake = FakeMarts()
    override(deps.MartRepoDep, fake)
    override(deps.DimensionRepoDep, FakeDimensions())
    client.get("/marts/area-monthly?area_ids=1,2,3")
    assert fake.kwargs["area_ids"] == [1, 2, 3]


def test_mart_reports_ids_that_returned_nothing(client, override):
    """So a UI can flag 'no data' against the exact checkbox the user ticked
    instead of quietly showing fewer series than were selected."""
    override(deps.MartRepoDep, FakeMarts())
    override(deps.DimensionRepoDep, FakeDimensions())
    body = client.get("/marts/area-monthly?area_ids=7,8").json()
    assert body["requested_ids"] == [7, 8]
    assert body["missing_ids"] == [7, 8]


def test_mart_q_is_resolved_to_an_id(client, override):
    fake = FakeMarts()
    override(deps.MartRepoDep, fake)
    override(deps.DimensionRepoDep, FakeDimensions())
    client.get("/marts/area-monthly?q=marina")
    assert fake.kwargs["area_ids"] == [1]


def test_ambiguous_q_is_422_not_a_guess(client, override):
    override(deps.MartRepoDep, FakeMarts())
    override(deps.DimensionRepoDep, FakeDimensions())
    response = client.get("/marts/area-monthly?q=ambiguous")
    assert response.status_code == 422
    assert response.json()["error"] == "ambiguous_entity"


def test_bad_id_in_the_csv_is_a_clear_422(client, override):
    override(deps.MartRepoDep, FakeMarts())
    override(deps.DimensionRepoDep, FakeDimensions())
    response = client.get("/marts/area-monthly?area_ids=1,abc")
    assert response.status_code == 422


def test_future_months_are_excluded_by_default(client, override):
    """Marts run to 2028 because leases are registered years ahead."""
    fake = FakeMarts()
    override(deps.MartRepoDep, fake)
    override(deps.DimensionRepoDep, FakeDimensions())
    client.get("/marts/area-monthly?area_ids=1")
    assert fake.kwargs["include_future"] is False


# ------------------------------------------------------------------ facts


def test_fact_query_without_a_selective_filter_is_refused(client, override):
    """12M rows: an unfiltered scan is not a feature."""
    override(deps.FactRepoDep, FakeFacts())
    response = client.get("/facts/transactions")
    assert response.status_code == 422
    assert "area_id" in str(response.json())


def test_fact_query_with_an_area_is_allowed(client, override):
    override(deps.FactRepoDep, FakeFacts())
    assert client.get("/facts/transactions?area_id=1").status_code == 200


# ------------------------------------------------------------- openapi/docs


def test_every_route_is_documented(client):
    schema = client.app.openapi()
    undocumented = [
        f"{method} {path}"
        for path, ops in schema["paths"].items()
        for method, op in ops.items()
        if not op.get("summary")
    ]
    assert undocumented == []


def test_only_auth_routes_are_non_get(client):
    """The read-only guarantee at the surface layer (API_DESIGN.md §4)."""
    schema = client.app.openapi()
    non_get = {
        (path, method)
        for path, ops in schema["paths"].items()
        for method in ops
        if method != "get"
    }
    assert non_get == {("/auth/login", "post"), ("/auth/refresh", "post")}


@pytest.mark.parametrize(
    "path", ["/dimensions/areas", "/marts/area-monthly", "/analytics/area-ranking"]
)
def test_geo_filters_are_offered_everywhere_the_map_drives(client, path):
    schema = client.app.openapi()
    params = {p["name"] for p in schema["paths"][path]["get"].get("parameters", [])}
    assert {"has_geo_data", "geo_level"} <= params


def test_project_schema_exposes_geo_match_method(client):
    """The honesty-critical field: a client must be able to tell a precise
    project pin from the coarse area-centroid backbone
    (docs/PROJECT_GEO_ENRICHMENT.md). Locking it into the public contract."""
    schema = client.app.openapi()
    props = schema["components"]["schemas"]["Project"]["properties"]
    assert "geo_match_method" in props


def test_project_schema_exposes_building_confidence_fields(client):
    """geo_building_count / geo_spread_m tell a client when a 'precise' project
    is really a broad footprint (docs/PROJECT_GEO_ENRICHMENT.md §6)."""
    props = client.app.openapi()["components"]["schemas"]["Project"]["properties"]
    assert "geo_building_count" in props and "geo_spread_m" in props


def test_building_endpoints_are_registered_and_documented(client):
    schema = client.app.openapi()
    for path in (
        "/dimensions/buildings",
        "/dimensions/buildings/{building_id}",
        "/geo/buildings",
    ):
        assert path in schema["paths"]
        assert schema["paths"][path]["get"].get("summary")


def test_building_schema_flags_precise_vs_register_only(client):
    """A building can be in the attribute register without a Makani point;
    is_precise must distinguish the two."""
    props = client.app.openapi()["components"]["schemas"]["Building"]["properties"]
    assert {"is_precise", "geo_match_method", "has_geo_data", "floors"} <= set(props)


def test_list_buildings_passes_filters_through(client, override):
    fake = FakeBuildings()
    override(deps.BuildingRepoDep, fake)
    client.get("/dimensions/buildings?area_id=5&project_id=9&has_geo_data=true")
    assert fake.kwargs["area_id"] == 5
    assert fake.kwargs["project_id"] == 9
    assert fake.kwargs["has_geo_data"] is True


def test_missing_ids_are_computed_across_the_whole_result_not_the_page():
    """Regression: `missing_ids` once came from the current page.

    Rows are ordered by entity name, so an entity whose rows sat past the page
    boundary was reported as having no data — a false 'no data' against a
    checkbox the user had ticked, which is the opposite of this field's job.
    """
    import asyncio
    from types import SimpleNamespace

    from dxb_api.config import build_settings
    from dxb_api.repositories.marts import MartRepository

    # Page holds only entity 31; entity 20's rows exist further down.
    page_row = SimpleNamespace(
        _mapping={"entity_id": 31, "name_en": "AL BARSHA", "month": date(2025, 1, 1)}
    )

    class FakeSession:
        async def execute(self, stmt):
            sql = str(stmt).lower()
            if "mart_area_monthly" in sql:
                # The main area_monthly query (canonical-area self-join,
                # AREA_CODE_MIGRATION_ANALYSIS.md) — distinguished from the
                # area_code_evidence resolution queries below by the
                # mart_area_monthly table it selects from. "distinct" (the
                # with-data probe derived from this same query) is checked
                # first since it also contains "mart_area_monthly".
                if "distinct" in sql:
                    return SimpleNamespace(all=lambda: [(20,), (31,)])
                return SimpleNamespace(all=lambda: [page_row])
            # area_code_evidence resolution queries (expand_area_ids /
            # _canonical_map): nothing in this test is superseded, so every
            # requested id has zero reviewed successors and maps to itself.
            return SimpleNamespace(all=lambda: [])

    repo = MartRepository(FakeSession(), build_settings())
    result = asyncio.run(repo.area_monthly(area_ids=[20, 31, 999999], limit=1))

    assert result["returned_ids"] == [20, 31]
    assert result["missing_ids"] == [999999]
