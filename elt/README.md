# dxb ELT service

Collects Dubai Land Department open data (transactions, rents, projects) into a local
Postgres star schema. See ../docs/PLAN.md for the full design.

Run everything from the repo root:

```
docker compose up -d --build         # db + scheduled daily pipeline
docker compose run --rm elt dxb init                      # migrations + seed
docker compose run --rm elt dxb backfill --from 2026-01-01  # historical load
docker compose run --rm elt dxb stats
```

## Area-code migration

DLD has been re-coding established communities to new area ids since
2026-07-20 (still ongoing) — the same project/building, a new `area_id`
going forward, with no guarantee an old area maps to just one new one (21 of
48 split areas so far fan out into several). The pipeline detects splits
daily (non-fatal, before the mart rebuild) but never acts on one alone —
`area_code_evidence` rows sit `reviewed=false` until a human confirms them.
Review and act on detected splits with:

```
docker compose exec elt dxb list-area-splits
```

Arrow keys move the highlight, Enter approves the highlighted pending pair
or reverts an already-approved one back to pending, Esc/Ctrl-C quits.
"Approving" only ever flips one boolean (`area_code_evidence.reviewed`) —
`dim_project`/`dim_building`/`dim_area` are never written by this mechanism,
in either direction, so a misclick is one more Enter-press to undo. See
[docs/AREA_CODE_MIGRATION_ANALYSIS.md](../docs/AREA_CODE_MIGRATION_ANALYSIS.md)
for the full design and why resolution is anchored on the project rather
than the area.

## Dev setup

One-time, per clone — wires up the tracked pre-commit hook
(`.pre-commit-config.yaml`, blocks commits on `ruff check` failures in
`elt/`; runs via `elt/`'s own uv-managed venv, no separate ruff install):

```
uv run --project elt pre-commit install
```
