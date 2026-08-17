from __future__ import annotations

import logging
from datetime import date, datetime

import typer
from sqlalchemy import func, select, text

from dxb.config import get_settings

app = typer.Typer(help="Dubai real estate ELT: DLD open data -> Postgres star schema")

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)


def _parse(day: str) -> date:
    return datetime.strptime(day, "%Y-%m-%d").date()


@app.command()
def init() -> None:
    """Run migrations and seed dim_source."""
    from dxb.db.engine import init_db

    init_db()
    typer.echo("database ready")


@app.command()
def collect(
    endpoint: str = typer.Argument(help="areas | projects | transactions | rents"),
    from_date: str = typer.Option(None, "--from", help="YYYY-MM-DD"),
    to_date: str = typer.Option(None, "--to", help="YYYY-MM-DD"),
) -> None:
    """Collect one endpoint into staging."""
    from dxb.collectors.client import DldClient
    from dxb.collectors.dld import (
        collect_areas,
        collect_projects,
        collect_windowed,
        default_window,
    )
    from dxb.db.engine import get_session, source_id

    settings = get_settings()
    with (
        get_session() as session,
        DldClient(
            page_size=settings.page_size,
            throttle_seconds=settings.throttle_seconds,
            max_concurrency=settings.max_concurrency,
        ) as client,
    ):
        sid = source_id(session)
        if endpoint == "areas":
            report = collect_areas(session, client, sid)
        elif endpoint == "projects":
            report = collect_projects(session, client, sid)
        elif endpoint in ("transactions", "rents"):
            if from_date and to_date:
                d_from, d_to = _parse(from_date), _parse(to_date)
            else:
                d_from, d_to = default_window(session, sid, endpoint)
            report = collect_windowed(session, client, sid, endpoint, d_from, d_to)
        else:
            raise typer.BadParameter(f"unknown endpoint {endpoint!r}")
    typer.echo(report)


@app.command()
def transform() -> None:
    """Transform staged rows into dims and facts."""
    from dxb.db.engine import get_session
    from dxb.transform.dld import transform_all

    with get_session() as session:
        report = transform_all(session, get_settings().source_url)
    typer.echo(report)


@app.command()
def marts() -> None:
    """Rebuild analytics marts."""
    from dxb.db.engine import get_session
    from dxb.marts import rebuild_marts

    with get_session() as session:
        typer.echo(rebuild_marts(session))


@app.command()
def backfill(
    from_date: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to_date: str = typer.Option(None, "--to", help="YYYY-MM-DD (default today)"),
) -> None:
    """Full pipeline over an explicit historical range (resumable)."""
    from dxb.pipeline import run_with_retries

    d_to = _parse(to_date) if to_date else date.today()
    report = run_with_retries(
        kind="backfill", date_from=_parse(from_date), date_to=d_to
    )
    raise typer.Exit(0 if report else 1)


@app.command("run-once")
def run_once() -> None:
    """One daily pipeline run now (with retries + alerting)."""
    from dxb.pipeline import run_with_retries

    report = run_with_retries(kind="manual")
    raise typer.Exit(0 if report else 1)


@app.command("run-scheduler")
def run_scheduler() -> None:
    """Long-lived scheduler (container default command)."""
    from dxb.scheduler import main

    main()


@app.command("import-csv")
def import_csv(
    source: str = typer.Argument(
        help="alexefimik | austinpowers | area-coords | project-coords | all"
    ),
) -> None:
    """One-off historical import from data/raw/*.csv (see docs/CSV_DATA_ANALYSIS.md).
    Not part of the daily scheduler or backfill — run manually, once."""
    from dxb.csv_import.pipeline import import_all, import_geo, import_source
    from dxb.csv_import.sources import GEO_SOURCES, TRANSACTION_SOURCES
    from dxb.db.engine import get_session

    settings = get_settings()
    with get_session() as session:
        if source == "all":
            report = import_all(session, settings.source_url)
        elif source in TRANSACTION_SOURCES:
            report = import_source(session, source, settings.source_url)
        elif source in GEO_SOURCES:
            report = import_geo(session, source)
        else:
            raise typer.BadParameter(f"unknown source {source!r}")
    typer.echo(report)


