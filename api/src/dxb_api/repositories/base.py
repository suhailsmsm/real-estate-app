"""Shared repository machinery: pagination and fuzzy entity resolution.

The only layer permitted to import SQLAlchemy (API_DESIGN.md §3, enforced by
the ruff TID251 ban configured in pyproject.toml).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NoReturn

from dxb_core.models import AreaCodeEvidence, DimArea, ProjectAreaActual
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from dxb_api.config import Settings
from dxb_api.errors import AmbiguousEntityError, ValidationError


@dataclass(frozen=True)
class Candidate:
    id: int
    name_en: str
    similarity: float


class BaseRepository:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    # ------------------------------------------------------------ paging

    def _bounded(self, limit: int | None, offset: int | None) -> tuple[int, int]:
        limit = self._settings.default_page_limit if limit is None else limit
        offset = offset or 0
        if limit < 1 or limit > self._settings.max_page_limit:
            raise ValidationError(
                f"limit must be between 1 and {self._settings.max_page_limit}",
                limit=limit,
            )
        if offset < 0:
            raise ValidationError("offset must be >= 0", offset=offset)
        return limit, offset

    async def _page(self, stmt: Select, limit: int, offset: int) -> tuple[list, bool]:
        """Fetch one page plus a sentinel row.

        Asking for limit+1 rows tells us whether more exist without a second
        COUNT query over the same predicate — which on the 12M-row fact tables
        would roughly double the cost of every page.
        """
        rows = (await self._session.execute(stmt.limit(limit + 1).offset(offset))).all()
        return list(rows[:limit]), len(rows) > limit

    async def _count(self, stmt: Select) -> int:
        subq = stmt.order_by(None).subquery()
        return int(
            await self._session.scalar(select(func.count()).select_from(subq)) or 0
        )

    def _check_id_set(self, ids: list[int] | None, param: str) -> list[int] | None:
        if not ids:
            return None
        if len(ids) > self._settings.max_entity_ids:
            raise ValidationError(
                f"{param} accepts at most {self._settings.max_entity_ids} ids; "
                "narrow the selection rather than requesting everything.",
                requested=len(ids),
                max_entity_ids=self._settings.max_entity_ids,
            )
        return ids

    # -------------------------------------------------- fuzzy resolution

    async def _resolve(
        self,
        *,
        query: str,
        name_col,
        id_col,
        base_stmt: Select,
        entity: str,
    ) -> tuple[int, dict]:
        """Turn a `q=` string into exactly one id, or raise 422.

        `similarity()` is used directly with an explicit threshold rather than
        the `%` operator, because `%` consults the session-level
        pg_trgm.similarity_threshold — a hidden global that would make results
        depend on connection state instead of on the request.
        """
        threshold = self._settings.trgm_threshold
        sim = func.similarity(name_col, query).label("sim")
        stmt = (
            base_stmt.add_columns(sim)
            .where(sim >= threshold)
            .order_by(sim.desc(), name_col.asc())
            .limit(5)
        )
        rows = (await self._session.execute(stmt)).all()
        candidates = [
            Candidate(
                id=int(r._mapping[id_col]),
                name_en=r._mapping[name_col],
                similarity=float(r.sim),
            )
            for r in rows
        ]

        if not candidates:
            raise AmbiguousEntityError(
                f"No {entity} matched {query!r} above a similarity of {threshold}.",
                query=query,
                candidates=[],
                hint=(
                    "Trigram matching is lexical, so acronyms and nicknames "
                    "(e.g. 'JVC') do not match. Try the full name, or list the "
                    "dimension endpoint to see the real values."
                ),
            )

        best = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        if (
            runner_up
            and (best.similarity - runner_up.similarity)
            < self._settings.trgm_ambiguity_margin
        ):
            # Guessing here is the hallucination vector: correct arithmetic
            # attached to the wrong entity reads as authoritative.
            raise AmbiguousEntityError(
                f"{query!r} matches more than one {entity} about equally well. "
                "Pass an explicit id, or narrow the query.",
                query=query,
                candidates=[c.__dict__ for c in candidates],
            )

        resolved = {
            "query": query,
            "id": best.id,
            "name_en": best.name_en,
            "similarity": round(best.similarity, 3),
            "runners_up": [c.__dict__ for c in candidates[1:]],
        }
        return best.id, resolved

    # ------------------------------------------------- area-code migration
    #
    # DLD started re-coding ~89 established communities under new area codes
    # from 2026-07-20 onward (docs/AREA_CODE_MIGRATION_ANALYSIS.md, revision
    # 2). An old area can fan out into SEVERAL new ones — 21 of 48 split old
    # areas have more than one reviewed successor — so there is no scalar
    # "canonical id" column on `dim_area` to redirect through; `AreaCodeEvidence`
    # (`reviewed = true` rows) *is* the old->new relationship, queried
    # directly, one-to-many.
    #
    # Three cases per old area id:
    #   0 reviewed successors -> it is not an old/split area at all: itself.
    #   1 reviewed successor  -> genuinely unambiguous: redirects transparently,
    #                            same behaviour as before this rework.
    #   2+ reviewed successors -> no single id to redirect to. Raise
    #                            `AmbiguousEntityError` naming every successor
    #                            rather than guessing or silently returning the
    #                            old area's own (small, residual) rows.
    #
    # Facts (`fact_sale_transaction` / `fact_rent_contract`) are never
    # rewritten, so an old area's rows permanently stay under its own old
    # `area_id` — that is what `expand_area_ids` widens a filter across.
    # Marts are rebuilt project-first-correct by the ELT side, so by the time
    # this API reads them an old area's mart rows are already folded into its
    # sole successor (when unambiguous) or left as an honest small residual
    # (when ambiguous, never merged into any one successor). The
    # self-join helpers below (`_canonical_area_id_expr` /
    # `_canonical_area_alias`) exist for the rare/defensive case where some
    # residual still sits under an already-unambiguous old code.

    async def _successors_map(self, area_ids: list[int]) -> dict[int, list[dict]]:
        """For each of `area_ids`: every reviewed successor (`area_code_evidence`
        row with `reviewed = true`), or an empty list when the id is not an
        old/split area at all. Shared foundation for `resolve_canonical_area_id`,
        `expand_area_ids`, `_canonical_map`, and `successors_of` — one query
        shape, however many ids the caller passes in.
        """
        requested = sorted({int(a) for a in area_ids if a is not None})
        if not requested:
            return {}
        successor = aliased(DimArea)
        rows = (
            await self._session.execute(
                select(
                    AreaCodeEvidence.old_area_id,
                    successor.id,
                    successor.dld_area_code,
                    successor.name_en,
                )
                .join(successor, successor.id == AreaCodeEvidence.new_area_id)
                .where(
                    AreaCodeEvidence.old_area_id.in_(requested),
                    AreaCodeEvidence.reviewed.is_(True),
                )
                .order_by(AreaCodeEvidence.old_area_id.asc(), successor.id.asc())
            )
        ).all()
        out: dict[int, list[dict]] = {rid: [] for rid in requested}
        for r in rows:
            out[r.old_area_id].append(
                {"id": r.id, "dld_area_code": r.dld_area_code, "name_en": r.name_en}
            )
        return out

    async def successors_of(self, old_area_id: int) -> list[dict]:
        """Every reviewed successor of `old_area_id`, regardless of count.

        Empty when the id was never split (or is not an old area at all).
        Used for an old area's own lineage display (`get_area`, always the
        full list — even for the 27 unambiguous ones, for consistency) and
        to build the ambiguity error's candidate list.
        """
        return (await self._successors_map([old_area_id])).get(old_area_id, [])

    def _raise_ambiguous_area(
        self, old_area_id: int, successors: list[dict]
    ) -> NoReturn:
        named = ", ".join(
            f"{s['dld_area_code'] or s['id']} ({s['name_en']})" for s in successors
        )
        raise AmbiguousEntityError(
            f"Area {old_area_id} was subdivided into {len(successors)} current "
            f"areas ({named}) and has no single successor to redirect to. "
            "Query one of the successors directly by id — this old id can "
            "still be looked up on its own for audit/history, but its "
            "aggregated figures cannot be attributed to one canonical area "
            "(AREA_CODE_MIGRATION_ANALYSIS.md).",
            old_area_id=old_area_id,
            candidates=successors,
        )

    async def resolve_canonical_area_id(self, area_id: int) -> int:
        """Resolve one area id forward to its canonical (new-code) id.

        0 reviewed successors -> itself (never split, or not in `dim_area`
        at all — a missing id is left for the caller's own not-found
        handling rather than masked here). 1 -> that successor. 2+ ->
        `AmbiguousEntityError` naming all of them: there is no single
        canonical id to return.
        """
        successors = await self.successors_of(area_id)
        if not successors:
            return area_id
        if len(successors) == 1:
            return successors[0]["id"]
        self._raise_ambiguous_area(area_id, successors)

    async def _canonical_map(self, area_ids: list[int]) -> dict[int, int]:
        """Map each of `area_ids` to its resolved canonical id — itself when
        unsplit, its sole successor when unambiguous. Raises
        `AmbiguousEntityError` (naming the first offending id encountered)
        if any requested id has 2+ reviewed successors.

        Used by callers that need the *mapping* (e.g. reconciling which of
        several requested ids each ended up under) rather than the flat,
        already-expanded id list `expand_area_ids` returns.
        """
        requested = sorted({int(a) for a in area_ids if a is not None})
        if not requested:
            return {}
        successors_map = await self._successors_map(requested)
        for rid in requested:
            if len(successors_map[rid]) > 1:
                self._raise_ambiguous_area(rid, successors_map[rid])
        return {
            rid: (successors_map[rid][0]["id"] if successors_map[rid] else rid)
            for rid in requested
        }

    async def _sole_predecessors(
        self, canonical_ids: set[int]
    ) -> dict[int, list[dict]]:
        """Old areas whose ONLY reviewed successor is each of `canonical_ids`,
        keyed by that canonical id.

        An old area with several successors is excluded even if one of them
        is in `canonical_ids` — it does not belong exclusively to any single
        successor, so it is never listed as "this area's predecessor".
        """
        if not canonical_ids:
            return {}
        predecessor = aliased(DimArea)
        candidate_rows = (
            await self._session.execute(
                select(
                    AreaCodeEvidence.old_area_id,
                    AreaCodeEvidence.new_area_id,
                    predecessor.dld_area_code,
                    predecessor.name_en,
                )
                .join(predecessor, predecessor.id == AreaCodeEvidence.old_area_id)
                .where(
                    AreaCodeEvidence.reviewed.is_(True),
                    AreaCodeEvidence.new_area_id.in_(canonical_ids),
                )
            )
        ).all()
        out: dict[int, list[dict]] = {cid: [] for cid in canonical_ids}
        if not candidate_rows:
            return out
        # Re-check each candidate's FULL successor count (not just whether it
        # points at one of `canonical_ids`) — an old area counts as "sole"
        # predecessor only if this is its one and only reviewed successor.
        full_successors = await self._successors_map(
            [r.old_area_id for r in candidate_rows]
        )
        for r in candidate_rows:
            if len(full_successors.get(r.old_area_id, [])) == 1:
                out[r.new_area_id].append(
                    {
                        "id": r.old_area_id,
                        "dld_area_code": r.dld_area_code,
                        "name_en": r.name_en,
                    }
                )
        return out

    async def superseded_predecessors(self, canonical_area_id: int) -> list[dict]:
        """Every old area whose SOLE reviewed successor is `canonical_area_id`.

        Empty when the area was never split. Shared by the areas dimension
        endpoint (lineage fields) and analytics (the merge caveat) so both
        describe the same relationship the same way.
        """
        return (await self._sole_predecessors({canonical_area_id})).get(
            canonical_area_id, []
        )

    async def expand_area_ids(self, area_ids: list[int]) -> list[int]:
        """Redirect a set of requested area ids to the full, de-duplicated set
        that must be queried to answer for them.

        Raises `AmbiguousEntityError` for the first requested id (in sorted
        order) that has 2+ reviewed successors — there is nothing sensible to
        expand it to.

        For the rest: resolve each to its canonical id (itself when unsplit,
        its sole successor when unambiguous), then expand each canonical id
        back out to every OTHER old id that also points to it as its sole
        successor. The result is `{every canonical id involved} | {every old
        id solely pointing to one of them}` — a redirect, not an additional
        filter: callers replace `WHERE area_id = :id` with
        `WHERE area_id = ANY(:expanded)`, never both, so a row stored under
        an old code is only ever visible through the canonical id's expanded
        set, never through its own id *and* the canonical one at once.
        """
        requested = sorted({int(a) for a in area_ids if a is not None})
        if not requested:
            return []
        successors_map = await self._successors_map(requested)
        for rid in requested:
            if len(successors_map[rid]) > 1:
                self._raise_ambiguous_area(rid, successors_map[rid])

        canonicals = {
            (successors_map[rid][0]["id"] if successors_map[rid] else rid)
            for rid in requested
        }
        predecessors_map = await self._sole_predecessors(canonicals)
        expanded = set(canonicals)
        for preds in predecessors_map.values():
            expanded.update(p["id"] for p in preds)
        return sorted(expanded)

    # -------------------------------------------- project-anchored lookups
    #
    # `dim_project.area_id` / `dim_building.area_id` are NEVER rewritten by
    # this mechanism (CLAUDE.md's "never touch existing data" rule) — a
    # migrated project keeps showing its ORIGINAL area_id forever. Resolution
    # for anything keyed off a project (a project/building's own area lookup,
    # or a fact row that carries a `project_id`) instead reads the read-time
    # indirection table `project_area_actual`, joined to its `(old_area_id,
    # new_area_id)` pair's `area_code_evidence.reviewed` flag. A project
    # resolves to exactly one place by construction (the detector's per-code
    # project sets are disjoint), so this never needs an ambiguity check —
    # unlike an old AREA id, which can.

    def _project_area_actual_subq(self, new_area_id: int) -> Select:
        """Every `project_id` whose reviewed `project_area_actual` mapping
        points at `new_area_id`.

        Embedded directly as an `IN` subquery by callers rather than
        fetched into Python — for an old/superseded id this is simply always
        empty (`project_area_actual.new_area_id` is only ever a NEW code), so
        callers can `OR` it onto a literal match unconditionally without
        first checking whether the requested id is old or new.
        """
        return (
            select(ProjectAreaActual.project_id)
            .join(
                AreaCodeEvidence,
                (AreaCodeEvidence.old_area_id == ProjectAreaActual.old_area_id)
                & (AreaCodeEvidence.new_area_id == ProjectAreaActual.new_area_id),
            )
            .where(
                ProjectAreaActual.new_area_id == new_area_id,
                AreaCodeEvidence.reviewed.is_(True),
            )
        )

    async def _area_scope_filter(self, area_col, project_col, area_id: int):
        """WHERE condition for one area-scoped FACT-shaped filter (a row that
        carries both its own `area_id` and a `project_id`): either the row's
        own (possibly old) area_id is in the widened old+new set
        (`expand_area_ids` — which also raises for a 2+-successor old id), OR
        its project resolves via `project_area_actual` (reviewed) to the
        requested area's canonical id.

        The project half is what correctly attributes a pre-migration row to
        ONE OF SEVERAL successors of an ambiguous old area — something the
        area-level widening alone cannot do, since an ambiguous old area is
        deliberately never treated as any one successor's "sole predecessor"
        (AREA_CODE_MIGRATION_ANALYSIS.md, "resolve through the project
        first"). A project-less row, or one whose project has no reviewed
        mapping, relies solely on the area-level half, unchanged from before
        this rework.
        """
        successors = await self.successors_of(area_id)
        if len(successors) > 1:
            self._raise_ambiguous_area(area_id, successors)
        canonical_id = successors[0]["id"] if successors else area_id
        expanded = await self.expand_area_ids([area_id])
        return or_(
            area_col.in_(expanded),
            project_col.in_(self._project_area_actual_subq(canonical_id)),
        )

    # ---------------------------------- canonicalizing mart/analytics rows

    def _canonical_area_id_expr(self):
        """SQL expression: `dim_area.id`'s canonical id — itself, unless it is
        an old area with exactly one reviewed successor, in which case that
        successor.

        Only safe to evaluate where a 2+-successor id cannot appear in the
        surrounding query — every caller guarantees this: either the id was
        already validated through `expand_area_ids`/`resolve_canonical_area_id`
        (which raise before this expression is ever built), or the
        surrounding query has already filtered out every id that is an
        `old_area_id` in a reviewed row at all (see `_exclude_superseded_areas`).
        """
        sole_successor = (
            select(AreaCodeEvidence.new_area_id)
            .where(
                AreaCodeEvidence.old_area_id == DimArea.id,
                AreaCodeEvidence.reviewed.is_(True),
            )
            .limit(1)
            .scalar_subquery()
        )
        return func.coalesce(sole_successor, DimArea.id)

    def _canonical_area_alias(self) -> tuple[Any, Any]:
        """A second `dim_area`, aliased as "canonical", plus the join
        condition resolving a raw `dim_area.id` row to it — itself, unless
        it's an old area with exactly one reviewed successor. Lets a mart/
        analytics query label and group rows under the canonical area even
        while filtering the mart's own raw `area_id` column across the
        widened (old+new) id set from `expand_area_ids`.
        """
        canonical = aliased(DimArea)
        return canonical, canonical.id == self._canonical_area_id_expr()

    def _exclude_superseded_areas(self, stmt: Select, area_id_col=None) -> Select:
        """Filter out any area that is an `old_area_id` in a reviewed
        `area_code_evidence` row, regardless of how many successors it has.

        Used wherever an old, superseded area must never surface as its own
        entity — map features (`geo.py`) and area ranking (`analytics.py`) —
        rather than being resolved through to one canonical id. This holds
        for both the unambiguous (1-successor) and ambiguous (2+) cases: a
        superseded area is never its own ranked/rendered row either way.
        """
        col = DimArea.id if area_id_col is None else area_id_col
        old_ids = select(AreaCodeEvidence.old_area_id).where(
            AreaCodeEvidence.reviewed.is_(True)
        )
        return stmt.where(col.notin_(old_ids))

    def _exclude_ambiguous_old_areas(self, stmt: Select, area_id_col=None) -> Select:
        """Filter out only an old area with 2+ reviewed successors — never
        one with exactly one.

        Distinct from `_exclude_superseded_areas` above, and not
        interchangeable with it: this is for queries that CANONICALIZE via
        `_canonical_area_alias` (mart/analytics listings with no explicit id
        filter) rather than simply hiding old features. A 1-successor old
        area's row should still naturally canonicalize and merge into its
        successor's bucket — dropping it here would silently discard real
        data. A 2+-successor old area's row has nothing sensible to
        canonicalize to (the self-join's `LIMIT 1` would otherwise pick one
        successor arbitrarily), so it must be excluded outright rather than
        guessed into one bucket.
        """
        col = DimArea.id if area_id_col is None else area_id_col
        ambiguous_old_ids = (
            select(AreaCodeEvidence.old_area_id)
            .where(AreaCodeEvidence.reviewed.is_(True))
            .group_by(AreaCodeEvidence.old_area_id)
            .having(func.count() > 1)
        )
        return stmt.where(col.notin_(ambiguous_old_ids))
