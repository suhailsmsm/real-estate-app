/**
 * Pure map logic: metric selection, colour/height scales, the polygon/dot
 * split, and query-param building.
 *
 * Kept free of MapLibre and React entirely (docs/UI_PLAN.md's testability
 * goal, sharpened by a hard constraint here: jsdom has no WebGL, so a real
 * map can't be constructed in a test at all). Every decision that determines
 * *what the map should show* lives here as a plain function over plain data,
 * so it's covered without a browser. MapView.tsx's job is reduced to piping
 * these results into `source.setData` and `map.setPaintProperty` calls.
 */

import { extent } from "d3-array";
import { scaleLinear, scaleQuantile } from "d3-scale";
import type { QueryValue } from "../../core/client";
import type { MapState, MapSemantics } from "../../core/viewstate";
import type { AreaFeature, AreaFeatureCollection, AreaProperties } from "./types";

// ------------------------------------------------------------- numeric coercion

/**
 * Most of this API sends Postgres NUMERIC as a JSON string; `/geo/areas`
 * currently doesn't (types.ts). Coercing unconditionally means this view
 * survives either serialisation without a silent NaN collapsing the colour
 * scale's domain — the exact bug the task brief warns about.
 */
export function toNumberOrNull(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

// ------------------------------------------------------------------ metrics

export interface MetricSpec {
  field: "sale_median_price_m2" | "rent_median_annual_m2" | "gross_yield_pct";
  countField: "sale_cnt" | "rent_cnt";
  label: string;
  unit: string;
}

/** Which mart column each semantics mode paints, and how to label it. */
export const METRIC_BY_SEMANTICS: Record<MapSemantics, MetricSpec> = {
  sales: {
    field: "sale_median_price_m2",
    countField: "sale_cnt",
    label: "Sale price",
    unit: "AED/m²",
  },
  rents: {
    field: "rent_median_annual_m2",
    countField: "rent_cnt",
    label: "Annual rent",
    unit: "AED/m²/yr",
  },
  yield: {
    field: "gross_yield_pct",
    countField: "sale_cnt",
    label: "Gross yield",
    unit: "%",
  },
};

/**
 * The metric value for one area under the current semantics, or `null`.
 *
 * `null` means two different things get merged deliberately at this layer:
 * "no qualifying mart row" (the properties key is absent) and "a
 * non-finite/blank value" (defensive — hasn't been observed live, but a
 * NUMERIC column can carry NULL). Both mean the same thing to a renderer:
 * there is nothing to plot. What must NOT be merged in here is 0 — a real,
 * observed value (gross_yield_pct legitimately hits 0 for some areas) — so
 * this returns `0`, not `null`, when the field is present and zero.
 */
export function metricValue(props: AreaProperties, semantics: MapSemantics): number | null {
  return toNumberOrNull(props[METRIC_BY_SEMANTICS[semantics].field]);
}

export function sampleCount(props: AreaProperties, semantics: MapSemantics): number | null {
  return toNumberOrNull(props[METRIC_BY_SEMANTICS[semantics].countField]);
}

// ------------------------------------------------------------ dot vs polygon

export interface PartitionedAreas {
  polygons: AreaFeature[];
  dots: AreaFeature[];
}

/**
 * Split area features by whether they carry a real boundary.
 *
 * This is the load-bearing honesty check (task brief): `has_boundary: false`
 * means the geometry MapLibre received is already a centroid fallback (the
 * API's own `geo_level=polygon` behaviour), never a synthesized shape. Every
 * feature lands in exactly one bucket — nothing is dropped, and nothing
 * without a boundary is ever routed to the polygon layer, which would
 * silently render a fabricated area outline.
 */
export function partitionAreaFeatures(fc: AreaFeatureCollection | undefined | null): PartitionedAreas {
  const polygons: AreaFeature[] = [];
  const dots: AreaFeature[] = [];
  for (const f of fc?.features ?? []) {
    (f.properties.has_boundary ? polygons : dots).push(f);
  }
  return { polygons, dots };
}

/** Every non-null metric value across a set of features, for scale domains. */
export function collectMetricValues(features: AreaFeature[], semantics: MapSemantics): number[] {
  const out: number[] = [];
  for (const f of features) {
    const v = metricValue(f.properties, semantics);
    if (v !== null) out.push(v);
  }
  return out;
}

// --------------------------------------------------------------- colour ramp

/**
 * Five-stop red ramp, light to dark — chosen over the app's own ochre accent
 * (`--accent`, theme.css) on direct request, so the choropleth's colour
 * carries only "how expensive," never doubling as the app's own interactive-
 * element accent (which stays ochre everywhere else, e.g. buttons).
 */
export const RED_RAMP = ["#fdece8", "#f3b3a3", "#e37a5e", "#c63f2e", "#7a1f16"];

/**
 * Sentinel for "no metric value" — deliberately a cool grey outside the warm
 * ramp entirely, so it can never be mistaken for a real (possibly low) value
 * on the ramp. This is what makes "no data" visibly different from "zero"
 * (task brief) at the colour layer, on top of the `null`-vs-`0` split
 * `metricValue` already makes.
 */
export const NO_DATA_COLOR = "#9aa5a9";

export type ColorScale = ReturnType<typeof scaleQuantile<string>>;

/**
 * Quantile rather than linear: sale price per m² is heavily right-skewed
 * (live domain ran ~511 to ~102,728 AED/m²), so a linear scale would paint
 * almost everything the same pale colour and reserve the dark end for a
 * handful of outliers. Quantile buckets keep every bucket populated and the
 * map legible, at the cost of the legend showing data-dependent breakpoints
 * rather than round numbers — worth it here.
 */
export function buildColorScale(values: number[]): ColorScale | null {
  if (values.length === 0) return null;
  return scaleQuantile<string>().domain(values).range(RED_RAMP);
}

export function colorForValue(scale: ColorScale | null, value: number | null): string {
  if (value === null || !scale) return NO_DATA_COLOR;
  return scale(value);
}

export interface LegendStop {
  color: string;
  from: number;
  to: number;
}

/** Real, data-derived bucket boundaries for the legend — never invented. */
export function legendStops(scale: ColorScale): LegendStop[] {
  return scale.range().map((color) => {
    const [from, to] = scale.invertExtent(color);
    return { color, from, to };
  });
}

// ---------------------------------------------------------- height encoding

const MIN_EXTRUSION_M = 200;
const MAX_EXTRUSION_M = 6000;
const DOT_RADIUS_MIN = 5;
const DOT_RADIUS_MAX = 16;
/** Fixed dot size in `color` encoding, and for areas with no metric value. */
export const DOT_RADIUS_FLAT = 6;

/**
 * `fill-extrusion` only accepts polygon geometry — a boundary-less area's
 * centroid dot has nothing to extrude. Rather than approximate a shape to
 * extrude (forbidden by the honesty requirement) or silently drop the
 * height signal, dots get a *sized* circle instead when encoding is
 * `height`: still visibly a dot (never a polygon), still carries the value.
 */
export function buildHeightScale(values: number[]): ((v: number) => number) | null {
  if (values.length === 0) return null;
  const [lo, hi] = extent(values);
  if (lo === undefined || hi === undefined) return null;
  if (lo === hi) return () => MIN_EXTRUSION_M;
  const s = scaleLinear().domain([lo, hi]).range([MIN_EXTRUSION_M, MAX_EXTRUSION_M]).clamp(true);
  return (v: number) => s(v);
}

export function buildRadiusScale(values: number[]): ((v: number) => number) | null {
  if (values.length === 0) return null;
  const [lo, hi] = extent(values);
  if (lo === undefined || hi === undefined) return null;
  if (lo === hi) return () => DOT_RADIUS_MIN;
  const s = scaleLinear().domain([lo, hi]).range([DOT_RADIUS_MIN, DOT_RADIUS_MAX]).clamp(true);
  return (v: number) => s(v);
}

// --------------------------------------------------------- feature styling

/** Extra properties this view injects into every area feature before handing
 * it to MapLibre, so paint expressions are trivial `['get', '_x']` lookups
 * instead of re-deriving colour/height inside a GL expression. */
export interface StyledAreaProperties extends AreaProperties {
  _metricValue: number | null;
  _sample: number | null;
  _color: string;
  _height: number;
  _radius: number;
}

export interface StyledAreaFeature extends Omit<AreaFeature, "properties"> {
  properties: StyledAreaProperties;
}

export interface AreaStylingContext {
  semantics: MapSemantics;
  encoding: MapState["encoding"];
  colorScale: ColorScale | null;
  heightScale: ((v: number) => number) | null;
  radiusScale: ((v: number) => number) | null;
}

export function styleAreaFeatures(features: AreaFeature[], ctx: AreaStylingContext): StyledAreaFeature[] {
  return features.map((f) => {
    const value = metricValue(f.properties, ctx.semantics);
    const wantsSize = ctx.encoding === "height" && value !== null;
    return {
      ...f,
      properties: {
        ...f.properties,
        _metricValue: value,
        _sample: sampleCount(f.properties, ctx.semantics),
        _color: colorForValue(ctx.colorScale, value),
        _height: wantsSize && ctx.heightScale ? ctx.heightScale(value) : 0,
        _radius: wantsSize && ctx.radiusScale ? ctx.radiusScale(value) : DOT_RADIUS_FLAT,
      },
    };
  });
}

// ------------------------------------------------------------- query params

/** Builds `/geo/areas` params from map state. `geo_level` is always
 * `"polygon"`: the API falls back to a centroid automatically when an area
 * has no boundary (confirmed live), which is exactly the honest-degrade
 * behaviour this view needs — no separate `"point"` request required. */
export function buildAreasParams(map: MapState): Record<string, QueryValue> {
  return {
    geo_level: "polygon",
    usage: map.usage ?? undefined,
    month_from: map.monthFrom ?? undefined,
    month_to: map.monthTo ?? undefined,
    min_sample: map.minSample ?? undefined,
  };
}

/** `/geo/buildings` is unbounded without a scope — pass the selected area
 * when there is one so the payload stays proportional to what's on screen. */
export function buildBuildingsParams(map: MapState): Record<string, QueryValue> {
  return {
    area_id: map.selectedAreaId ?? undefined,
  };
}

// ------------------------------------------------------------------ layer ids

export const SOURCE_ID = {
  areaPolygons: "dxb-area-polygons",
  areaDots: "dxb-area-dots",
  projects: "dxb-projects",
  buildings: "dxb-buildings",
} as const;

export const LAYER_ID = {
  areaFill: "dxb-area-fill",
  areaExtrusion: "dxb-area-extrusion",
  areaOutline: "dxb-area-outline",
  areaDots: "dxb-area-dots-circle",
  projects: "dxb-projects-circle",
  buildings: "dxb-buildings-circle",
} as const;
