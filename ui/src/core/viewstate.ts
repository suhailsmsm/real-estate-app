/**
 * ViewState — the single serializable description of what the UI is showing.
 *
 * Everything the user can configure lives here, in one plain JSON object, and
 * the UI is a pure function of it (docs/UI_PLAN.md §1). That buys four things
 * at once: shareable URLs (one codec, `url.ts`), undo/redo (it's a reducer
 * over a JSON object, `reducer.ts`), testability (assert on state, no browser
 * needed), and the copilot (the bot emits *patches* — the same channel a click
 * goes through, never a private back door).
 *
 * Two rules keep those properties true:
 *
 *   1. No non-serializable values. No Dates, no Maps, no functions, no class
 *      instances — dates are ISO `YYYY-MM-DD` strings. If it can't survive
 *      `JSON.parse(JSON.stringify(x))`, it doesn't belong here.
 *   2. Every capability is reachable by a patch. If a view can do something
 *      that isn't expressible as ViewState, the copilot can't drive it and a
 *      shared URL won't reproduce it.
 *
 * All three views' state is held simultaneously, not just the active one, so
 * switching views and coming back preserves what you had configured.
 */

import { z } from "zod";

// ---------------------------------------------------------------- listing

export const LISTING_ENTITIES = [
  "transactions",
  "rents",
  "buildings",
  "projects",
  "areas",
] as const;

export type ListingEntity = (typeof LISTING_ENTITIES)[number];

/**
 * Every filter any listing entity supports, flat and all-optional.
 *
 * Deliberately one flat shape rather than a per-entity discriminated union:
 * a flat object is trivial to patch, to encode into a URL, and for the
 * copilot to emit. Which subset is *meaningful* for the current entity is a
 * presentation concern, declared per entity in `entities.ts` and used to
 * decide which controls to render — not something the state shape needs to
 * enforce.
 *
 * Names match the API's query parameters exactly (verified against
 * /openapi.json), so building a request is a filter-and-copy, never a
 * translation table that can drift.
 */
export const listingFiltersSchema = z
  .object({
    q: z.string(),
    area_id: z.number().int(),
    project_id: z.number().int(),
    building_id: z.number().int(),
    developer_id: z.number().int(),
    date_from: z.string(),
    date_to: z.string(),
    start_date_from: z.string(),
    start_date_to: z.string(),
    txn_group: z.string(),
    version: z.string(),
    usage: z.string(),
    property_type_id: z.number().int(),
    is_offplan: z.boolean(),
    is_master: z.boolean(),
    status: z.string(),
    rooms: z.string(),
    price_min: z.number(),
    price_max: z.number(),
    area_m2_min: z.number(),
    area_m2_max: z.number(),
    annual_amount_min: z.number(),
    annual_amount_max: z.number(),
    include_future: z.boolean(),
    has_geo_data: z.boolean(),
  })
  .partial();

export type ListingFilters = z.infer<typeof listingFiltersSchema>;

export const sortSchema = z.object({
  column: z.string(),
  desc: z.boolean(),
});

export type Sort = z.infer<typeof sortSchema>;

export const listingStateSchema = z.object({
  entity: z.enum(LISTING_ENTITIES),
  filters: listingFiltersSchema,
  /** Visible column ids, in display order. Empty means "the entity's default set". */
  columns: z.array(z.string()),
  /** Client-side sort — sorts the loaded page, not the full result set (docs/UI_PLAN.md §4.1). */
  sort: sortSchema.nullable(),
  limit: z.number().int().min(1).max(500),
  offset: z.number().int().min(0),
});

export type ListingState = z.infer<typeof listingStateSchema>;

// -------------------------------------------------------------- dashboard

export const DASHBOARD_METRICS = [
  "sale_median_price_m2",
  "rent_median_annual_m2",
  "gross_yield_pct",
  "sale_cnt",
  "rent_cnt",
] as const;

export type DashboardMetric = (typeof DASHBOARD_METRICS)[number];

