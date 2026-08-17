# Area code migration — plan

Status: **revision 2, implemented, tested, and live-verified end to end.**
Revision 1 (scalar `dim_area.superseded_by_area_id`, one old area → one new
area) was implemented, tested, and then found insufficient by actually
running it — see below; it was replaced, not extended. As of the live
verification pass, all 84 pairs the detector has found (48 distinct old
areas, 21 genuinely one-to-many) are reviewed — see "Live verification
results" at the end of this doc.

## What happened

DLD issued new `C-xx` area codes city-wide starting 2026-07-20. 89+
established communities affected so far, still rolling out. Same
projects/buildings, new area_id going forward.

## Revision 2: it's a subdivision, not a rename — one old area, many new ones

Running the detector against live data found: **21 of 48 split old areas
(44%) fan out into multiple new codes, not one.** `MARSA DUBAI` (old code 20)
alone splits into **four** completely disjoint communities — `DUBAI MARINA`
(292), `BLUEWATERS` (318), `DUBAI HARBOUR` (339), `JUMEIRAH BEACH RESIDENCE`
(374); zero shared projects between any pair, confirmed. One old area has
five successors. A scalar `superseded_by_area_id` can only point one old area
to one new area — applying it here would have folded three unrelated
communities' entire histories into whichever successor got set first.

Checked and confirmed, so the fix stays scoped:

- **Never many-old-to-one-new.** Zero cases of multiple old areas collapsing
  into one new code — DLD is subdividing, never merging, confirmed directly.
  One-to-many is sufficient; many-to-many is not needed.
- **No project-level fragmentation.** 6 apparent project-name duplicates
  among split-area projects, all turned out to be the pre-existing
  `is_master`/child pattern (a master-development stub, unrelated to area
  codes) — not a new duplication bug. `dld_project_number`-keyed upsert holds.
- **`dim_project.area_id` has not self-corrected yet, and we don't rewrite it
  ourselves either.** Known Marina projects (PRINCESS TOWER, PARK ISLANDS,
  ...) still show old code 20 today, and the DLD project feed hasn't caught
  up — but per the base rule (CLAUDE.md), the fix isn't to write our own
  answer into that column. It's read-time indirection instead (see below).

## Decisions (per user direction, revised)

**Base rule, now codified in `CLAUDE.md`**: we never update a value that
came from an external source with our own inferred one — only the source
itself, reporting something different on a later import, legitimately
changes a stored value. Wherever our own analysis concludes a row's true
current meaning differs from what's literally stored, the fix is an
indirection table or auxiliary metadata joined at read time, never a
rewrite. Everything below follows from that rule, not just from convenience.

- **Canonical id = the NEW code.** Unchanged.
- **`superseded_by_area_id` becomes a list**, not a scalar column — an old
  area can have several current successors. No new column: `area_code_evidence`
  (`reviewed = true` rows) *is* that list — queried directly, never collapsed
  onto `dim_area`.
- **Resolution is anchored on the PROJECT, not the area.** Each new code's
  projects are disjoint (confirmed), so a project unambiguously belongs to
  exactly one successor — unlike an old area, which may not. Facts/rents with
  a `project_id` (**90.2%** of split-area sales) resolve their canonical area
  by checking `project_area_actual` (reviewed) first, falling back to
  `dim_project.area_id` exactly as stored — never via the fact row's own
  `area_id` directly. Buildings resolve the same way, through their project.
- **Nothing is ever written to `dim_project.area_id` or `dim_building.area_id`
  by this mechanism.** `project_area_actual` is pure read-time indirection —
  the detector's findings drive what gets *joined against*, never a backfill.
  "Applying" a reviewed pair is flipping `area_code_evidence.reviewed = true`,
  nothing more.
- **Rows with no `project_id` (~9.8%, standalone plots)** have no precise
  signal. Old-area's single successor is used **only when unambiguous** (the
  27 of 48 old areas with exactly one successor); for the 21 one-to-many old
  areas, these rows stay attributed to the old area — an honest, small gap,
  never a guess.
- **API/MCP responses show the lineage** as a list: a current area's detail
  includes every old code that feeds it; an old area's detail lists all of
  its successors (not "a" successor).

## Schema (revised)

Drop the scalar pointer from revision 1; it can't represent one-to-many.

```sql
ALTER TABLE dim_area DROP COLUMN superseded_by_area_id;
```

