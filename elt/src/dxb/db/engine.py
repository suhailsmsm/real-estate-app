from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config as AlembicConfig
from dxb_core.models import DimSource
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from dxb.config import get_settings

log = logging.getLogger(__name__)


def _elt_root() -> Path:
    """Directory holding alembic.ini: the CWD in the container (WORKDIR /app),
    the source tree when running from a host checkout."""
    for candidate in (Path.cwd(), Path(__file__).resolve().parents[3]):
        if (candidate / "alembic.ini").exists():
            return candidate
    raise RuntimeError("alembic.ini not found (run from elt/ or the container)")


SOURCES = [
    {
        "code": "dld_gateway",
        "name": "Dubai Land Department open data",
        "base_url": "https://dubailand.gov.ae/en/open-data/real-estate-data/",
        "license": "Dubai open data; attribution to Dubai Land Department",
        "is_government": True,
    },
    {
        "code": "datadubai_transactions",
        "name": "data.dubai — DLD real estate transactions",
        "base_url": "https://data.dubai/en/l/470061",
        "license": "Dubai open data; attribution to Dubai Land Department",
        "is_government": True,
    },
    {
        "code": "datadubai_rents",
        "name": "data.dubai — DLD rent contracts (Ejari)",
        "base_url": "https://data.dubai/en/l/468586",
        "license": "Dubai open data; attribution to Dubai Land Department",
        "is_government": True,
    },
    {
        "code": "osm",
        "name": "OpenStreetMap (via Nominatim)",
        "base_url": "https://www.openstreetmap.org/copyright",
        "license": "ODbL — Open Database License; (c) OpenStreetMap contributors",
        "is_government": False,
    },
    {
        "code": "datadubai_buildings",
        "name": "data.dubai — DLD building register",
        "base_url": "https://data.dubai/en/l/459613",
        "license": "Dubai open data; attribution to Dubai Land Department",
        "is_government": True,
    },
    {
        "code": "makani",
        "name": "Makani — Dubai official addressing (find-place, Google-backed)",
        "base_url": "https://www.makani.ae/",
        # Building geocodes come from Makani's Google-Places-backed search;
        # not government-authored geometry, so flagged non-government.
        "license": "Makani public find-place service; Google Places-derived",
        "is_government": False,
    },
]

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().dsn, pool_pre_ping=True)
    return _engine


def get_session() -> Session:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory()


def migrate() -> None:
    root = _elt_root()
    cfg = AlembicConfig(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_settings().dsn)
    command.upgrade(cfg, "head")


def seed_sources(session: Session) -> None:
    for src in SOURCES:
        if not session.scalar(select(DimSource).where(DimSource.code == src["code"])):
            session.add(DimSource(**src))
    session.commit()


def init_db() -> None:
    migrate()
    with get_session() as session:
        seed_sources(session)
    log.info("database migrated and seeded")


def source_id(session: Session, code: str = "dld_gateway") -> int:
    sid = session.scalar(select(DimSource.id).where(DimSource.code == code))
    if sid is None:
        raise RuntimeError(f"source {code!r} not seeded — run `dxb init`")
    return sid
