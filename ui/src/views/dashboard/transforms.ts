/**
 * Pure data shaping for the dashboard chart.
 *
 * Kept free of React and Plot on purpose: the two things that are easy to get
 * wrong here — coercing the API's stringified numerics, and the MoM/YoY
 * percentage math — are exactly what needs the tightest unit tests, and pure
 * functions are trivial to test without mounting anything.
 */

import type { components } from "../../core/api-types";
import type { DashboardMetric, DashboardTransform } from "../../core/viewstate";

export type MartRow = components["schemas"]["MartRow"];

export interface SeriesPoint {
  /** `YYYY-MM-01`. */
  month: string;
  /**
   * `null` marks a month with no qualifying row — a genuine gap, not a zero.
   * Plot's line mark breaks the line wherever y is null/NaN, which is exactly
   * what a gap should look like: an absence, never a plunge to zero.
   */
  value: number | null;
}

export interface EntitySeries {
  entityId: number;
  name: string;
  /** In the currently selected transform (level / mom_pct / yoy_pct). */
  points: SeriesPoint[];
  /** Always the raw metric level, independent of the chart's transform — the
   * summary card states a fact about the metric, not about what the chart
   * happens to be plotting. */
  latestValue: number | null;
  latestMonth: string | null;
  /** Year-over-year change of the level series, ending at latestMonth. */
  yoyChangePct: number | null;
  /** Sample size backing latestValue. */
  sampleSize: number | null;
}