/** Absolute level vs month-over-month vs year-over-year percentage change. */
export const DASHBOARD_TRANSFORMS = ["level", "mom_pct", "yoy_pct"] as const;
export type DashboardTransform = (typeof DASHBOARD_TRANSFORMS)[number];

export const dashboardStateSchema = z.object({
  entityType: z.enum(["area", "project"]),
  /** Compared side by side, one series each. */
  entityIds: z.array(z.number().int()),
  metric: z.enum(DASHBOARD_METRICS),
  transform: z.enum(DASHBOARD_TRANSFORMS),
  monthFrom: z.string().nullable(),
  monthTo: z.string().nullable(),
  usage: z.string().nullable(),
  minSample: z.number().int().min(0).nullable(),
  includeFuture: z.boolean(),
});

export type DashboardState = z.infer<typeof dashboardStateSchema>;

// -------------------------------------------------------------------- map

export const MAP_GRANULARITIES = ["areas", "projects", "buildings"] as const;
export const MAP_SEMANTICS = ["sales", "rents", "yield"] as const;
export const MAP_ENCODINGS = ["color", "height"] as const;

export type MapGranularity = (typeof MAP_GRANULARITIES)[number];
export type MapSemantics = (typeof MAP_SEMANTICS)[number];
export type MapEncoding = (typeof MAP_ENCODINGS)[number];

export const mapStateSchema = z.object({
  granularity: z.enum(MAP_GRANULARITIES),
  semantics: z.enum(MAP_SEMANTICS),
  encoding: z.enum(MAP_ENCODINGS),
  monthFrom: z.string().nullable(),
  monthTo: z.string().nullable(),
  usage: z.string().nullable(),
  minSample: z.number().int().min(0).nullable(),
  /** Serializable camera — part of the state so a shared link lands where you were looking. */
  viewport: z.object({
    lng: z.number(),
    lat: z.number(),
    zoom: z.number(),
    pitch: z.number(),
    bearing: z.number(),
  }),
  selectedAreaId: z.number().int().nullable(),
});

export type MapState = z.infer<typeof mapStateSchema>;

// ------------------------------------------------------------- the whole

export const VIEWS = ["listing", "dashboard", "map"] as const;
export type ViewName = (typeof VIEWS)[number];

export const viewStateSchema = z.object({
  view: z.enum(VIEWS),
  listing: listingStateSchema,
  dashboard: dashboardStateSchema,
  map: mapStateSchema,
});

export type ViewState = z.infer<typeof viewStateSchema>;

/** Dubai, framed to fit the built-up coastline strip where the data actually is. */
export const DUBAI_VIEWPORT = {
  lng: 55.2708,
  lat: 25.15,
  zoom: 9.4,
  pitch: 0,
  bearing: 0,
} as const;

export function defaultViewState(): ViewState {
  return {
    view: "listing",
    listing: {
      entity: "transactions",
      // Facts endpoints refuse an unfiltered scan (422, "required_one_of"), so
      // the default state must already satisfy that — otherwise the app's very
      // first request fails and looks broken. Twelve months back is both a
      // valid filter and a sensible opening view.
      filters: { date_from: isoMonthsAgo(12) },
      columns: [],
      sort: null,
      limit: 50,
      offset: 0,
    },
    dashboard: {
      entityType: "area",
      entityIds: [],
      metric: "sale_median_price_m2",
      transform: "level",
      monthFrom: null,
      monthTo: null,
      usage: null,
      minSample: null,
      includeFuture: false,
    },
    map: {
      granularity: "areas",
      semantics: "sales",
      encoding: "color",
      monthFrom: null,
      monthTo: null,
      usage: null,
      minSample: null,
      viewport: { ...DUBAI_VIEWPORT },
      selectedAreaId: null,
    },
  };
}

/** `YYYY-MM-DD`, n months before today. Pure string math on the ISO form. */
export function isoMonthsAgo(months: number, from = new Date()): string {
  const d = new Date(
    Date.UTC(from.getUTCFullYear(), from.getUTCMonth() - months, from.getUTCDate()),
  );
  return d.toISOString().slice(0, 10);
}