@app.command("import-datadubai")
def import_datadubai(
    dataset: str = typer.Argument(help="transactions | rents | buildings | all"),
) -> None:
    """One-off / recurring import from the data.dubai CSV exports in data/raw
    (see docs/DATADUBAI_REBUILD_PLAN.md). Not part of the daily scheduler.
    Drop fresh monthly files in data/raw and re-run to top up — transactions,
    rents and the building register are all handled here."""
    from dxb.datadubai.buildings import import_buildings
    from dxb.datadubai.importer import import_dataset
    from dxb.datadubai.pipeline import import_all
    from dxb.datadubai.sources import DATASETS
    from dxb.db.engine import get_session

    settings = get_settings()
    with get_session() as session:
        if dataset == "all":
            report = import_all(session, settings.source_url)
        elif dataset == "buildings":
            # Attribute register, not facts — its own importer.
            report = import_buildings(session, settings.source_url)
        elif dataset in DATASETS:
            report = import_dataset(session, dataset, settings.source_url)
        else:
            raise typer.BadParameter(f"unknown dataset {dataset!r}")
    typer.echo(report)


@app.command("enrich-buildings")
def enrich_buildings_cmd(
    all_: bool = typer.Option(
        False, "--all", help="Reconsider every building, not just newly-seen ones."
    ),
    limit: int = typer.Option(
        0, "--limit", help="Cap buildings attempted in this run (0 = all)."
    ),
) -> None:
    """Makani-geocode buildings, then roll their points up into project
    locations (docs/PROJECT_GEO_ENRICHMENT.md §6). The daily pipeline runs this
    incrementally for new buildings; this command is the full historical sweep
    (throttled ~0.4s/call, so chunk it with --limit)."""
    from dxb.db.engine import get_session
    from dxb.geo.buildings import enrich_buildings, place_projects_from_buildings

    with get_session() as session:
        buildings = enrich_buildings(
            session, missing_only=not all_, limit=limit or None
        )
        projects = place_projects_from_buildings(session)
    typer.echo({"buildings": buildings, "projects": projects})


@app.command("set-cutovers")
def set_cutovers() -> None:
    """Recompute the data.dubai cutovers and the gateway watermarks."""
    from dxb.datadubai.cutover import finalize
    from dxb.db.engine import get_session

    with get_session() as session:
        typer.echo(finalize(session))


@app.command("enrich-geo")
def enrich_geo() -> None:
    """One-off full-sweep OSM/Nominatim geo enrichment for dim_area (see
    docs/OSM_AREA_GEO_ENRICHMENT.md). The daily/backfill/CSV-import pipelines
    already run a lighter incremental version of this automatically for
    newly-seen areas — this command is for the initial backfill and for
    re-running after the matcher improves."""
    from dxb.db.engine import get_session
    from dxb.osm_geo.enrich import enrich_areas

    with get_session() as session:
        report = enrich_areas(session, missing_only=False)
    typer.echo(
        {k: v for k, v in report.items() if k != "details"}
    )  # details dumped separately for docs, not the terminal


@app.command("enrich-project-geo")
def enrich_project_geo(
    precision: bool = typer.Option(
        False,
        "--precision",
        help="Also run the throttled Nominatim precision upgrade (~1 req/s).",
    ),
    limit: int = typer.Option(
        0, "--limit", help="Cap projects attempted in the precision pass (0 = all)."
    ),
) -> None:
    """Project geolocation (see docs/PROJECT_GEO_ENRICHMENT.md).

    Default: just the instant area-centroid backbone (pure SQL, ~97%
    coverage). The daily/backfill pipelines already run this automatically for
    newly-seen projects. Pass --precision to also run the Nominatim upgrade,
    which validated only ~10% in probing and is slow, so it is opt-in and
    chunkable via --limit."""
    from dxb.db.engine import get_session
    from dxb.osm_geo.projects import backfill_area_centroids, enrich_project_points

    with get_session() as session:
        report = {"area_centroid_filled": backfill_area_centroids(session)}
        if precision:
            report["precision"] = enrich_project_points(session, limit=limit or None)
    typer.echo(report)


@app.command("detect-area-splits")
def detect_area_splits() -> None:
    """Run the area-code-split detector standalone and print a summary
    (docs/AREA_CODE_MIGRATION_ANALYSIS.md). The daily pipeline already runs
    this automatically and non-fatally, right before the mart rebuild — this
    command is for re-running on demand (e.g. after a backfill). Only ever
    refreshes area_code_evidence and project_area_actual; never touches
    dim_project, dim_building, or dim_area."""
    from dxb.area_codes import detect_area_code_splits
    from dxb.db.engine import get_session

    with get_session() as session:
        report = detect_area_code_splits(session)
    typer.echo(
        f"candidates examined: {report['candidates']}, "
        f"splits detected/refreshed: {report['detected']}"
    )
    for pair in report["pairs"]:
        typer.echo(
            f"  old_area={pair['old_area_id']:<6} new_area={pair['new_area_id']:<6} "
            f"overlap={pair['overlap_pct']}%"
        )