function toNumber(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Pulls one metric's value off a row, coercing the API's numeric-as-string encoding. */
export function metricValue(row: MartRow, metric: DashboardMetric): number | null {
  switch (metric) {
    case "sale_median_price_m2":
      return toNumber(row.sale_median_price_m2);
    case "rent_median_annual_m2":
      return toNumber(row.rent_median_annual_m2);
    case "gross_yield_pct":
      return toNumber(row.gross_yield_pct);
    case "sale_cnt":
      return toNumber(row.sale_cnt);
    case "rent_cnt":
      return toNumber(row.rent_cnt);
  }
}

/**
 * The transaction count backing a metric value — never quote a median
 * without it (docs: `_cnt / sample_size` in /meta/metrics).
 *
 * gross_yield_pct divides a rent median by a sale median drawn from two
 * different samples; the weaker of the two leg counts is what actually
 * bounds how much to trust the ratio, so that side wins rather than
 * averaging or summing them.
 */
export function metricSampleSize(row: MartRow, metric: DashboardMetric): number | null {
  switch (metric) {
    case "sale_median_price_m2":
    case "sale_cnt":
      return toNumber(row.sale_cnt);
    case "rent_median_annual_m2":
    case "rent_cnt":
      return toNumber(row.rent_cnt);
    case "gross_yield_pct": {
      const sale = toNumber(row.sale_cnt);
      const rent = toNumber(row.rent_cnt);
      if (sale === null) return rent;
      if (rent === null) return sale;
      return Math.min(sale, rent);
    }
  }
}

/** Months-since-epoch as an integer, so gap-filling and lag lookups are index arithmetic. */
function monthKey(iso: string): number {
  const y = Number(iso.slice(0, 4));
  const m = Number(iso.slice(5, 7));
  return y * 12 + (m - 1);
}

function keyToMonth(key: number): string {
  const y = Math.floor(key / 12);
  const m = (key % 12) + 1;
  return `${y}-${String(m).padStart(2, "0")}-01`;
}

/**
 * Percentage change `lag` months back. Returns null (a break, not a spike)
 * when either endpoint is missing, and when the prior value is exactly zero
 * — a metric that was zero would otherwise divide-by-zero into Infinity/NaN
 * and either crash the axis scale or render as a nonsensical spike.
 */
export function computeChangeSeries(values: (number | null)[], lag: number): (number | null)[] {
  return values.map((v, i) => {
    if (i < lag) return null;
    const prev = values[i - lag];
    if (v === null || prev === null || prev === 0) return null;
    return (v / prev - 1) * 100;
  });
}

/** Which row wins when more than one usage row lands on the same entity/month. */
function rowWeight(row: MartRow, metric: DashboardMetric): number {
  return metricSampleSize(row, metric) ?? 0;
}

/**
 * Turns raw mart rows into one gap-filled, transform-applied series per
 * requested entity.
 *
 * Two things this does deliberately:
 *  - Fills every calendar month between an entity's first and last row with
 *    an explicit point, `value: null` where no row exists. Skipping absent
 *    months instead would let Plot draw a straight line across the gap,
 *    inventing a trend that isn't in the data.
 *  - When `usage` isn't pinned to one value, a month can carry more than one
 *    usage row for the same entity. Averaging or summing different medians
 *    together would misrepresent both, so instead the row with the larger
 *    backing sample wins — the most-supported number for that month, not a
 *    blend of two unrelated ones.
 */
export function buildDashboardSeries(
  rows: MartRow[],
  entityIds: number[],
  entityNames: Map<number, string>,
  metric: DashboardMetric,
  transform: DashboardTransform,
): EntitySeries[] {
  return entityIds.map((id) => {
    const own = rows.filter((r) => r.entity_id === id);
    const name = entityNames.get(id) ?? own[0]?.name_en ?? `#${id}`;

    if (own.length === 0) {
      return {
        entityId: id,
        name,
        points: [],
        latestValue: null,
        latestMonth: null,
        yoyChangePct: null,
        sampleSize: null,
      };
    }

    const byMonth = new Map<string, MartRow>();
    for (const r of own) {
      const key = r.month.slice(0, 7);
      const existing = byMonth.get(key);
      if (!existing || rowWeight(r, metric) > rowWeight(existing, metric)) byMonth.set(key, r);
    }

    const months = [...byMonth.keys()].sort();
    const minKey = monthKey(`${months[0]}-01`);
    const maxKey = monthKey(`${months[months.length - 1]}-01`);

    const level: SeriesPoint[] = [];
    const rowsInOrder: (MartRow | null)[] = [];
    for (let k = minKey; k <= maxKey; k++) {
      const month = keyToMonth(k);
      const row = byMonth.get(month.slice(0, 7)) ?? null;
      rowsInOrder.push(row);
      level.push({ month, value: row ? metricValue(row, metric) : null });
    }

    const levelValues = level.map((p) => p.value);
    const yoySeries = computeChangeSeries(levelValues, 12);
    const transformedValues =
      transform === "level"
        ? levelValues
        : transform === "mom_pct"
          ? computeChangeSeries(levelValues, 1)
          : yoySeries;

    const points: SeriesPoint[] = level.map((p, i) => ({
      month: p.month,
      value: transformedValues[i],
    }));

    let latestIdx = -1;
    for (let i = levelValues.length - 1; i >= 0; i--) {
      if (levelValues[i] !== null) {
        latestIdx = i;
        break;
      }
    }

    const latestValue = latestIdx >= 0 ? levelValues[latestIdx] : null;
    const latestMonth = latestIdx >= 0 ? level[latestIdx].month : null;
    const yoyChangePct = latestIdx >= 0 ? yoySeries[latestIdx] : null;
    const latestRow = latestIdx >= 0 ? rowsInOrder[latestIdx] : null;
    const sampleSize = latestRow ? metricSampleSize(latestRow, metric) : null;

    return { entityId: id, name, points, latestValue, latestMonth, yoyChangePct, sampleSize };
  });
}

/**
 * Fixed categorical palette rather than CSS custom properties: Plot writes
 * `stroke` as an SVG presentation attribute, and colour must stay legible on
 * both the light and dark `--surface` backgrounds (theme.css) without a
 * re-render on theme change. Colour is keyed by entity id, not by array
 * position, so removing and re-adding an entity — or reordering the
 * selection — never reshuffles which colour belongs to which line.
 */
const PALETTE = [
  "#2f6fed",
  "#e0623d",
  "#2ea87a",
  "#a85d14",
  "#7b5ec7",
  "#c2377b",
  "#1d8fa5",
  "#8a8f00",
];

export function colorForEntity(entityId: number): string {
  const idx = ((entityId % PALETTE.length) + PALETTE.length) % PALETTE.length;
  return PALETTE[idx];
}
