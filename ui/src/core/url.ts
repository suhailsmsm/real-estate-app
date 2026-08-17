/**
 * ViewState ⇄ URL — one codec, so every configuration is a shareable link.
 *
 * The encoding is sparse: only values that differ from the defaults are
 * written, which keeps the common case short and readable
 * (`?view=map&granularity=areas`) instead of a wall of every field. Decoding
 * is total — anything missing, unknown or malformed falls back to the default
 * rather than throwing, because a URL is untrusted input that a user can hand-
 * edit, and a bad link should open the app, not break it.
 */

import { deepMerge, type DeepPartial, validatePatch } from "./reducer";
import { defaultViewState, type ViewState, viewStateSchema } from "./viewstate";

/** Filters go in as `f.<name>` so they never collide with view-level keys. */
const FILTER_PREFIX = "f.";

function num(v: string | null): number | undefined {
  if (v === null || v.trim() === "") return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

function bool(v: string | null): boolean | undefined {
  if (v === "true" || v === "1") return true;
  if (v === "false" || v === "0") return false;
  return undefined;
}

function ints(v: string | null): number[] | undefined {
  if (!v) return undefined;
  const parts = v
    .split(",")
    .map((s) => Number(s.trim()))
    .filter((n) => Number.isFinite(n));
  return parts.length ? parts : undefined;
}

export function encodeViewState(state: ViewState): string {
  const d = defaultViewState();
  const p = new URLSearchParams();
  const set = (k: string, v: unknown, dflt: unknown) => {
    if (v === null || v === undefined) return;
    if (JSON.stringify(v) === JSON.stringify(dflt)) return;
    p.set(k, String(v));
  };

  set("view", state.view, d.view);

  if (state.view === "listing") {
    const l = state.listing;
    set("entity", l.entity, d.listing.entity);
    set("limit", l.limit, d.listing.limit);
    set("offset", l.offset, d.listing.offset);
    if (l.sort) p.set("sort", `${l.sort.column}:${l.sort.desc ? "desc" : "asc"}`);
    if (l.columns.length) p.set("cols", l.columns.join(","));
    for (const [k, v] of Object.entries(l.filters)) {
      if (v !== undefined && v !== null && v !== "") p.set(FILTER_PREFIX + k, String(v));
    }
  } else if (state.view === "dashboard") {
    const b = state.dashboard;
    set("etype", b.entityType, d.dashboard.entityType);
    if (b.entityIds.length) p.set("ids", b.entityIds.join(","));
    set("metric", b.metric, d.dashboard.metric);
    set("transform", b.transform, d.dashboard.transform);
    set("from", b.monthFrom, d.dashboard.monthFrom);
    set("to", b.monthTo, d.dashboard.monthTo);
    set("usage", b.usage, d.dashboard.usage);
    set("minSample", b.minSample, d.dashboard.minSample);
    set("future", b.includeFuture, d.dashboard.includeFuture);
  } else {
    const m = state.map;
    set("gran", m.granularity, d.map.granularity);
    set("sem", m.semantics, d.map.semantics);
    set("enc", m.encoding, d.map.encoding);
    set("from", m.monthFrom, d.map.monthFrom);
    set("to", m.monthTo, d.map.monthTo);
    set("usage", m.usage, d.map.usage);
    set("minSample", m.minSample, d.map.minSample);
    set("sel", m.selectedAreaId, d.map.selectedAreaId);
    // Rounded: full float precision makes an unreadable URL and the extra
    // digits are below what the camera can even resolve.
    const v = m.viewport;
    const dv = d.map.viewport;
    if (JSON.stringify(v) !== JSON.stringify(dv)) {
      p.set(
        "cam",
        [
          v.lng.toFixed(4),
          v.lat.toFixed(4),
          v.zoom.toFixed(2),
          v.pitch.toFixed(0),
          v.bearing.toFixed(0),
        ].join(","),
      );
    }
  }

  const s = p.toString();
  return s ? `?${s}` : "";
}

export function decodeViewState(search: string): ViewState {
  const base = defaultViewState();
  const p = new URLSearchParams(search);
  const patch: DeepPartial<ViewState> = {};

  const view = p.get("view");
  if (view === "listing" || view === "dashboard" || view === "map") patch.view = view;

  // Listing
  const listing: Record<string, unknown> = {};
  const entity = p.get("entity");
  if (entity) listing.entity = entity;
  const limit = num(p.get("limit"));
  if (limit !== undefined) listing.limit = limit;
  const offset = num(p.get("offset"));
  if (offset !== undefined) listing.offset = offset;
  const sort = p.get("sort");
  if (sort) {
    const [column, dir] = sort.split(":");
    if (column) listing.sort = { column, desc: dir === "desc" };
  }
  const cols = p.get("cols");
  if (cols) listing.columns = cols.split(",").filter(Boolean);

  const filters: Record<string, unknown> = {};
  for (const [k, v] of p.entries()) {
    if (!k.startsWith(FILTER_PREFIX)) continue;
    const key = k.slice(FILTER_PREFIX.length);
    // Coerce to the type the schema expects; zod rejects anything that ends up
    // wrong, and the whole patch falls back to defaults rather than half-applying.
    if (/_id$/.test(key) || key === "property_type_id") filters[key] = num(v);
    else if (/^(price|area_m2|annual_amount)_(min|max)$/.test(key)) filters[key] = num(v);
    else if (/^(is_offplan|is_master|include_future|has_geo_data)$/.test(key)) filters[key] = bool(v);
    else filters[key] = v;
  }
  if (Object.keys(filters).length) listing.filters = filters;
  if (Object.keys(listing).length) patch.listing = listing as DeepPartial<ViewState>["listing"];

  // Dashboard
  const dash: Record<string, unknown> = {};
  const etype = p.get("etype");
  if (etype) dash.entityType = etype;
  const ids = ints(p.get("ids"));
  if (ids) dash.entityIds = ids;
  const metric = p.get("metric");
  if (metric) dash.metric = metric;
  const transform = p.get("transform");
  if (transform) dash.transform = transform;
  const usage = p.get("usage");
  const from = p.get("from");
  const to = p.get("to");
  const minSample = num(p.get("minSample"));
  const future = bool(p.get("future"));
  if (view === "dashboard") {
    if (from) dash.monthFrom = from;
    if (to) dash.monthTo = to;
    if (usage) dash.usage = usage;
    if (minSample !== undefined) dash.minSample = minSample;
    if (future !== undefined) dash.includeFuture = future;
  }
  if (Object.keys(dash).length) patch.dashboard = dash as DeepPartial<ViewState>["dashboard"];

  // Map
  const map: Record<string, unknown> = {};
  const gran = p.get("gran");
  if (gran) map.granularity = gran;
  const sem = p.get("sem");
  if (sem) map.semantics = sem;
  const enc = p.get("enc");
  if (enc) map.encoding = enc;
  if (view === "map") {
    if (from) map.monthFrom = from;
    if (to) map.monthTo = to;
    if (usage) map.usage = usage;
    if (minSample !== undefined) map.minSample = minSample;
  }
  const sel = num(p.get("sel"));
  if (sel !== undefined) map.selectedAreaId = sel;
  const cam = p.get("cam");
  if (cam) {
    const [lng, lat, zoom, pitch, bearing] = cam.split(",").map(Number);
    if ([lng, lat, zoom, pitch, bearing].every((n) => Number.isFinite(n))) {
      map.viewport = { lng, lat, zoom, pitch, bearing };
    }
  }
  if (Object.keys(map).length) patch.map = map as DeepPartial<ViewState>["map"];

  const result = validatePatch(base, patch);
  if (result.ok) return result.state;

  // A hand-edited or stale link: keep whatever parts are individually valid
  // rather than discarding the whole thing, then fall back to defaults for the
  // rest. Opening the app is always better than showing an error page.
  const salvaged = viewStateSchema.safeParse(deepMerge(base, { view: patch.view }));
  return salvaged.success ? salvaged.data : base;
}
