/**
 * The ViewState reducer — the single channel through which state changes.
 *
 * Every mutation goes through here: a click, a URL restore, and (later) a
 * copilot patch all take the same path (docs/UI_PLAN.md §5). That is what
 * makes undo work identically regardless of who made the change, and what
 * stops the copilot needing a private back door into the UI.
 *
 * `applyPatch` is the copilot's entry point. It is a deep merge, validated
 * against the ViewState schema — an LLM-produced patch is untrusted input and
 * is checked exactly like any other. An invalid patch is rejected whole, never
 * partially applied, so the UI can never land in a state no user could reach.
 */

import {
  defaultColumns,
  ENTITIES,
  missingRequiredFilter,
} from "./entities";
import {
  type ListingEntity,
  type ListingFilters,
  type Sort,
  type ViewName,
  type ViewState,
  viewStateSchema,
} from "./viewstate";

export type Action =
  | { type: "setView"; view: ViewName }
  | { type: "listing/setEntity"; entity: ListingEntity }
  | { type: "listing/setFilter"; key: keyof ListingFilters; value: unknown }
  | { type: "listing/clearFilters" }
  | { type: "listing/setColumns"; columns: string[] }
  | { type: "listing/setSort"; sort: Sort | null }
  | { type: "listing/setPage"; offset: number }
  | { type: "listing/setLimit"; limit: number }
  | { type: "dashboard/set"; patch: Partial<ViewState["dashboard"]> }
  | { type: "map/set"; patch: Partial<ViewState["map"]> }
  | { type: "replace"; state: ViewState }
  | { type: "patch"; patch: DeepPartial<ViewState> };

export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends object ? (T[K] extends unknown[] ? T[K] : DeepPartial<T[K]>) : T[K];
};

/** Plain-object check — arrays and null are values to replace, not merge. */
function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function deepMerge<T>(base: T, patch: DeepPartial<T>): T {
  if (!isPlainObject(base) || !isPlainObject(patch)) return patch as T;
  const out: Record<string, unknown> = { ...base };
  for (const [k, v] of Object.entries(patch)) {
    if (v === undefined) continue;
    out[k] = isPlainObject(v) && isPlainObject(out[k]) ? deepMerge(out[k], v) : v;
  }
  return out as T;
}

/**
 * Switching entity resets the parts of listing state that are entity-specific.
 *
 * Filters, columns and sort all reference field names that only exist for the
 * previous entity — carrying them over would send `txn_group` to the rents
 * endpoint (a 422) or sort by a column that isn't in the new table. The reset
 * also seeds whatever `requiresOneOf` demands, so the first request after a
 * switch is valid by construction rather than erroring on arrival.
 */
function switchEntity(state: ViewState["listing"], entity: ListingEntity): ViewState["listing"] {
  const filters: ListingFilters = {};
  if (missingRequiredFilter(entity, filters)) {
    const required = ENTITIES[entity].requiresOneOf!;
    // Prefer a date bound over an entity id: it is the one filter that is
    // always satisfiable without the user first picking something.
    const dateKey = required.find((k) => k.includes("date"));
    if (dateKey) {
      (filters as Record<string, unknown>)[dateKey] = state.filters.date_from ??
        state.filters.start_date_from ??
        isoMonthsAgoLocal(12);
    }
  }
  return {
    entity,
    filters,
    columns: defaultColumns(entity),
    sort: null,
    limit: state.limit,
    offset: 0,
  };
}

function isoMonthsAgoLocal(months: number): string {
  const n = new Date();
  return new Date(Date.UTC(n.getUTCFullYear(), n.getUTCMonth() - months, n.getUTCDate()))
    .toISOString()
    .slice(0, 10);
}

/** Any change to what is being queried must return to page 1. */
function resetPage(l: ViewState["listing"]): ViewState["listing"] {
  return { ...l, offset: 0 };
}

export function reducer(state: ViewState, action: Action): ViewState {
  switch (action.type) {
    case "setView":
      return { ...state, view: action.view };

    case "listing/setEntity":
      if (action.entity === state.listing.entity) return state;
      return { ...state, listing: switchEntity(state.listing, action.entity) };

    case "listing/setFilter": {
      const filters = { ...state.listing.filters };
      if (action.value === undefined || action.value === null || action.value === "") {
        delete filters[action.key];
      } else {
        (filters as Record<string, unknown>)[action.key] = action.value;
      }
      return { ...state, listing: resetPage({ ...state.listing, filters }) };
    }

    case "listing/clearFilters":
      return { ...state, listing: resetPage({ ...state.listing, filters: {} }) };

    case "listing/setColumns":
      return { ...state, listing: { ...state.listing, columns: action.columns } };

    case "listing/setSort":
      return { ...state, listing: { ...state.listing, sort: action.sort } };

    case "listing/setPage":
      return { ...state, listing: { ...state.listing, offset: Math.max(0, action.offset) } };

    case "listing/setLimit":
      return { ...state, listing: resetPage({ ...state.listing, limit: action.limit }) };

    case "dashboard/set":
      return { ...state, dashboard: { ...state.dashboard, ...action.patch } };

    case "map/set":
      return { ...state, map: { ...state.map, ...action.patch } };

    case "replace":
      return action.state;

    case "patch":
      return applyPatch(state, action.patch);

    default:
      return state;
  }
}

export interface PatchResult {
  state: ViewState;
  ok: boolean;
  error?: string;
}

/**
 * Merge a partial ViewState and validate the result.
 *
 * Used by the copilot and by URL restore. Validation happens on the *merged*
 * result rather than the patch alone, because a patch is only meaningful in
 * context — `{listing: {sort: {column: "x"}}}` is not a valid Sort on its own
 * but is fine merged onto an existing one.
 */
export function applyPatch(state: ViewState, patch: DeepPartial<ViewState>): ViewState {
  const result = validatePatch(state, patch);
  return result.ok ? result.state : state;
}

export function validatePatch(state: ViewState, patch: DeepPartial<ViewState>): PatchResult {
  let merged: ViewState;
  try {
    merged = deepMerge(state, patch);
  } catch (e) {
    return { state, ok: false, error: `Patch could not be merged: ${String(e)}` };
  }
  const parsed = viewStateSchema.safeParse(merged);
  if (!parsed.success) {
    const issue = parsed.error.issues[0];
    return {
      state,
      ok: false,
      error: `Invalid patch at ${issue.path.join(".") || "(root)"}: ${issue.message}`,
    };
  }
  return { state: parsed.data, ok: true };
}