`area_code_evidence` (already built) keeps its shape **and** gets back the
project-set array, restored — the two structures serve different purposes,
not redundant ones:

```sql
ALTER TABLE area_code_evidence ADD COLUMN evidence_project_ids INTEGER[];

CREATE TABLE project_area_actual (
  project_id BIGINT PRIMARY KEY REFERENCES dim_project(id),
  old_area_id INTEGER NOT NULL REFERENCES dim_area(id),
  new_area_id INTEGER NOT NULL REFERENCES dim_area(id),
  detected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- **`project_area_actual`** is the *operational* table — indexed by
  `project_id`, one row per project, what the resolution mechanism actually
  looks up and what the apply step writes from. `project_id` is the primary
  key because a project has exactly one current target, refreshable by
  upsert.
- **`area_code_evidence.evidence_project_ids`** is *old-area metadata* — a
  denormalized snapshot, kept on the header row itself, of everything that
  used to belong to that old area at detection time. Deliberately a plain
  array, not a join: "what used to be under MARSA DUBAI" is answered by
  reading this one column directly (`SELECT evidence_project_ids FROM
  area_code_evidence WHERE old_area_id = 20`), no `project_area_actual` join
  needed. This is a historical/point-in-time snapshot — like a one-time data
  load, not a live view — and it does not participate in resolution at all;
  nothing in the mechanism above reads it. Kept for what it enables later,
  if needed (e.g. a legacy map view of a superseded area's contents).

Both are populated by the same detector run, which already computes this
exact project set internally — persisted twice, once per-project
(`project_area_actual`) and once as the header's own array
(`evidence_project_ids`). No `reviewed` flag on either child structure: trust
flows from the parent — nothing is *applied* until its `(old_area_id,
new_area_id)` pair's `area_code_evidence.reviewed = true`.

**`project_area_actual` is read-time indirection, not a mutation target —
nothing is ever written to `dim_project` or `dim_building` by this
mechanism**, the same "never touch existing data" rule as everywhere else in
this design. "Applying" a reviewed pair is nothing more than flipping
`area_code_evidence.reviewed = true`; `project_area_actual`'s rows for that
pair are immediately live to every reader the instant that happens — there
is no separate backfill/cascade step to run. A project's canonical area is
resolved by checking `project_area_actual` (joined to its pair's `reviewed`
flag) **first**; only when no reviewed mapping exists for that project does
resolution fall back to reading `dim_project.area_id` **directly, exactly as
DLD's own project feed reported it** (never a value we wrote). Buildings
resolve the same way, through their own `project_id` — also never touching
`dim_building.area_id`.

## One mechanism, used everywhere: resolve through the project first

```sql
-- canonical area for one fact row (sale/rent):
canonical_area_id := COALESCE(
  -- primary: reviewed indirection, read-only
  (SELECT pam.new_area_id
     FROM project_area_actual pam
     JOIN area_code_evidence ace
       ON ace.old_area_id = pam.old_area_id AND ace.new_area_id = pam.new_area_id
    WHERE pam.project_id = f.project_id AND ace.reviewed),
  -- next: the project's own stored area, untouched, for projects the
  -- detector never flagged (nothing to redirect) or not yet reviewed
  (SELECT p.area_id FROM dim_project p WHERE p.id = f.project_id),
  -- fallback only for project-less rows, and only when the old area has
  -- exactly one successor (never guess between several):
  (SELECT new_area_id FROM area_code_evidence
     WHERE old_area_id = f.area_id AND reviewed
     HAVING count(*) OVER (PARTITION BY old_area_id) = 1),
  f.area_id
);
```

Still a **redirect, not a union**: a row is grouped/labeled under exactly one
canonical id, never both its own and the canonical one — summing "all areas"
cannot double-count. Marts, `expand_area_ids`, and the analytics self-joins
built in revision 1 all need reworking to this project-first shape instead of
joining on `dim_area.superseded_by_area_id` (removed).

Requesting an old id directly still works (audit/history), and is flagged as
superseded with the **list** of its current successors, not a single one.

## Querying a project or building's own area — OR'd with indirection, never ambiguous

`dim_project.area_id` and `dim_building.area_id` are **never rewritten**
(base rule, CLAUDE.md) — so a migrated project still shows its *original*
area_id forever, and a plain `WHERE area_id = new_id` match would miss every
project the detector has actually identified as belonging there. The fix is
the same indirection used everywhere else, `OR`'d onto the literal match
rather than replacing it — still never an ambiguity error, since a project
resolves to exactly one place by construction:

- **A new/canonical area's projects**:
  `dim_project WHERE area_id = new_id OR id IN (SELECT project_id FROM
  project_area_actual pam JOIN area_code_evidence ace ON (...) WHERE
  pam.new_area_id = new_id AND ace.reviewed)` — literal matches (new
  developments, or DLD's own feed eventually catching up) **plus** every
  project the detector has identified and a human has reviewed. No
  successor-count logic needed here — a project has exactly one reviewed
  redirect, never several, so there's nothing to disambiguate.
- **An old/superseded area's projects**: `dim_project WHERE area_id = old_id`
  — unchanged, a plain, literal, historical snapshot. Deliberately **not**
  OR'd with anything: this answers "what was originally filed here," which a
  reviewed-redirect project can legitimately still satisfy alongside also
  satisfying the new area's query — two independent reads answering two
  different questions, not a double-counted aggregate.
- Buildings follow the identical two rules, through their own `project_id`'s
  `project_area_actual` entry (no `project_id` means no indirection to
  check — literal match only, either way).

Still **not** the `expand_area_ids` / ambiguity-error treatment used for
facts and aggregates (§ below) — that exists because facts need one
*resolved, combined* answer and an old area with several successors has none
to give. A project/building lookup never faces that: `list_projects`,
`resolve_project`, `list_buildings`, and `resolve_building` never raise the
ambiguity error; they just need the `OR` above when the requested id is a
new/canonical area. The old-id case needs no change at all.

## Querying an old id directly, when it has more than one successor

An old area with exactly one reviewed successor redirects transparently —
genuinely unambiguous, same behavior as revision 1. An old area with
**several** successors (44% of split areas) has no single canonical id to
redirect to, and after the mart rebuild its own mart/fact rows hold only its
small project-less residual (the ~9.8% fallback) — returning that silently
would be a *different* kind of misleading answer, not a fix.

Decision: **surface it as an ambiguity, the same way this API already
handles a fuzzy name matching more than one entity** (`AmbiguousEntityError`
— see `_resolve` in `repositories/base.py`). Querying `area_id=20` (old
MARSA DUBAI) anywhere — dimension lookup, facts, marts, analytics — raises,
naming all four current successors, rather than picking one or returning a
partial slice. This applies uniformly to every area-scoped endpoint, not
only name-driven resolution. An old area with exactly one successor never
hits this path; only the 21 one-to-many old areas can.

## API / MCP surface

- `GET /dimensions/areas/{id}` on a **current** area: adds
  `superseded_areas: [{id, dld_area_code, name_en}]` — every old code whose
  activity now reports under this area. Empty for an area never split.
- `GET /dimensions/areas/{id}` on an **old** area: adds `superseded_by:
  [{id, dld_area_code, name_en}]` — a **list**, not a single object, because
  DLD subdivides, not renames (21 of 48 split areas have more than one
  successor). The old area's own id/name/code are returned unchanged; only
  its aggregated figures are unreachable when the list has 2+ entries (see
  ambiguity error below).
- `find_entity(detailed=true)` mirrors both fields.
- `growth`/`compare` on a canonical area get one caveat line naming the old
  code(s) folded in, so a client sees *why* a number differs from what
  raw-facts-by-old-code used to show.

## Detection (daily, mostly unchanged — now also captures the project set)

Non-fatal pipeline step, before `rebuild_marts`: find areas with a recent
`min(txn_date)`, run the project-overlap check against those, upsert
`area_code_evidence` header rows (`reviewed=false`, refreshing
`evidence_txn_count` and `evidence_project_ids`) **and one
`project_area_actual` row per project in the overlap set**, both daily
(the set can grow as more sales land under the new code). A human flips
`reviewed=true` on the header; nothing is joined against — for resolution,
lineage display, or the `list_projects`/`list_buildings` `OR` above — before
that.

## Geocoding — retire the per-area name-hint crutch

A prior fix added `_NAME_HINTS` to `osm_geo/matcher.py`: a hand-curated
`{"MARSA DUBAI": "Dubai Marina"}` dict to help old code 20 geocode. Checked
against the new plan and it should be reverted, not extended:

- Every new `C-xx` code checked (269, 274, 282, 292, 395) geocodes `exact`
  with the **unmodified** matcher — new codes use standard English names,
  no hints needed.
- Old codes are the ones with DLD-only administrative names OSM doesn't
  recognize. Once superseded, their own geometry is irrelevant — the API
  surfaces the successor's geometry instead (see above).
- Hand-curating one dict entry per split does not scale to 89+ cases and is
  exactly the kind of narrow point-fix the unification design replaces.

Action: revert `_NAME_HINTS` (matcher.py, enrich.py, their tests, the
OSM_AREA_GEO_ENRICHMENT.md addition) — already done, confirmed reverted to
the committed baseline. Change the enrichment sweep's candidate query to skip
areas present as `old_area_id` in any `reviewed` `area_code_evidence` row
(there is no `dim_area` flag to check now — revision 2 dropped the scalar
column) — no wasted Nominatim calls on codes that will never be canonical.

## Full impact audit (grep across elt/, api/, mcp/, schema)

**Revision 1's building/project match-key fix (already implemented, tests
green) canonicalized via the scalar `superseded_by_area_id` — needs
reworking to the project-anchored mechanism above**, since that column no
longer exists and area-level canonicalization is wrong for the 21 one-to-many
old areas anyway. The underlying bug it fixed is unchanged and still real:
`dim_building` has `UniqueConstraint(name_en, area_id)`, and the matcher
caches building lookups as `{(name, area_id): id}` — a building named
"PRINCESS TOWER" reported under a new area code is a cache miss against the
row stored under the old one, creating a duplicate instead of linking. The
fix is the same shape, just keyed on the building's **project's** resolved
area — checked against `project_area_actual` (reviewed) first, falling back
to the project's own stored `area_id` — instead of an area-to-area pointer.
Nothing is written; this only changes the lookup key. Applies identically to
`datadubai/buildings.py`'s dedupe key.

**Geocoding containment (already fixed, tests green) also needs reworking**
to resolve via project rather than the scalar column, for the same reason.
`geo/buildings.py` / `osm_geo/projects.py` validate a candidate point against
`area_id`'s boundary; a project/building whose area flips to a code with no
geometry yet (confirmed: area 303, `DOWN TOWN JABAL ALI`) would otherwise
fail containment against a polygon that will never exist for the old row.

**MCP needs no changes.** It only passes `area_id` through to the REST API
as a plain parameter — fixing this entirely at the API layer is sufficient,
confirming the thin-client design (§2 of MCP_DESIGN.md) pays off here.

**Rents: unaffected today, included unconditionally anyway (per direction).**
Ejari (rental registration) hasn't adopted the new codes yet — even brand-new
rent contracts on the same split Marina projects still land under old code
20. Not a special case: `expand_area_ids` applies to rents exactly like
everything else, so nothing changes when Ejari does eventually catch up.

Every touch point, by file:

| Layer | File | What |
|---|---|---|
| ELT write | `transform/dld.py` | building match key (fix above); `project.area_id` self-corrects |
| ELT write | `datadubai/buildings.py`, `datadubai/transform.py` | same building match key |
| ELT write | `geo/buildings.py`, `osm_geo/projects.py` | geocoding containment check (fix above) |
| ELT write | `marts.py` | `_AREA_MART_SQL` GROUP BY → canonical (already planned) |
| API read | `repositories/facts.py` | transactions/rents `area_id` filter → expand |
| API read | `repositories/marts.py` | `area_monthly` (`area_ids`), `building_summary` (`area_id`) → expand |
| API read | `repositories/dimensions.py` | `get_area` → lineage/ambiguity. `list_projects`, `resolve_project` → `area_id` match `OR`'d with `project_area_actual` indirection when the requested id is new/canonical (§ "OR'd with indirection" above); querying an old id stays a plain, unchanged literal match |
| API read | `repositories/buildings.py` | `list_buildings`, `resolve_building` → same `OR`'d-indirection shape, through the building's own `project_id` |
| API read | `repositories/geo.py` | area GeoJSON, project/building points → expand |
| API read | `repositories/analytics.py` | ranking/growth/compare on `entity=area` → expand |
| MCP | — | none; thin passthrough |

## Build order (revision 2)

Done in revision 1, holds unchanged: schema for `area_code_evidence` and the
detector's core (89 candidates, 83 detected, verified live); `_NAME_HINTS`
crutch reverted; MCP needs nothing (thin passthrough confirmed).

Reworked/added for revision 2:

1. Migration: drop `dim_area.superseded_by_area_id`; add
   `area_code_evidence.evidence_project_ids INTEGER[]` (old-area metadata,
   e.g. for a future map view of a superseded area's contents); add new
   table `project_area_actual` (`project_id` PK, `old_area_id`,
   `new_area_id`, `detected_at`) as the operational lookup.
2. Detector: upsert `evidence_project_ids` on the header row **and** one
   `project_area_actual` row per project in the overlap set (already
   computed internally, just needs persisting both ways).
3. **No apply/mutation step.** "Applying" a reviewed pair is flipping
   `area_code_evidence.reviewed = true` — nothing else. `project_area_actual`
   is read-time indirection; `dim_project`/`dim_building` are never written.
   (There is no longer a step here that replaces revision 1's "set `dim_area`
   pointer" mutation — revision 2 has no mutation step of any kind.)
4. Building/project match-key + geocoding-containment fixes: rework to check
   `project_area_actual` (reviewed) first, falling back to the project's own
   stored `area_id`, never writing either.
5. Marts + `expand_area_ids` + analytics self-joins: rework to the
   project-first mechanism (§ above), area-fallback only when an old area's
   successor is unambiguous. `list_projects`/`resolve_project`/
   `list_buildings`/`resolve_building` never raise the ambiguity error and
   never call `expand_area_ids` — but do get the `OR`'d `project_area_actual`
   indirection when queried by a new/canonical area id (§ "OR'd with
   indirection, never ambiguous" above); querying an old id stays unchanged.
6. API/MCP lineage fields: list-shaped (`superseded_areas`/`superseded_by`
   are lists on both sides now, not a single object).
7. **Done.** Re-ran the detector live, reviewed, verified end to end — see
   "Live verification results" below.

## Live verification results

Rebuilt all three images (shared `dxb-core` model changes), applied the
migration, ran the detector live: **84 pairs across 48 distinct old areas,
21 of them genuinely one-to-many** — matching the design's predicted 44%
exactly. All 84 reviewed via the interactive CLI (see below); marts rebuilt
on the full approval set.

Checked, all confirmed correct against live data:

- **Reconciliation is exact, not approximate.** Summed over every
  `mart_area_monthly` row: raw total `sale_cnt` = canonical-grouped total +
  the excluded-ambiguous-old-area residual, to the row
  (`1,341,862 = 988,044 + 353,818`). No loss, no double-count, at full scale.
- **Project-anchored recovery stays disjoint at scale.** MARSA DUBAI's four
  successors recovered 44 / 1 / 8 / 1 projects respectively (Marina /
  Bluewaters / Harbour / JBR) — exact match, project-for-project, to the
  detector's own original evidence counts.
- **Ambiguity holds consistently.** Every one-to-many old area (spot-checked
  MARSA DUBAI plus four more) raises `AmbiguousEntityError` on
  facts/growth/compare, naming every successor, with the candidate count
  matching `area_code_evidence` exactly.
- **Ranking never leaks or duplicates.** Zero superseded old ids and zero
  duplicate canonical ids across 200 ranked areas.
- **Buildings reconcile precisely even where the numbers look surprising at
  first.** JVC (old 31 → new 274): old code literally still shows 1,863
  buildings (never rewritten); new code recovers exactly 289 via
  `project_area_actual` — 288 of those still literally tagged under the old
  code, 1 recovered purely through its project. The remaining ~1,574 either
  have no `project_id` at all (can't be resolved, correctly excluded) or
  belong to projects outside this specific pair's confirmed overlap
  (legitimately unrelated, not a gap in the mechanism).
- **Map features never show a superseded area under its own id** — 0 of 7
  sampled old ids leaked into `/geo/areas`.

### Reviewing detected splits: the interactive CLI

`dxb approve-area-split <old> <new>` is gone — a human had to already know
both numeric ids, which defeats the point of a *review* step. Replaced by a
single interactive command:

```
docker compose exec elt dxb list-area-splits
```

Arrow keys move the highlight over every `area_code_evidence` pair (pending
and already-approved, refreshed after every action); Enter **toggles** the
highlighted pair — approves it if pending, or **reverts** it back to
pending if already approved (`area_codes.revert_area_split`, symmetric with
`approve_area_split`) — Esc/Ctrl-C quits. Revert exists specifically because
Enter-to-approve is fast enough that a misclick needs a one-keystroke undo,
not a trip to SQL: reverting is exactly as safe as approving, since neither
one ever touches anything but `area_code_evidence.reviewed` — nothing to
roll back downstream.
