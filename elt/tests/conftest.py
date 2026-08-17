"""Shared fixtures & helpers for the dxb unit-test suite.

Unit tests only: no real network, no real database. External systems are
mocked/stubbed everywhere.
"""

from __future__ import annotations

import json

import httpx
import pytest

from dxb.config import Settings

# Every env var get_settings() reads (dxb/config.py). Settings is built
# eagerly and in full on every get_settings() call, so a test exercising one
# corner of it (e.g. job retries) still touches every field — including ones
# it never mentions, like schedule_hour. Without this fixture, tests are only
# hermetic by accident: whatever real values happen to be in the developer's
# shell (from a sourced .env, an IDE's auto-loaded env file, a previous
# `docker compose` session, ...) leak in and can break unrelated tests non-
# reproducibly. Clear them all before every test; individual tests then
# opt in to specific values via monkeypatch.setenv as needed.
_DXB_ENV_VARS = [
    "DXB_DB_HOST",
    "DXB_DB_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "DXB_PAGE_SIZE",
    "DXB_THROTTLE_SECONDS",
    "DXB_MAX_CONCURRENCY",
    "DXB_SOURCE_URL",
    "DXB_SCHEDULE_HOUR",
    "DXB_SCHEDULE_MINUTE",
    "DXB_JOB_ATTEMPTS",
    "DXB_JOB_WAIT_MIN_SECONDS",
    "DXB_JOB_WAIT_MAX_SECONDS",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_STARTTLS",
    "ALERT_TO",
    "ALERT_FROM",
    "DXB_DATA_RAW",
]


@pytest.fixture(autouse=True)
def _clean_dxb_env(monkeypatch):
    for name in _DXB_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------- settings

_SETTINGS_DEFAULTS = dict(
    db_host="localhost",
    db_port=5432,
    db_user="dxb",
    db_password="",
    db_name="dxb",
    page_size=500,
    throttle_seconds=0.0,
    max_concurrency=1,
    source_url="https://example.test/open-data/",
    schedule_hour=2,
    schedule_minute=30,
    job_attempts=3,
    job_wait_min_seconds=0.0,
    job_wait_max_seconds=0.0,
    smtp_host="",
    smtp_port=587,
    smtp_user="",
    smtp_password="",
    smtp_starttls=True,
    alert_to="",
    alert_from="dxb-elt@localhost",
    data_raw_dir="/app/data/raw",
)


def make_settings(**overrides) -> Settings:
    """Build a full Settings object with sensible test defaults."""
    return Settings(**{**_SETTINGS_DEFAULTS, **overrides})


@pytest.fixture
def settings_factory():
    return make_settings


# ----------------------------------------------------- insert introspection


def insert_value_rows(stmt) -> list[dict]:
    """Return the list of value dicts embedded in a (multi-row) pg_insert.

    Both dxb.collectors.dld.stage_rows and dxb.transform.dld._upsert_facts
    build ``pg_insert(table).values([...])``; SQLAlchemy stores the rows in the
    statement's ``_multi_values`` before any DB round-trip, so we can assert on
    them with a mocked session.
    """
    return list(stmt._multi_values[0])


# --------------------------------------------------------- httpx mock helper


def gateway_transport(handler) -> httpx.MockTransport:
    """Wrap a handler(request) -> (rows list | (rows, response_code) | Response)
    into a MockTransport that returns the gateway envelope."""

    def _dispatch(request: httpx.Request) -> httpx.Response:
        result = handler(request)
        if isinstance(result, httpx.Response):
            return result
        if isinstance(result, tuple):
            rows, code = result
        else:
            rows, code = result, 200
        return httpx.Response(
            200, json={"responseCode": code, "response": {"result": rows}}
        )

    return httpx.MockTransport(_dispatch)


def request_json(request: httpx.Request) -> dict:
    return json.loads(request.content)


# ----------------------------------------------------------- stub dim caches


class StubCaches:
    """Minimal stand-in for transform.dld.DimCaches.

    Returns fixed surrogate ids and records the calls so tests can assert on
    the mapping. ``area_id=None`` simulates an unresolved area (drops a fact).
    """

    def __init__(
        self,
        area_id: int | None = 10,
        ptype_id: int | None = 20,
        project_id: int | None = 30,
        developer_id: int | None = 40,
        building_id: int | None = 50,
    ) -> None:
        self._area_id = area_id
        self._ptype_id = ptype_id
        self._project_id = project_id
        self._developer_id = developer_id
        self._building_id = building_id
        self.area_calls: list = []
        self.ptype_calls: list = []
        self.project_calls: list = []
        self.building_calls: list = []

    def area(self, name, name_ar=None):
        self.area_calls.append((name, name_ar))
        return self._area_id

    def ptype(self, usage, prop_type, subtype):
        self.ptype_calls.append((usage, prop_type, subtype))
        return self._ptype_id

    def project(
        self, name, *, is_master=False, master_name=None, area_id=None, name_ar=None
    ):
        self.project_calls.append((name, master_name, area_id))
        return self._project_id

    def developer(self, dld_number, name_en, name_ar=None):
        return self._developer_id

    def building(self, name, *, area_id=None, project_id=None, name_ar=None):
        self.building_calls.append((name, area_id, project_id))
        return self._building_id


@pytest.fixture
def stub_caches():
    return StubCaches()


# ------------------------------------------------ fake session for pipeline


class FakeSession:
    """Context-manager session backed by a shared store.

    Emulates just enough of SQLAlchemy's Session for pipeline.run_with_retries:
    add() assigns an autoincrement-style id, get() looks rows up by id.
    """

    def __init__(self, store: dict) -> None:
        self.store = store

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def add(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            self.store["_seq"] = self.store.get("_seq", 0) + 1
            obj.id = self.store["_seq"]
        self.store[obj.id] = obj

    def commit(self) -> None:
        pass

    def get(self, model, pk):
        return self.store.get(pk)


@pytest.fixture
def fake_session_store():
    """A shared store + a get_session-compatible factory over it."""
    store: dict = {}

    def factory() -> FakeSession:
        return FakeSession(store)

    factory.store = store  # type: ignore[attr-defined]
    return factory
