"""One-off historical rebuild from the data.dubai exports.

Not part of the daily scheduler. Sequence: import transactions/rents/buildings
-> enrich areas -> place projects from any already-geocoded buildings + area
fallback -> set cutovers/watermarks -> rebuild marts.

The throttled Makani building geocoding is deliberately NOT run here: over a
full backfill it is a multi-hour sweep, so it is the explicit
`dxb enrich-buildings --all` CLI. CSV building *attributes* are loaded here
(fast, local), before that geocoding — see docs/PROJECT_GEO_ENRICHMENT.md §6.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from dxb.datadubai.buildings import import_buildings
from dxb.datadubai.cutover import finalize
from dxb.datadubai.importer import import_dataset
from dxb.datadubai.sources import DATASETS, files_for
from dxb.geo.buildings import place_projects_from_buildings
from dxb.marts import rebuild_marts
from dxb.osm_geo.enrich import enrich_missing_areas
from dxb.osm_geo.projects import enrich_missing_project_geo

log = logging.getLogger(__name__)


def import_all(session: Session, source_url: str, with_geo: bool = True) -> dict:
    report: dict = {}
    report["transactions"] = import_dataset(session, "transactions", source_url)
    report["rents"] = import_dataset(session, "rents", source_url)
    # Buildings are attribute enrichment, not facts; skip quietly if no export
    # is present so a transactions/rents-only refresh still works.
    if files_for(DATASETS["buildings"]):
        report["buildings"] = import_buildings(session, source_url)
    if with_geo:
        report["geo_enrich"] = enrich_missing_areas(session)
        # Place projects from whatever buildings are already Makani-validated
        # (run `dxb enrich-buildings --all` to geocode them), then fill the
        # area-centroid backbone for everything still unplaced.
        report["building_placement"] = place_projects_from_buildings(session)
        report["project_geo"] = enrich_missing_project_geo(session)
    report["bookkeeping"] = finalize(session)
    report["marts"] = rebuild_marts(session)
    return report