def _area_split_label(session, row) -> str:
    from dxb_core.models import DimArea

    old = session.get(DimArea, row.old_area_id)
    new = session.get(DimArea, row.new_area_id)
    old_label = (
        f"{old.name_en} ({old.dld_area_code or old.id})"
        if old
        else str(row.old_area_id)
    )
    new_label = (
        f"{new.name_en} ({new.dld_area_code or new.id})"
        if new
        else str(row.new_area_id)
    )
    status = "APPROVED" if row.reviewed else "pending "
    return (
        f"[{status}] {old_label} -> {new_label}  "
        f"({row.evidence_project_overlap_pct or 0}% overlap, "
        f"{row.evidence_txn_count} txns, "
        f"{len(row.evidence_project_ids or [])} projects, "
        f"first seen {row.first_seen_new_code})"
    )


@app.command("list-area-splits")
def list_area_splits() -> None:
    """Interactively review area-code-split evidence: arrows move the
    highlight, Enter toggles the highlighted pair — approves a pending one or
    reverts an already-approved one back to pending — Esc/Ctrl-C quits.

    This is the entire "apply"/"undo" step (docs/AREA_CODE_MIGRATION_
    ANALYSIS.md), symmetric in both directions: no dim_project, dim_building,
    or dim_area write ever happens. project_area_actual is read-time
    indirection, so every resolution path (building match key, geocoding
    containment, marts, API) picks up the change on its next read as soon as
    `area_code_evidence.reviewed` flips, either way — a mistaken approval is
    corrected the same way it was made, no SQL required.
    """
    import questionary

    from dxb.area_codes import all_evidence, approve_area_split, revert_area_split
    from dxb.db.engine import get_session

    while True:
        with get_session() as session:
            rows = all_evidence(session)
            if not rows:
                typer.echo("no area-code-split evidence yet")
                return

            choices = [
                questionary.Choice(
                    _area_split_label(session, row),
                    value=(row.old_area_id, row.new_area_id, row.reviewed),
                )
                for row in rows
            ]
            picked = questionary.select(
                f"{len(rows)} pairs — arrows to move, Enter to approve/revert, "
                "Esc/Ctrl-C to quit:",
                choices=choices,
            ).ask()

            if picked is None:
                return

            old_area_id, new_area_id, was_reviewed = picked
            if was_reviewed:
                report = revert_area_split(session, old_area_id, new_area_id)
                verb = "reverted"
            else:
                report = approve_area_split(session, old_area_id, new_area_id)
                verb = "approved"

        if not report["found"]:
            typer.echo("that pair changed since the list was loaded — refreshing")
        else:
            typer.echo(f"{verb}: old_area={old_area_id} new_area={new_area_id}")


@app.command()
def stats() -> None:
    """Row counts and a quick market sanity report."""
    from dxb_core.models import (
        DimArea,
        DimProject,
        FactRentContract,
        FactSaleTransaction,
        StgRaw,
    )

    from dxb.db.engine import get_session

    with get_session() as session:
        for label, model in [
            ("stg_raw", StgRaw),
            ("dim_area", DimArea),
            ("dim_project", DimProject),
            ("fact_sale_transaction", FactSaleTransaction),
            ("fact_rent_contract", FactRentContract),
        ]:
            count = session.scalar(select(func.count()).select_from(model))
            typer.echo(f"{label:26} {count:>10,}")
        unprocessed = session.scalar(
            select(func.count())
            .select_from(StgRaw)
            .where(StgRaw.processed_at.is_(None))
        )
        typer.echo(f"{'stg_raw unprocessed':26} {unprocessed:>10,}")

        typer.echo(
            "\nTop-10 areas by median sale AED/m2 (residential, latest full month):"
        )
        rows = session.execute(
            text("""
            WITH latest AS (
                SELECT max(month) AS month FROM mart_area_monthly
                WHERE usage = 'Residential' AND sale_cnt >= 10
            )
            SELECT a.name_en, m.sale_cnt, m.sale_median_price_m2,
                   m.rent_median_annual_m2, m.gross_yield_pct
            FROM mart_area_monthly m
            JOIN dim_area a ON a.id = m.area_id, latest
            WHERE m.month = latest.month AND m.usage = 'Residential'
                AND m.sale_cnt >= 10
            ORDER BY m.sale_median_price_m2 DESC NULLS LAST
            LIMIT 10
        """)
        ).all()
        for name, cnt, med, rent_med, yield_pct in rows:
            typer.echo(
                f"  {name:35} sales={cnt:>5} median={med or 0:>10,.0f} "
                f"rent/m2/yr={rent_med or 0:>7,.0f} yield={yield_pct or 0:>5}%"
            )


if __name__ == "__main__":
    app()
